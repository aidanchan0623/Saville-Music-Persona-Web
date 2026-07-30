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


JOB_PREFIX = "report_generation_job:"
ACTIVE_STATUSES = {"queued", "building", "writing", "saving"}
STAGE_PROGRESS = {"queued": 0, "building": 20, "writing": 45, "saving": 85, "complete": 100, "failed": 100}
Processor = Callable[[str, "ReportGenerationCoordinator", float], None]


class ReportGenerationAlreadyRunning(RuntimeError):
    pass


class ReportGenerationCapacityReached(RuntimeError):
    pass


class ReportGenerationCoordinator:
    def __init__(self, repo: JsonRepository, timeout_seconds: int, maximum_concurrent: int = 2) -> None:
        self.repo = repo
        self.timeout_seconds = timeout_seconds
        self.maximum_concurrent = max(1, maximum_concurrent)
        self._lock = threading.Lock()
        self._active_scopes: dict[str, str] = {}
        self._logger = logging.getLogger("saville.report_generation")

    def start(self, provider: str, processor: Processor) -> dict[str, Any]:
        scope = current_session_namespace() or "local"
        with self._lock:
            if scope in self._active_scopes:
                raise ReportGenerationAlreadyRunning("A persona report is already being generated for this session.")
            if len(self._active_scopes) >= self.maximum_concurrent:
                raise ReportGenerationCapacityReached("The hosted report writer is busy. Try again shortly.")
            job_id = uuid.uuid4().hex
            self._active_scopes[scope] = job_id
        job = {
            "jobId": job_id,
            "status": "queued",
            "progress": 0,
            "message": "Persona report generation is queued.",
            "errorCode": None,
            "provider": provider,
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
            "finishedAt": None,
            "report": None,
        }
        self.repo.save_json(self.key(job_id), job)
        context = copy_context()
        threading.Thread(
            target=context.run,
            args=(self._run, job_id, processor),
            name=f"report-generation-{job_id[:8]}",
            daemon=True,
        ).start()
        return job

    def _run(self, job_id: str, processor: Processor) -> None:
        try:
            processor(job_id, self, time.monotonic() + self.timeout_seconds)
        except TimeoutError:
            self.fail(job_id, "Report generation timed out safely. Retry to use the deterministic writer.", "report_generation_timeout")
        except Exception:  # noqa: BLE001
            self._logger.exception("report generation failed")
            self.fail(job_id, "Report generation failed safely. Your listening profile was preserved.", "report_generation_failed")
        finally:
            scope = current_session_namespace() or "local"
            with self._lock:
                if self._active_scopes.get(scope) == job_id:
                    self._active_scopes.pop(scope, None)

    def stage(self, job_id: str, status: str, message: str, **fields: Any) -> dict[str, Any]:
        job = self.get(job_id) or {"jobId": job_id, "createdAt": utc_now()}
        job.update(
            {
                "status": status,
                "progress": STAGE_PROGRESS[status],
                "message": message,
                "errorCode": None,
                "updatedAt": utc_now(),
                **fields,
            }
        )
        if status in {"complete", "failed"}:
            job["finishedAt"] = utc_now()
        self.repo.save_json(self.key(job_id), job)
        return job

    def fail(self, job_id: str, message: str, error_code: str) -> dict[str, Any]:
        return self.stage(job_id, "failed", message, errorCode=error_code)

    def get(self, job_id: str) -> dict[str, Any] | None:
        value = self.repo.load_json(self.key(job_id))
        if not isinstance(value, dict):
            return None
        scope = current_session_namespace() or "local"
        if value.get("status") in ACTIVE_STATUSES:
            with self._lock:
                still_running = self._active_scopes.get(scope) == job_id
            if not still_running:
                value.update(
                    {
                        "status": "failed",
                        "progress": 100,
                        "message": "The backend restarted during report generation. Retry to use the available writer.",
                        "errorCode": "backend_restarted",
                        "updatedAt": utc_now(),
                        "finishedAt": utc_now(),
                    }
                )
                self.repo.save_json(self.key(job_id), value)
        return value

    @staticmethod
    def check_timeout(deadline: float) -> None:
        if time.monotonic() > deadline:
            raise TimeoutError

    @staticmethod
    def key(job_id: str) -> str:
        return f"{JOB_PREFIX}{job_id}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
