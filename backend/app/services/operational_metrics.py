from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.database.repository import JsonRepository


logger = logging.getLogger("saville.operational_metrics")
PROCESS_STARTED_AT = datetime.now(timezone.utc)
PROCESS_STARTED_MONOTONIC = time.monotonic()


class OperationalMetricsService:
    """Privacy-preserving counters for operating the anonymous hosted service."""

    _allowed_metrics = {
        "api.server_errors",
        "cleanup.cache_rows_deleted",
        "cleanup.events_deleted",
        "cleanup.sessions_deleted",
        "cleanup.uploads_deleted",
        "imports.spotify.accepted",
        "imports.spotify.rejected",
        "imports.takeout.accepted",
        "imports.takeout.rejected",
        "reports.accepted",
        "reports.fallback",
        "reports.hosted_writer",
        "reports.rejected",
        "sessions.deleted",
        "sessions.started",
    }

    def __init__(self, repo: JsonRepository) -> None:
        self.repo = repo

    def record(self, name: str, amount: int = 1) -> None:
        if name not in self._allowed_metrics or amount <= 0:
            return
        try:
            self.repo.increment_runtime_metric(name, amount)
        except Exception:  # noqa: BLE001
            # Observability must never make a visitor request fail.
            logger.exception("Failed to record aggregate operational metric %s", name)

    def record_cleanup(self, totals: dict[str, int]) -> None:
        mapping = {
            "sessions": "cleanup.sessions_deleted",
            "cacheRows": "cleanup.cache_rows_deleted",
            "events": "cleanup.events_deleted",
            "uploads": "cleanup.uploads_deleted",
        }
        for source, metric in mapping.items():
            value = max(0, int(totals.get(source) or 0))
            if value:
                self.record(metric, value)

    def record_mutation(self, method: str, path: str, status_code: int) -> None:
        if status_code >= 500:
            self.record("api.server_errors")
        if method != "POST":
            return
        metric_prefix = {
            "/api/data/import-takeout": "imports.takeout",
            "/api/data/import-spotify-history": "imports.spotify",
            "/api/report/jobs": "reports",
        }.get(path)
        if metric_prefix:
            outcome = "accepted" if 200 <= status_code < 300 else "rejected"
            self.record(f"{metric_prefix}.{outcome}")

    def snapshot(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "startedAt": PROCESS_STARTED_AT.isoformat(),
            "uptimeSeconds": max(0, int(time.monotonic() - PROCESS_STARTED_MONOTONIC)),
            "sessions": self.repo.anonymous_session_summary(),
            "storage": {"databaseBytes": self.repo.database_size_bytes()},
            "counters": self.repo.runtime_metrics_snapshot(),
            "privacy": {
                "containsSessionIdentifiers": False,
                "containsListeningHistory": False,
                "containsNetworkIdentifiers": False,
            },
        }
