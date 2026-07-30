from __future__ import annotations

import time
from pathlib import Path

from app.database.repository import JsonRepository
from app.services.report_generation_jobs import ReportGenerationCoordinator
from app.session import current_session_namespace, is_shared_cache_key, session_scope


def repository(path: Path) -> JsonRepository:
    return JsonRepository(path, namespace_resolver=current_session_namespace, shared_key_predicate=is_shared_cache_key)


def test_report_worker_keeps_job_and_result_inside_the_browser_session(tmp_path: Path) -> None:
    coordinator = ReportGenerationCoordinator(repository(tmp_path / "reports.db"), timeout_seconds=5)
    session_id = "a" * 64

    def processor(job_id: str, active: ReportGenerationCoordinator, _deadline: float) -> None:
        active.stage(job_id, "writing", "writing")
        active.stage(job_id, "complete", "done", provider="deterministic", report={"schemaVersion": 8})

    with session_scope(session_id):
        queued = coordinator.start("deterministic", processor)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = coordinator.get(queued["jobId"])
            if job and job["status"] == "complete":
                break
            time.sleep(0.01)
        assert job and job["report"] == {"schemaVersion": 8}

    with session_scope("b" * 64):
        assert coordinator.get(queued["jobId"]) is None


def test_stale_report_job_is_failed_after_a_backend_restart(tmp_path: Path) -> None:
    repo = repository(tmp_path / "restart.db")
    coordinator = ReportGenerationCoordinator(repo, timeout_seconds=5)
    job_id = "stale-job"
    with session_scope("c" * 64):
        repo.save_json(
            coordinator.key(job_id),
            {
                "jobId": job_id,
                "status": "writing",
                "progress": 45,
                "message": "writing",
                "errorCode": None,
                "provider": "openai-compatible",
                "createdAt": "2026-07-30T00:00:00+00:00",
                "updatedAt": "2026-07-30T00:00:00+00:00",
                "finishedAt": None,
                "report": None,
            },
        )
        recovered = coordinator.get(job_id)

    assert recovered and recovered["status"] == "failed"
    assert recovered["errorCode"] == "backend_restarted"
