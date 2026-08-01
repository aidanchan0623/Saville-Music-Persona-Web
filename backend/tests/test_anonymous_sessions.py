from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.database.repository import JsonRepository
from app.main import app
from app.database.recording_catalog import RecordingCatalog
from app.services.session_cleanup import SessionCleanupService
from app.services.takeout_import_jobs import (
    ImportCapacity,
    TakeoutImportAlreadyRunning,
    TakeoutImportCapacityReached,
    TakeoutImportCoordinator,
)
from app.session import current_session_namespace, is_shared_cache_key, session_scope


def anonymous_repository(path: Path) -> JsonRepository:
    return JsonRepository(
        path,
        namespace_resolver=current_session_namespace,
        shared_key_predicate=is_shared_cache_key,
    )


def test_hosted_writer_status_and_budgets_do_not_expose_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = anonymous_repository(tmp_path / "writer-budget.db")
    monkeypatch.setattr(routes, "repo", repository)
    monkeypatch.setattr(routes.settings, "deployment_mode", "anonymous")
    monkeypatch.setattr(routes.settings, "hosted_llm_provider", "openai-compatible")
    monkeypatch.setattr(routes.settings, "hosted_llm_api_key", "never-return-this-key")
    monkeypatch.setattr(routes.settings, "hosted_llm_model", "bounded-writer")
    monkeypatch.setattr(routes.settings, "hosted_llm_requests_per_session_hour", 2)
    monkeypatch.setattr(routes.settings, "hosted_llm_requests_global_day", 1)

    status = routes.runtime_providers()
    assert status["reportWriter"]["configured"] is True
    assert "never-return-this-key" not in json.dumps(status)

    with session_scope("1" * 64):
        assert routes.consume_hosted_llm_budget() is None
    with session_scope("2" * 64):
        assert routes.consume_hosted_llm_budget() == "hosted_llm_global_budget_exhausted"


def test_anonymous_middleware_issues_isolated_http_only_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = anonymous_repository(tmp_path / "sessions.db")
    monkeypatch.setattr(routes, "repo", repository)
    monkeypatch.setattr(routes.settings, "deployment_mode", "anonymous")
    monkeypatch.setattr(routes.settings, "session_cookie_secure", False)

    first = TestClient(app)
    second = TestClient(app)
    first_status = first.get("/api/session")
    second_status = second.get("/api/session")

    assert first_status.status_code == 200
    assert first_status.json()["anonymous"] is True
    assert first_status.json()["accountConnectionsEnabled"] is False
    assert "httponly" in first_status.headers["set-cookie"].casefold()
    assert first.cookies.get(routes.settings.session_cookie_name) != second.cookies.get(routes.settings.session_cookie_name)
    assert first.post("/api/auth/setup").status_code == 403
    untrusted = first.post("/api/auth/setup", headers={"Origin": "https://untrusted.example"})
    assert untrusted.json()["code"] == "origin_not_allowed"
    assert untrusted.headers["x-content-type-options"] == "nosniff"
    monkeypatch.setattr(routes.settings, "serve_frontend", True)
    assert first.post("/api/auth/setup", headers={"Origin": "http://testserver"}).json()["detail"]["code"] == "account_connections_disabled"
    assert first.get("/api/spotify/login", follow_redirects=False).status_code == 403
    assert first.get("/api/auth/status").json()["auth_file_path"] == ""
    assert first.get("/api/session").json()["expiresAt"] == first_status.json()["expiresAt"]


def test_health_probes_do_not_create_visitor_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = anonymous_repository(tmp_path / "health.db")
    monkeypatch.setattr(routes, "repo", repository)
    monkeypatch.setattr(routes.settings, "deployment_mode", "anonymous")
    monkeypatch.setattr(routes.settings, "serve_frontend", False)
    client = TestClient(app)
    health = client.get("/api/health")
    ready = client.get("/api/ready")
    assert health.status_code == 200
    assert health.json()["version"] == "0.5.0"
    assert ready.status_code == 200
    assert ready.json()["workerTopology"] == "single-process"
    assert client.cookies.get(routes.settings.session_cookie_name) is None


def test_security_headers_and_private_operator_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = anonymous_repository(tmp_path / "operations.db")
    monkeypatch.setattr(routes, "repo", repository)
    monkeypatch.setattr(routes.settings, "deployment_mode", "anonymous")
    monkeypatch.setattr(routes.settings, "operations_token", "operator-secret")
    monkeypatch.setattr(routes.settings, "session_cookie_secure", False)
    client = TestClient(app)

    session = client.get("/api/session")
    assert session.headers["cache-control"] == "no-store"
    assert session.headers["x-content-type-options"] == "nosniff"
    assert session.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in session.headers["content-security-policy"]

    forbidden = TestClient(app).get("/api/ops/status")
    assert forbidden.status_code == 403
    assert forbidden.cookies.get(routes.settings.session_cookie_name) is None
    status = TestClient(app).get(
        "/api/ops/status",
        headers={"X-Saville-Ops-Token": "operator-secret"},
    )
    assert status.status_code == 200
    payload = status.json()
    assert payload["version"] == "0.5.0"
    assert payload["privacy"]["containsListeningHistory"] is False
    assert payload["privacy"]["containsSessionIdentifiers"] is False
    assert payload["counters"]["sessions.started"] == 1
    assert "operator-secret" not in json.dumps(payload)


