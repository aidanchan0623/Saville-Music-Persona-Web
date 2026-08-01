from __future__ import annotations

import json
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.main import app
from app.database.repository import JsonRepository
from app.services.takeout_import_jobs import (
    TakeoutImportAlreadyRunning,
    TakeoutImportCoordinator,
    TakeoutImportTimedOut,
)
from app.services.duration_enrichment_jobs import DurationEnrichmentCoordinator
from app.services.takeout_service import TakeoutParseError, parse_takeout_file


def json_history(count: int = 3) -> list[dict[str, object]]:
    # Keep endpoint fixtures inside the real current-month API window so this
    # test remains valid across calendar boundaries.
    started_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    return [
        {
            "header": "YouTube Music",
            "title": "Watched Synthetic Song",
            "titleUrl": "https://www.youtube.com/watch?v=synthetic1",
            "subtitles": [{"name": "Synthetic Artist"}],
            "time": (started_at + timedelta(seconds=index)).isoformat().replace("+00:00", "Z"),
            "products": ["YouTube"],
        }
        for index in range(count)
    ]


def html_history() -> str:
    return """
    <div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">
      Watched <a href="https://www.youtube.com/watch?v=synthetic1">Synthetic Song</a><br>
      <a href="https://www.youtube.com/channel/example">Synthetic Artist - Topic</a><br>
      Jul 10, 2026, 10:32:18 PM GMT+08:00<br>
    </div>
    """


def write_format(tmp_path: Path, file_type: str, count: int = 3) -> Path:
    if file_type == "json":
        path = tmp_path / "watch-history.json"
        path.write_text(json.dumps(json_history(count)), encoding="utf-8")
        return path
    if file_type == "html":
        path = tmp_path / "watch-history.html"
        path.write_text(html_history(), encoding="utf-8")
        return path
    path = tmp_path / "takeout.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Takeout/YouTube and YouTube Music/history/watch-history.json", json.dumps(json_history(count)))
        archive.writestr(
            "Takeout/YouTube and YouTube Music/music-library-songs.csv",
            "Video ID,Song Title,Album Title,Artist Name 1\nsynthetic1,Synthetic Song,Synthetic Album,Synthetic Artist\n",
        )
    return path


@pytest.mark.parametrize("file_type", ["json", "html", "zip"])
def test_supported_takeout_formats_rebuild_a_usable_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_type: str) -> None:
    repository = JsonRepository(tmp_path / f"{file_type}.db")
    coordinator = TakeoutImportCoordinator(repository, timeout_seconds=30)
    monkeypatch.setattr(routes, "repo", repository)
    path = write_format(tmp_path, file_type)
    job_id = f"successful-{file_type}"
    coordinator.stage(job_id, "queued", "queued")

    routes.process_takeout_import(job_id, path, coordinator, time.monotonic() + 30)

    job = coordinator.get(job_id)
    assert job and job["status"] == "complete"
    assert repository.load_json("normalised")["metadata"]["play_count"] >= 1
    assert repository.load_json("analysis")["top_tracks"][0]["title"] == "Synthetic Song"


def test_large_valid_takeout_file_keeps_all_events(tmp_path: Path) -> None:
    path = write_format(tmp_path, "json", count=5000)
    result = parse_takeout_file(path)
    assert result.raw_event_count == 5000
    assert len(result.entries) == 5000


def test_new_takeout_replaces_previous_profile_without_cross_user_contamination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = JsonRepository(tmp_path / "merged-takeouts.db")
    coordinator = TakeoutImportCoordinator(repository, timeout_seconds=30)
    monkeypatch.setattr(routes, "repo", repository)
    monkeypatch.setattr(routes.ytmusic, "enrich_artist_image_cache", lambda *_args, **_kwargs: {"seeded": 0, "attempted": 0, "repaired": 0, "added": 0})
    monkeypatch.setattr(routes.ytmusic, "enrich_album_image_cache", lambda *_args, **_kwargs: {"seeded": 0, "attempted": 0, "added": 0})

    first = write_format(tmp_path, "json", count=3)
    coordinator.stage("first", "queued", "queued")
    routes.process_takeout_import("first", first, coordinator, time.monotonic() + 30)

    second = tmp_path / "watch-history-second.json"
    replacement = json_history(1)
    replacement[0]["title"] = "Watched Replacement Song"
    replacement[0]["titleUrl"] = "https://www.youtube.com/watch?v=replacement1"
    replacement[0]["subtitles"] = [{"name": "Replacement Artist"}]
    second.write_text(json.dumps(replacement), encoding="utf-8")
    coordinator.stage("second", "queued", "queued")
    routes.process_takeout_import("second", second, coordinator, time.monotonic() + 30)

    history = repository.load_json("takeout_history")
    job = coordinator.get("second")
    assert len(history) == 1
    assert repository.load_json("normalised")["metadata"]["play_count"] == 1
    assert repository.load_json("analysis")["top_tracks"][0]["title"] == "Replacement Song"
    assert job["totalImportedCount"] == 1
    assert job["duplicateCount"] == 0


