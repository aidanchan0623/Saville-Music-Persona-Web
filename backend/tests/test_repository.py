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


def test_large_json_is_compressed_at_rest_and_round_trips(tmp_path: Path) -> None:
    repository = JsonRepository(tmp_path / "compressed.db")
    payload = {"events": [{"title": "A long listening event", "artist": "Artist"}] * 20_000}

    repository.save_json_batch({"normalised": payload})

    with sqlite3.connect(repository.db_path) as conn:
        storage_type, stored_bytes = conn.execute(
            "SELECT typeof(value), length(value) FROM json_cache WHERE key = 'normalised'"
        ).fetchone()
    assert storage_type == "blob"
    assert stored_bytes < 100_000
    assert repository.load_json("normalised") == payload


def test_evict_cached_releases_only_the_selected_parsed_value(tmp_path: Path) -> None:
    repository = JsonRepository(tmp_path / "evict.db")
    repository.save_json_batch({"normalised": {"version": 1}, "analysis": {"version": 1}})
    normalised = repository.load_json_cached("normalised")
    analysis = repository.load_json_cached("analysis")

    repository.evict_cached(["normalised"])

    assert repository.load_json_cached("normalised") == normalised
    assert repository.load_json_cached("normalised") is not normalised
    assert repository.load_json_cached("analysis") is analysis


def test_runtime_metrics_are_atomic_and_session_summary_is_aggregate(tmp_path: Path) -> None:
    repository = JsonRepository(tmp_path / "metrics.db")
    repository.increment_runtime_metric("reports.accepted")
    repository.increment_runtime_metric("reports.accepted", 2)
    assert repository.runtime_metrics_snapshot() == {"reports.accepted": 3}
    assert repository.anonymous_session_summary() == {"active": 0, "expired": 0}
    assert repository.database_size_bytes() > 0
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


def test_shared_metadata_merge_preserves_parallel_cache_entries(tmp_path: Path) -> None:
    repository = JsonRepository(tmp_path / "shared-merge.db")
    first_stale_snapshot = {"items": {"artist-a": {"url": "a.jpg"}}, "aliases": {"a": "artist-a"}}
    second_stale_snapshot = {"items": {"artist-b": {"url": "b.jpg"}}, "aliases": {"b": "artist-b"}}

    repository.merge_json_dict("artist_image_cache_v2", first_stale_snapshot)
    merged = repository.merge_json_dict("artist_image_cache_v2", second_stale_snapshot)

    assert merged["items"] == {
        "artist-a": {"url": "a.jpg"},
        "artist-b": {"url": "b.jpg"},
    }
    assert merged["aliases"] == {"a": "artist-a", "b": "artist-b"}