def test_operator_status_is_not_discoverable_without_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes.settings, "operations_token", "")
    response = TestClient(app).get("/api/ops/status")
    assert response.status_code == 404
    assert response.cookies.get(routes.settings.session_cookie_name) is None


def test_import_worker_keeps_the_request_session_context(tmp_path: Path) -> None:
    repository = anonymous_repository(tmp_path / "worker.db")
    coordinator = TakeoutImportCoordinator(repository, timeout_seconds=10)
    upload = tmp_path / "upload.json"
    upload.write_text("[]", encoding="utf-8")
    session_id = "c" * 64

    def processor(job_id: str, _path: Path, active: TakeoutImportCoordinator, _deadline: float) -> None:
        repository.save_json("normalised", {"session": session_id})
        active.stage(job_id, "complete", "done")

    with session_scope(session_id):
        job_id = coordinator.reserve("json")
        coordinator.queue(job_id, upload, upload.stat().st_size, processor)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = coordinator.get(job_id)
            if job and job["status"] == "complete":
                break
            time.sleep(0.01)
        assert repository.load_json("normalised") == {"session": session_id}

    with session_scope("d" * 64):
        assert repository.load_json("normalised") is None


def test_import_reservations_are_independent_between_sessions(tmp_path: Path) -> None:
    coordinator = TakeoutImportCoordinator(anonymous_repository(tmp_path / "reservations.db"), timeout_seconds=10)
    with session_scope("e" * 64):
        first_job = coordinator.reserve("zip")
        with pytest.raises(TakeoutImportAlreadyRunning):
            coordinator.reserve("zip")
    with session_scope("f" * 64):
        second_job = coordinator.reserve("zip")
        coordinator.release_reservation(second_job)
    with session_scope("e" * 64):
        coordinator.release_reservation(first_job)


def test_shared_import_capacity_bounds_parallel_sessions(tmp_path: Path) -> None:
    capacity = ImportCapacity(1)
    repository = anonymous_repository(tmp_path / "capacity.db")
    takeout = TakeoutImportCoordinator(repository, timeout_seconds=10, capacity=capacity)
    spotify = TakeoutImportCoordinator(repository, timeout_seconds=10, source_label="Spotify", capacity=capacity)
    with session_scope("a" * 64):
        first_job = takeout.reserve("zip")
    with session_scope("b" * 64):
        with pytest.raises(TakeoutImportCapacityReached):
            spotify.reserve("zip")
    with session_scope("a" * 64):
        takeout.release_reservation(first_job)
    with session_scope("b" * 64):
        second_job = spotify.reserve("zip")
        spotify.release_reservation(second_job)


def test_rate_limit_is_atomic_and_scoped_per_session(tmp_path: Path) -> None:
    repository = anonymous_repository(tmp_path / "rate.db")
    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    with session_scope("1" * 64):
        assert repository.consume_rate_limit("usage:upload", limit=2, window_seconds=3600, now=now)[0]
        assert repository.consume_rate_limit("usage:upload", limit=2, window_seconds=3600, now=now)[0]
        assert not repository.consume_rate_limit("usage:upload", limit=2, window_seconds=3600, now=now)[0]
    with session_scope("2" * 64):
        assert repository.consume_rate_limit("usage:upload", limit=2, window_seconds=3600, now=now)[0]


def test_cleanup_removes_expired_profile_but_keeps_shared_metadata(tmp_path: Path) -> None:
    repository = anonymous_repository(tmp_path / "cleanup.db")
    session_id = "3" * 64
    namespace = f"session:{session_id}"
    with session_scope(session_id):
        repository.save_json(
            "session_meta",
            {"expiresAt": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()},
        )
        repository.save_json("normalised", {"private": True})
        repository.save_json("genre_metadata_cache", {"shared": True})
    catalog = RecordingCatalog(repository.db_path)
    with catalog.connect() as conn:
        conn.execute(
            "INSERT INTO listening_events(event_id, recording_id, profile_source, source, played_at, title, primary_artist, import_batch_id, linked_at) "
            "VALUES (?, NULL, ?, 'youtube', NULL, 'Song', 'Artist', NULL, ?)",
            ("event", f"{namespace}:youtube", datetime.now(timezone.utc).isoformat()),
        )
    result = SessionCleanupService(repository, interval_seconds=30, upload_ttl_hours=24).cleanup_if_due()
    assert result["sessions"] == 1
    assert result["cacheRows"] >= 2
    assert result["events"] == 1
    with session_scope(session_id):
        assert repository.load_json("normalised") is None
        assert repository.load_json("genre_metadata_cache") == {"shared": True}


def test_user_can_delete_anonymous_session_immediately(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = anonymous_repository(tmp_path / "delete.db")
    monkeypatch.setattr(routes, "repo", repository)
    monkeypatch.setattr(routes.settings, "deployment_mode", "anonymous")
    monkeypatch.setattr(routes.settings, "session_cookie_secure", False)
    client = TestClient(app)
    assert client.get("/api/session").status_code == 200
    with session_scope(client.cookies.get(routes.settings.session_cookie_name)):
        repository.save_json("normalised", {"private": True})
    response = client.delete("/api/session")
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert response.json()["cacheRowsDeleted"] >= 2
    assert client.cookies.get(routes.settings.session_cookie_name) is None