def test_malformed_parser_input_has_a_safe_error(tmp_path: Path) -> None:
    path = tmp_path / "watch-history.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(TakeoutParseError, match="invalid or truncated"):
        parse_takeout_file(path)


def test_archive_entry_size_is_bounded(tmp_path: Path) -> None:
    path = write_format(tmp_path, "zip")
    with pytest.raises(TakeoutParseError, match="allowed size"):
        parse_takeout_file(path, max_archive_entry_bytes=10)


def test_analytics_exception_preserves_previous_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = JsonRepository(tmp_path / "rollback.db")
    previous = {"metadata": {"play_count": 9}, "tracks": [{"title": "Previous"}], "play_events": [{}]}
    repository.save_json("normalised", previous)
    repository.save_json("analysis", {"top_tracks": [{"title": "Previous"}], "coverage": {}})
    coordinator = TakeoutImportCoordinator(repository, timeout_seconds=30)
    monkeypatch.setattr(routes, "repo", repository)
    monkeypatch.setattr(routes, "build_analysis", lambda _normalised: (_ for _ in ()).throw(RuntimeError("boom")))
    path = write_format(tmp_path, "json")
    coordinator.stage("rollback", "queued", "queued")

    routes.process_takeout_import("rollback", path, coordinator, time.monotonic() + 30)

    assert coordinator.get("rollback")["errorCode"] == "takeout_analytics_rebuild_failed"
    assert repository.load_json("normalised") == previous
    assert repository.load_json("analysis")["top_tracks"][0]["title"] == "Previous"


def test_timeout_marks_background_job_failed(tmp_path: Path) -> None:
    repository = JsonRepository(tmp_path / "timeout.db")
    coordinator = TakeoutImportCoordinator(repository, timeout_seconds=0)
    path = write_format(tmp_path, "json")
    job_id = coordinator.reserve(".json")
    coordinator.queue(job_id, path, path.stat().st_size, routes.process_takeout_import)

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = coordinator.get(job_id)
        if job and job["status"] == "failed":
            break
        time.sleep(0.01)
    assert job and job["errorCode"] == "import_timeout"


def test_duplicate_import_reservation_is_rejected(tmp_path: Path) -> None:
    coordinator = TakeoutImportCoordinator(JsonRepository(tmp_path / "duplicate.db"), timeout_seconds=30)
    coordinator.reserve(".json")
    with pytest.raises(TakeoutImportAlreadyRunning):
        coordinator.reserve(".zip")


def test_backend_restart_marks_incomplete_job_failed(tmp_path: Path) -> None:
    repository = JsonRepository(tmp_path / "restart.db")
    repository.save_json(
        "takeout_import_job:old-job",
        {"jobId": "old-job", "status": "rebuilding", "progress": 65, "message": "working", "errorCode": None},
    )
    TakeoutImportCoordinator(repository, timeout_seconds=30)
    recovered = repository.load_json("takeout_import_job:old-job")
    assert recovered["status"] == "failed"
    assert recovered["errorCode"] == "backend_restarted"


