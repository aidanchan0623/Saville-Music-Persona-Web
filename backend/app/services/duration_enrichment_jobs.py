from __future__ import annotations

import logging
import threading
import time
import uuid
from contextvars import copy_context
from datetime import datetime, timezone
from typing import Any, Callable

from app.database.repository import JsonRepository
from app.session import current_session_namespace


JOB_KEY = "duration_enrichment_job"
ACTIVE_STATUSES = {"queued", "resolving", "rebuilding"}
TERMINAL_STATUSES = {"complete", "failed"}
STAGE_PROGRESS = {"queued": 0, "resolving": 15, "rebuilding": 85, "complete": 100, "failed": 100}
Processor = Callable[["DurationEnrichmentCoordinator", float], None]


class DurationEnrichmentAlreadyRunning(RuntimeError):
    pass


class DurationEnrichmentCoordinator:
    """One local metadata job at a time; it never replaces a usable profile until rebuilt."""

    def __init__(self, repo: JsonRepository, timeout_seconds: int) -> None:
        self.repo = repo
        self.timeout_seconds = timeout_seconds
        self._lock = threading.RLock()
        self._active_scopes: set[str] = set()
        self._logger = logging.getLogger("saville.duration_enrichment")
        self.recover_interrupted_job()

    def recover_interrupted_job(self) -> None:
        job = self._load_status()
        if not job or job.get("status") not in ACTIVE_STATUSES:
            return
        self._mark_interrupted(job)

    def start(self, processor: Processor) -> dict[str, Any]:
        scope = current_session_namespace() or "local"
        with self._lock:
            if scope in self._active_scopes:
                raise DurationEnrichmentAlreadyRunning("Track duration enrichment is already running.")
            previous = self.status()
            if previous and previous.get("status") in ACTIVE_STATUSES:
                raise DurationEnrichmentAlreadyRunning("Track duration enrichment is already running.")
            self._active_scopes.add(scope)
        job = {
            "jobId": uuid.uuid4().hex,
            "status": "queued",
            "progress": 0,
            "message": "Track durations are queued for local enrichment.",
            "errorCode": None,
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
            "finishedAt": None,
            "attempted": 0,
            "added": 0,
        }
        self.repo.save_json(JOB_KEY, job)
        context = copy_context()
        thread = threading.Thread(target=context.run, args=(self._run, processor), name="duration-enrichment", daemon=True)
        thread.start()
        return job

    def _run(self, processor: Processor) -> None:
        deadline = time.monotonic() + self.timeout_seconds
        continue_with_next_batch = False
        try:
            processor(self, deadline)
        except TimeoutError:
            self.fail("Duration enrichment timed out. Your current listening profile was preserved.", "duration_enrichment_timeout")
        except Exception:  # noqa: BLE001
            self._logger.exception("duration enrichment failed")
            self.fail("Duration enrichment failed safely. Your current listening profile was preserved.", "duration_enrichment_failed")
        finally:
            scope = current_session_namespace() or "local"
            with self._lock:
                self._active_scopes.discard(scope)
            completed = self.status()
            continue_with_next_batch = bool(completed and completed.get("status") == "complete" and completed.get("continueQueued"))
            if continue_with_next_batch:
                self.start(processor)

    def stage(self, status: str, message: str, **fields: Any) -> dict[str, Any]:
        # Do not run restart recovery while the worker itself advances stages.
        # Direct processor tests also call this method without start().
        job = self._load_status() or {"jobId": uuid.uuid4().hex, "createdAt": utc_now()}
        job.update({"status": status, "progress": STAGE_PROGRESS[status], "message": message, "errorCode": None, "updatedAt": utc_now(), **fields})
        if status in TERMINAL_STATUSES:
            job["finishedAt"] = utc_now()
        self.repo.save_json(JOB_KEY, job)
        return job

    def fail(self, message: str, error_code: str) -> dict[str, Any]:
        return self.stage("failed", message, errorCode=error_code)

    def update_progress(self, progress: int, message: str, **fields: Any) -> dict[str, Any]:
        """Checkpoint live resolving progress without changing job stage."""
        job = self._load_status() or {"jobId": uuid.uuid4().hex, "createdAt": utc_now()}
        job.update(
            {
                "status": "resolving",
                "progress": max(STAGE_PROGRESS["resolving"], min(STAGE_PROGRESS["rebuilding"] - 1, int(progress))),
                "message": message,
                "errorCode": None,
                "updatedAt": utc_now(),
                **fields,
            }
        )
        self.repo.save_json(JOB_KEY, job)
        return job

    def status(self) -> dict[str, Any] | None:
        """Return this visitor's job, recovering work lost by a process restart.

        Anonymous jobs are namespaced. Module-start recovery can only see the
        unscoped/local namespace, so recovery must also happen lazily when the
        original visitor next polls or starts a job.
        """
        with self._lock:
            value = self._load_status()
            scope = current_session_namespace() or "local"
            if value and value.get("status") in ACTIVE_STATUSES and scope not in self._active_scopes:
                return self._mark_interrupted(value)
            return value

    def _load_status(self) -> dict[str, Any] | None:
        value = self.repo.load_json(JOB_KEY)
        return value if isinstance(value, dict) else None

    def _mark_interrupted(self, job: dict[str, Any]) -> dict[str, Any]:
        job.update(
            {
                "status": "failed",
                "progress": 100,
                "message": "The backend restarted while resolving track durations. Saved metadata was kept; enrichment will resume safely.",
                "errorCode": "backend_restarted",
                "updatedAt": utc_now(),
                "finishedAt": utc_now(),
            }
        )
        self.repo.save_json(JOB_KEY, job)
        return job

    @staticmethod
    def check_timeout(deadline: float) -> None:
        if time.monotonic() > deadline:
            raise TimeoutError


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
