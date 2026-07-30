from __future__ import annotations

import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app.database.recording_catalog import RecordingCatalog
from app.database.repository import JsonRepository


class SessionCleanupService:
    """Bound anonymous storage without collecting account or identity data."""

    def __init__(self, repo: JsonRepository, *, interval_seconds: int, upload_ttl_hours: int) -> None:
        self.repo = repo
        self.interval_seconds = interval_seconds
        self.upload_ttl_seconds = upload_ttl_hours * 60 * 60
        self._lock = threading.Lock()
        self._last_run = 0.0

    def session_expired(self, namespace: str, now: datetime | None = None) -> bool:
        payload = self.repo.load_json_from_namespace(namespace, "session_meta")
        if not isinstance(payload, dict) or not payload.get("expiresAt"):
            return False
        try:
            expires_at = datetime.fromisoformat(str(payload["expiresAt"]).replace("Z", "+00:00"))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        except ValueError:
            return True
        return expires_at <= (now or datetime.now(timezone.utc))

    def cleanup_if_due(self, *, exclude: set[str] | None = None) -> dict[str, int]:
        now_monotonic = time.monotonic()
        if now_monotonic - self._last_run < self.interval_seconds:
            return {"sessions": 0, "cacheRows": 0, "events": 0, "uploads": 0}
        if not self._lock.acquire(blocking=False):
            return {"sessions": 0, "cacheRows": 0, "events": 0, "uploads": 0}
        try:
            if time.monotonic() - self._last_run < self.interval_seconds:
                return {"sessions": 0, "cacheRows": 0, "events": 0, "uploads": 0}
            self._last_run = time.monotonic()
            excluded = exclude or set()
            totals = {"sessions": 0, "cacheRows": 0, "events": 0, "uploads": 0}
            for namespace in self.repo.expired_session_namespaces():
                if namespace in excluded:
                    continue
                result = self.purge_namespace(namespace)
                totals["sessions"] += 1
                totals["cacheRows"] += result["cacheRows"]
                totals["events"] += result["events"]
            totals["uploads"] = self._delete_stale_uploads()
            return totals
        finally:
            self._lock.release()

    def purge_namespace(self, namespace: str) -> dict[str, int]:
        events = RecordingCatalog(self.repo.db_path).delete_profile_source_prefix(f"{namespace}:")
        cache_rows = self.repo.delete_namespace(namespace)
        return {"cacheRows": cache_rows, "events": events}

    def _delete_stale_uploads(self) -> int:
        root = Path(tempfile.gettempdir()) / "saville-music-persona"
        cutoff = time.time() - self.upload_ttl_seconds
        deleted = 0
        for directory_name in ("takeout-imports", "spotify-history-imports"):
            directory = root / directory_name
            if not directory.exists():
                continue
            for path in directory.iterdir():
                try:
                    if path.is_file() and path.stat().st_mtime <= cutoff:
                        path.unlink(missing_ok=True)
                        deleted += 1
                except OSError:
                    # Another worker may still own the file; retry next pass.
                    continue
        return deleted