def test_duration_enrichment_rebuilds_cached_profile_without_reimport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = JsonRepository(tmp_path / "duration.db")
    normalised = {
        "tracks": [{"track_id": "video:track", "video_id": "track", "title": "Track", "artists": ["Artist"], "primary_artist": "Artist", "duration_seconds": None}],
        "play_events": [{"track_id": "video:track", "video_id": "track", "title": "Track", "artists": ["Artist"], "primary_artist": "Artist", "played_at": "2026-07-10T14:00:00+00:00"}],
        "excluded_play_events": [],
        "metadata": {"track_count": 1, "play_count": 1},
        "coverage": {},
    }
    repository.save_json("normalised", normalised)
    repository.save_json("analysis", {"top_tracks": [{"title": "Old"}], "coverage": {}})
    monkeypatch.setattr(routes, "repo", repository)
    monkeypatch.setattr(
        routes.ytmusic,
        "enrich_duration_cache",
        lambda _normalised, cache, _limit: cache.update({"track": {"duration_seconds": 200, "duration_source": "test", "duration_confidence": "high"}}) or {"attempted": 1, "added": 1, "failed": 0, "api_batches": 0, "fallback_attempted": 0},
    )
    coordinator = DurationEnrichmentCoordinator(repository, timeout_seconds=30)

    routes.process_duration_enrichment(coordinator, time.monotonic() + 30)

    assert coordinator.status()["status"] == "complete"
    assert repository.load_json("normalised")["play_events"][0]["duration_seconds"] == 200
    assert "coverage" in repository.load_json("analysis")


def test_duration_enrichment_queues_the_next_safe_batch(tmp_path: Path) -> None:
    coordinator = DurationEnrichmentCoordinator(JsonRepository(tmp_path / "continuation.db"), timeout_seconds=30)
    completed = threading.Event()
    calls: list[int] = []

    def processor(active: DurationEnrichmentCoordinator, _deadline: float) -> None:
        calls.append(len(calls) + 1)
        active.stage("complete", "done", continueQueued=len(calls) == 1)
        if len(calls) == 2:
            completed.set()

    coordinator.start(processor)

    assert completed.wait(timeout=2)
    assert calls == [1, 2]


def test_timeout_check_raises_safe_timeout(tmp_path: Path) -> None:
    coordinator = TakeoutImportCoordinator(JsonRepository(tmp_path / "check.db"), timeout_seconds=30)
    with pytest.raises(TakeoutImportTimedOut):
        coordinator.check_timeout(time.monotonic() - 1)


def test_upload_endpoint_queues_job_and_new_profile_is_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = JsonRepository(tmp_path / "endpoint.db")
    coordinator = TakeoutImportCoordinator(repository, timeout_seconds=30)
    monkeypatch.setattr(routes, "repo", repository)
    monkeypatch.setattr(routes, "takeout_imports", coordinator)
    monkeypatch.setattr(routes.settings, "private_dir", tmp_path / "private")
    client = TestClient(app)

    response = client.post(
        "/api/data/import-takeout",
        files={"file": ("watch-history.json", json.dumps(json_history()).encode(), "application/json")},
    )
    assert response.status_code == 202
    job_id = response.json()["jobId"]
    job = wait_for_job(client, job_id)
    assert job["status"] == "complete"
    assert job["playCount"] == 3

    top = client.get("/api/analysis/top-tracks")
    assert top.status_code == 200
    assert top.json()[0]["title"] == "Synthetic Song"


def test_malformed_upload_job_returns_structured_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = JsonRepository(tmp_path / "malformed-endpoint.db")
    coordinator = TakeoutImportCoordinator(repository, timeout_seconds=30)
    monkeypatch.setattr(routes, "repo", repository)
    monkeypatch.setattr(routes, "takeout_imports", coordinator)
    monkeypatch.setattr(routes.settings, "private_dir", tmp_path / "private")
    client = TestClient(app)

    response = client.post(
        "/api/data/import-takeout",
        files={"file": ("watch-history.json", b"{broken", "application/json")},
    )
    assert response.status_code == 202
    job = wait_for_job(client, response.json()["jobId"])
    assert job["status"] == "failed"
    assert job["errorCode"] == "takeout_parse_failed"
    assert "invalid or truncated" in job["message"]


def test_oversized_upload_is_rejected_before_queueing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = JsonRepository(tmp_path / "oversized.db")
    coordinator = TakeoutImportCoordinator(repository, timeout_seconds=30)
    monkeypatch.setattr(routes, "repo", repository)
    monkeypatch.setattr(routes, "takeout_imports", coordinator)
    monkeypatch.setattr(routes.settings, "private_dir", tmp_path / "private")
    monkeypatch.setattr(routes.settings, "takeout_max_upload_bytes", 10)
    client = TestClient(app)

    response = client.post(
        "/api/data/import-takeout",
        files={"file": ("watch-history.json", b"[12345678901]", "application/json")},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "takeout_upload_too_large"


def wait_for_job(client: TestClient, job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/api/data/import-takeout/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in {"complete", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError("Takeout import job did not finish")
