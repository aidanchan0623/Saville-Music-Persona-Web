from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.database.repository import JsonRepository
from app.session import current_session_namespace, is_shared_cache_key, session_scope


def test_cached_json_reuses_parsed_value_until_version_changes(tmp_path: Path) -> None:
    repository = JsonRepository(tmp_path / "cache.db")
    repository.save_json("normalised", {"tracks": ["first"]})

    first = repository.load_json_cached("normalised")
    second = repository.load_json_cached("normalised")

    assert first is second

    with sqlite3.connect(repository.db_path) as conn:
        conn.execute(
            "UPDATE json_cache SET value = ?, updated_at = ? WHERE key = ?",
            (json.dumps({"tracks": ["external"]}), "2099-01-01T00:00:00+00:00", "normalised"),
        )

    refreshed = repository.load_json_cached("normalised")
    assert refreshed == {"tracks": ["external"]}
    assert refreshed is not first


def test_repository_uses_wal_and_reports_writable_storage(tmp_path: Path) -> None:
    repository = JsonRepository(tmp_path / "hosted.db")
    assert repository.healthcheck() is True
    with repository.connect() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].casefold() == "wal"


def test_cached_json_tracks_batch_writes_and_deletes(tmp_path: Path) -> None:
    repository = JsonRepository(tmp_path / "batch.db")
    repository.save_json_batch({"normalised": {"version": 1}, "analysis": {"version": 1}})

    assert repository.load_json_cached("normalised") == {"version": 1}
    repository.save_json_batch({"normalised": {"version": 2}}, delete_keys=["analysis"])

    assert repository.load_json_cached("normalised") == {"version": 2}
    assert repository.load_json_cached("analysis") is None


def test_anonymous_namespaces_isolate_profiles_but_share_music_metadata(tmp_path: Path) -> None:
    repository = JsonRepository(
        tmp_path / "anonymous.db",
        namespace_resolver=current_session_namespace,
        shared_key_predicate=is_shared_cache_key,
    )
    first_session = "a" * 64
    second_session = "b" * 64

    with session_scope(first_session):
        repository.save_json("normalised", {"owner": "first"})
        repository.save_json("genre_metadata_cache", {"Artist": ["Mandopop"]})
        repository.save_json("takeout_import_job:first", {"status": "complete"})

    with session_scope(second_session):
        assert repository.load_json("normalised") is None
        assert repository.load_json("genre_metadata_cache") == {"Artist": ["Mandopop"]}
        assert repository.load_json_prefix("takeout_import_job:") == {}
        repository.save_json("normalised", {"owner": "second"})

    with session_scope(first_session):
        assert repository.load_json("normalised") == {"owner": "first"}
        assert repository.load_json_prefix("takeout_import_job:")["takeout_import_job:first"]["status"] == "complete"
