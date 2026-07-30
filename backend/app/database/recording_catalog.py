from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.data.genre_taxonomy import TAXONOMY_VERSION, normalise_external_genres
from app.analysis.track_metadata import display_recording_title


RECORDING_CATALOG_SCHEMA_VERSION = 1
AUTO_APPLY_THRESHOLD = 0.70
VERSION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("live", r"\blive\b|live at|live from"),
    ("remix", r"\bremix\b|\bmix\)"),
    ("remaster", r"\bremaster(?:ed)?\b"),
    ("slowed", r"\bslowed\b|slowed\s*\+\s*reverb"),
    ("sped_up", r"\bsped[ -]?up\b|\bspeed up\b"),
    ("instrumental", r"\binstrumental\b|\bkaraoke\b"),
    ("acoustic", r"\bacoustic\b|\bunplugged\b"),
    ("radio_edit", r"\bradio edit\b"),
    ("cover", r"\bcover\b"),
)
PRESENTATION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("music_video", r"official music video|official video|\bmv\b"),
    ("lyric_video", r"lyric video|lyrics video|official lyric"),
    ("official_audio", r"official audio|audio only"),
    ("visualizer", r"official visuali[sz]er|visuali[sz]er"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalise_recording_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("’", "'").replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def modifiers_for(title: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    value = normalise_recording_text(title)
    versions = tuple(name for name, pattern in VERSION_PATTERNS if re.search(pattern, value, re.I))
    presentations = tuple(name for name, pattern in PRESENTATION_PATTERNS if re.search(pattern, value, re.I))
    return versions, presentations


def base_title(value: Any) -> str:
    title = normalise_recording_text(value)
    for _, pattern in (*VERSION_PATTERNS, *PRESENTATION_PATTERNS):
        title = re.sub(pattern, " ", title, flags=re.I)
    title = re.sub(r"[\[\](){}|]", " ", title)
    title = re.sub(r"\bofficial\b|\bvideo\b|\baudio\b|\blyrics?\b|\bhd\b|\b4k\b", " ", title)
    title = re.sub(r"[^\w\u0080-\uffff']+", " ", title)
    return " ".join(title.split())


def identifier_candidates(track: dict[str, Any]) -> list[tuple[str, str, float]]:
    result: list[tuple[str, str, float]] = []
    video_id = str(track.get("video_id") or "").strip()
    source_track_id = str(track.get("source_track_id") or "").strip()
    if video_id:
        result.append(("youtube_video_id", video_id, 1.0))
    if source_track_id.startswith("spotify:track:"):
        result.append(("spotify_track_id", source_track_id.removeprefix("spotify:track:"), 0.99))
    for key, namespace in (("isrc", "isrc"), ("musicbrainz_recording_id", "musicbrainz_recording_id")):
        value = str(track.get(key) or "").strip()
        if value:
            result.append((namespace, value, 0.99))
    provider_ids = track.get("recording_identifiers")
    if isinstance(provider_ids, dict):
        for namespace, value in provider_ids.items():
            if str(namespace).strip() and str(value).strip():
                result.append((str(namespace).strip(), str(value).strip(), 0.98))
    return result


@dataclass(frozen=True)
class RecordingResolution:
    recording_id: str
    identity_confidence: float
    match_method: str


class RecordingCatalog:
    """Durable recording metadata, independent from listening-event dedupe."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialise()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialise(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS recordings (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    primary_artist TEXT NOT NULL,
                    album TEXT,
                    duration_seconds INTEGER,
                    version_modifiers TEXT NOT NULL DEFAULT '[]',
                    presentation_modifiers TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recording_identifiers (
                    namespace TEXT NOT NULL,
                    value TEXT NOT NULL,
                    recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
                    identity_confidence REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(namespace, value)
                );
                CREATE TABLE IF NOT EXISTS recording_aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
                    normalised_title TEXT NOT NULL,
                    normalised_artist TEXT NOT NULL,
                    normalised_album TEXT NOT NULL DEFAULT '',
                    duration_seconds INTEGER,
                    version_signature TEXT NOT NULL DEFAULT '',
                    identity_confidence REAL NOT NULL,
                    match_method TEXT NOT NULL,
                    UNIQUE(recording_id, normalised_title, normalised_artist, normalised_album, duration_seconds, version_signature)
                );
                CREATE INDEX IF NOT EXISTS recording_alias_lookup
                    ON recording_aliases(normalised_title, normalised_artist, version_signature);
                CREATE TABLE IF NOT EXISTS listening_events (
                    event_id TEXT PRIMARY KEY,
                    recording_id TEXT REFERENCES recordings(id) ON DELETE SET NULL,
                    profile_source TEXT NOT NULL DEFAULT 'youtube',
                    source TEXT NOT NULL,
                    played_at TEXT,
                    title TEXT NOT NULL,
                    primary_artist TEXT NOT NULL,
                    import_batch_id TEXT,
                    linked_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS genre_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    provider_recording_id TEXT NOT NULL DEFAULT '',
                    raw_genres TEXT NOT NULL,
                    identity_confidence REAL NOT NULL,
                    evidence_confidence REAL NOT NULL,
                    fetched_at TEXT NOT NULL,
                    UNIQUE(recording_id, provider, provider_recording_id)
                );
                CREATE TABLE IF NOT EXISTS genre_assignments (
                    recording_id TEXT PRIMARY KEY REFERENCES recordings(id) ON DELETE CASCADE,
                    primary_genre TEXT NOT NULL,
                    secondary_genres TEXT NOT NULL DEFAULT '[]',
                    identity_confidence REAL NOT NULL,
                    evidence_confidence REAL NOT NULL,
                    normalisation_confidence REAL NOT NULL,
                    combined_confidence REAL NOT NULL,
                    source_summary TEXT NOT NULL DEFAULT '[]',
                    taxonomy_version INTEGER NOT NULL,
                    auto_applied INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lookup_failures (
                    lookup_key TEXT PRIMARY KEY,
                    recording_id TEXT REFERENCES recordings(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 1,
                    next_retry_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recording_catalog_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT OR REPLACE INTO recording_catalog_meta(key, value) VALUES ('schema_version', ?)",
                (str(RECORDING_CATALOG_SCHEMA_VERSION),),
            )
            event_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(listening_events)").fetchall()}
            if "profile_source" not in event_columns:
                conn.execute("ALTER TABLE listening_events ADD COLUMN profile_source TEXT NOT NULL DEFAULT 'youtube'")
            conn.execute("CREATE INDEX IF NOT EXISTS listening_event_profile_source ON listening_events(profile_source)")
            migrated = conn.execute(
                "SELECT 1 FROM recording_catalog_meta WHERE key = 'alias_null_cleanup_v1'"
            ).fetchone()
            if not migrated:
                # SQLite treats NULL values as distinct in UNIQUE constraints.
                # Collapse legacy duplicates once and store 0 as the unknown
                # duration sentinel for all future aliases.
                conn.execute(
                    """
                    DELETE FROM recording_aliases
                    WHERE id NOT IN (
                        SELECT MIN(id) FROM recording_aliases
                        GROUP BY recording_id, normalised_title, normalised_artist,
                                 normalised_album, COALESCE(duration_seconds, 0), version_signature
                    )
                    """
                )
                conn.execute("UPDATE OR IGNORE recording_aliases SET duration_seconds = 0 WHERE duration_seconds IS NULL")
                conn.execute("DELETE FROM recording_aliases WHERE duration_seconds IS NULL")
                conn.execute(
                    "INSERT INTO recording_catalog_meta(key, value) VALUES ('alias_null_cleanup_v1', ?)",
                    (utc_now(),),
                )

    def resolve_track(self, track: dict[str, Any], *, create: bool = True) -> RecordingResolution | None:
        with self.connect() as conn:
            return self._resolve_track_in_connection(conn, track, create=create)

    def _resolve_track_in_connection(
        self,
        conn: sqlite3.Connection,
        track: dict[str, Any],
        *,
        create: bool = True,
    ) -> RecordingResolution | None:
        identifiers = identifier_candidates(track)
        cleaned_title = display_recording_title(track.get("title"), track.get("primary_artist"))
        versions, presentations = modifiers_for(cleaned_title)
        title_key = base_title(cleaned_title)
        artist_key = normalise_recording_text(track.get("primary_artist"))
        album_key = normalise_recording_text(track.get("album"))
        duration = _positive_int(track.get("duration_seconds"))
        signature = "|".join(versions)
        now = utc_now()

        found = []
        for namespace, value, confidence in identifiers:
            row = conn.execute(
                "SELECT recording_id FROM recording_identifiers WHERE namespace = ? AND value = ?",
                (namespace, value),
            ).fetchone()
            if row:
                found.append((str(row["recording_id"]), confidence, namespace))
        unique_ids = {item[0] for item in found}
        if len(unique_ids) > 1:
            self._failure_in_connection(conn, f"identifier-conflict:{'|'.join(sorted(unique_ids))}", None, "catalog", "identifier_conflict")
            return None
        if found:
            recording_id, confidence, namespace = max(found, key=lambda item: item[1])
            self._touch(conn, recording_id, now)
            self._store_alias(conn, recording_id, title_key, artist_key, album_key, duration, signature, confidence, f"exact_{namespace}")
            return RecordingResolution(recording_id, confidence, f"exact_{namespace}")

        medium = self._medium_match(conn, title_key, artist_key, album_key, duration, signature)
        # Only a high-quality medium match may attach a new provider ID.
        # Title + artist alone never reaches this threshold.
        if medium and medium.identity_confidence >= 0.90:
            self._touch(conn, medium.recording_id, now)
            for namespace, value, confidence in identifiers:
                conn.execute(
                    "INSERT OR IGNORE INTO recording_identifiers(namespace, value, recording_id, identity_confidence, created_at) VALUES (?, ?, ?, ?, ?)",
                    (namespace, value, medium.recording_id, min(confidence, medium.identity_confidence), now),
                )
            self._store_alias(conn, medium.recording_id, title_key, artist_key, album_key, duration, signature, medium.identity_confidence, medium.match_method)
            return medium

        # Weak text identity is useful for searching but is deliberately
        # not persisted as a canonical recording identity.
        if not identifiers and not medium:
            return None
        if not create:
            return medium

        recording_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO recordings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                recording_id,
                str(track.get("title") or "Unavailable track"),
                str(track.get("primary_artist") or "Unknown Artist"),
                str(track.get("album")) if track.get("album") else None,
                duration,
                json.dumps(versions),
                json.dumps(presentations),
                now,
                now,
                now,
            ),
        )
        for namespace, value, confidence in identifiers:
            conn.execute(
                "INSERT INTO recording_identifiers(namespace, value, recording_id, identity_confidence, created_at) VALUES (?, ?, ?, ?, ?)",
                (namespace, value, recording_id, confidence, now),
            )
        identity_confidence = max((item[2] for item in identifiers), default=medium.identity_confidence if medium else 0.65)
        self._store_alias(conn, recording_id, title_key, artist_key, album_key, duration, signature, identity_confidence, "new_strong_identifier" if identifiers else "medium_alias")
        return RecordingResolution(recording_id, identity_confidence, "new_strong_identifier" if identifiers else "medium_alias")

    def _medium_match(
        self,
        conn: sqlite3.Connection,
        title: str,
        artist: str,
        album: str,
        duration: int | None,
        signature: str,
    ) -> RecordingResolution | None:
        if not title or not artist:
            return None
        rows = conn.execute(
            "SELECT recording_id, normalised_album, duration_seconds FROM recording_aliases WHERE normalised_title = ? AND normalised_artist = ? AND version_signature = ?",
            (title, artist, signature),
        ).fetchall()
        candidates: dict[str, float] = {}
        for row in rows:
            album_match = bool(album and row["normalised_album"] and album == row["normalised_album"])
            stored_duration = _positive_int(row["duration_seconds"])
            duration_match = bool(duration and stored_duration and abs(duration - stored_duration) <= 4)
            if album_match and duration_match:
                confidence = 0.94
            elif duration_match:
                confidence = 0.90
            elif album_match:
                confidence = 0.86
            else:
                continue
            candidates[str(row["recording_id"])] = max(candidates.get(str(row["recording_id"]), 0), confidence)
        if len(candidates) != 1:
            return None
        recording_id, confidence = next(iter(candidates.items()))
        return RecordingResolution(recording_id, confidence, "title_artist_version_with_album_duration")

    @staticmethod
    def _store_alias(
        conn: sqlite3.Connection,
        recording_id: str,
        title: str,
        artist: str,
        album: str,
        duration: int | None,
        signature: str,
        confidence: float,
        method: str,
    ) -> None:
        if not title or not artist:
            return
        conn.execute(
            "INSERT OR IGNORE INTO recording_aliases(recording_id, normalised_title, normalised_artist, normalised_album, duration_seconds, version_signature, identity_confidence, match_method) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (recording_id, title, artist, album, duration or 0, signature, confidence, method),
        )

    @staticmethod
    def _touch(conn: sqlite3.Connection, recording_id: str, now: str) -> None:
        conn.execute("UPDATE recordings SET last_seen_at = ?, updated_at = ? WHERE id = ?", (now, now, recording_id))

    def sync_normalised(self, normalised: dict[str, Any], *, profile_source: str | None = None) -> dict[str, int]:
        self.rebuild_stale_assignments()
        tracks = {str(track.get("track_id")): track for track in normalised.get("tracks") or [] if isinstance(track, dict)}
        resolutions: dict[str, RecordingResolution] = {}
        now = utc_now()
        source_name = profile_source or _profile_source(normalised)
        events = [event for event in normalised.get("listening_events") or [] if isinstance(event, dict)]
        with self.connect() as conn:
            for track_id, track in tracks.items():
                resolved = self._resolve_track_in_connection(conn, track)
                if not resolved:
                    continue
                resolutions[track_id] = resolved
                track["recording_id"] = resolved.recording_id
                track["recording_identity_confidence"] = resolved.identity_confidence
                track["recording_match_method"] = resolved.match_method
                row = conn.execute("SELECT * FROM genre_assignments WHERE recording_id = ?", (resolved.recording_id,)).fetchone()
                assignment = self._assignment_payload(row) if row else None
                if assignment and assignment["autoApplied"]:
                    track["recording_genres"] = [assignment["primaryGenre"], *assignment["secondaryGenres"]]
                    track["recording_genre_confidence"] = {
                        "identity": assignment["identityConfidence"],
                        "evidence": assignment["evidenceConfidence"],
                        "normalisation": assignment["normalisationConfidence"],
                        "combined": assignment["combinedConfidence"],
                    }
                    track["recording_genre_sources"] = assignment["sourceSummary"]
            # This is a derived local index of the current profile. It does not
            # deduplicate or delete the canonical events in the profile.
            conn.execute("DELETE FROM listening_events WHERE profile_source = ?", (source_name,))
            event_rows = []
            for event in events:
                event_id = str(event.get("event_id") or event.get("id") or "").strip()
                if not event_id:
                    continue
                # Canonical event identifiers are intentionally stable so
                # overlapping uploads deduplicate inside one profile. In
                # anonymous hosted mode the same event may legitimately be
                # imported by different browser sessions, so scope only the
                # physical catalogue key while preserving the source event ID
                # in each session's normalised profile.
                stored_event_id = (
                    hashlib.sha256(f"{source_name}\0{event_id}".encode("utf-8")).hexdigest()
                    if source_name.startswith("session:")
                    else event_id
                )
                resolved = resolutions.get(str(event.get("track_id") or ""))
                event_rows.append(
                    (
                        stored_event_id,
                        resolved.recording_id if resolved else None,
                        source_name,
                        str(event.get("source") or "unknown"),
                        event.get("timestamp_utc") or event.get("played_at"),
                        str(event.get("title") or ""),
                        str(event.get("primary_artist") or event.get("artist") or ""),
                        event.get("import_batch_id"),
                        now,
                    )
                )
            conn.executemany(
                "INSERT INTO listening_events(event_id, recording_id, profile_source, source, played_at, title, primary_artist, import_batch_id, linked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                event_rows,
            )
        return {"tracks": len(tracks), "recordingsLinked": len(resolutions), "eventsIndexed": len(events)}

    def delete_profile_source_prefix(self, prefix: str) -> int:
        """Remove one anonymous profile's derived event index, retaining shared recordings."""
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM listening_events WHERE profile_source LIKE ?", (f"{prefix}%",))
            return int(cursor.rowcount or 0)

    def add_identifier(self, recording_id: str, namespace: str, value: str, confidence: float) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO recording_identifiers(namespace, value, recording_id, identity_confidence, created_at) VALUES (?, ?, ?, ?, ?)",
                (namespace, value, recording_id, confidence, utc_now()),
            )

    def save_evidence(
        self,
        recording_id: str,
        *,
        provider: str,
        provider_recording_id: str | None,
        raw_genres: Iterable[str],
        identity_confidence: float,
        evidence_confidence: float,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO genre_evidence(recording_id, provider, provider_recording_id, raw_genres, identity_confidence, evidence_confidence, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(recording_id, provider, provider_recording_id) DO UPDATE SET raw_genres=excluded.raw_genres, identity_confidence=excluded.identity_confidence, evidence_confidence=excluded.evidence_confidence, fetched_at=excluded.fetched_at",
                (recording_id, provider, provider_recording_id or "", json.dumps(list(raw_genres), ensure_ascii=False), identity_confidence, evidence_confidence, utc_now()),
            )

    def save_assignment(
        self,
        recording_id: str,
        *,
        primary_genre: str,
        secondary_genres: Iterable[str],
        identity_confidence: float,
        evidence_confidence: float,
        normalisation_confidence: float,
        source_summary: Iterable[str],
    ) -> dict[str, Any]:
        combined = round(identity_confidence * evidence_confidence * normalisation_confidence, 4)
        auto_applied = combined >= AUTO_APPLY_THRESHOLD and identity_confidence >= 0.85
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO genre_assignments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(recording_id) DO UPDATE SET primary_genre=excluded.primary_genre, secondary_genres=excluded.secondary_genres, identity_confidence=excluded.identity_confidence, evidence_confidence=excluded.evidence_confidence, normalisation_confidence=excluded.normalisation_confidence, combined_confidence=excluded.combined_confidence, source_summary=excluded.source_summary, taxonomy_version=excluded.taxonomy_version, auto_applied=excluded.auto_applied, updated_at=excluded.updated_at",
                (
                    recording_id,
                    primary_genre,
                    json.dumps(list(secondary_genres), ensure_ascii=False),
                    identity_confidence,
                    evidence_confidence,
                    normalisation_confidence,
                    combined,
                    json.dumps(list(source_summary), ensure_ascii=False),
                    TAXONOMY_VERSION,
                    int(auto_applied),
                    utc_now(),
                ),
            )
        return self.assignment(recording_id) or {}

    def assignment(self, recording_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM genre_assignments WHERE recording_id = ?", (recording_id,)).fetchone()
        if not row:
            return None
        return self._assignment_payload(row)

    @staticmethod
    def _assignment_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "recordingId": row["recording_id"],
            "primaryGenre": row["primary_genre"],
            "secondaryGenres": json.loads(row["secondary_genres"]),
            "identityConfidence": row["identity_confidence"],
            "evidenceConfidence": row["evidence_confidence"],
            "normalisationConfidence": row["normalisation_confidence"],
            "combinedConfidence": row["combined_confidence"],
            "sourceSummary": json.loads(row["source_summary"]),
            "taxonomyVersion": row["taxonomy_version"],
            "autoApplied": bool(row["auto_applied"]) and int(row["taxonomy_version"]) == TAXONOMY_VERSION,
        }

    def rebuild_stale_assignments(self) -> int:
        """Re-map durable raw evidence after taxonomy logic changes, offline."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT e.recording_id, e.provider, e.raw_genres,
                       e.identity_confidence, e.evidence_confidence
                FROM genre_evidence e
                JOIN genre_assignments a ON a.recording_id = e.recording_id
                WHERE a.taxonomy_version != ?
                ORDER BY (e.identity_confidence * e.evidence_confidence) DESC, e.fetched_at DESC
                """,
                (TAXONOMY_VERSION,),
            ).fetchall()
        rebuilt = 0
        seen: set[str] = set()
        for row in rows:
            recording_id = str(row["recording_id"])
            if recording_id in seen:
                continue
            seen.add(recording_id)
            taxonomy = normalise_external_genres(json.loads(row["raw_genres"]))
            if not taxonomy:
                continue
            self.save_assignment(
                recording_id,
                primary_genre=taxonomy.primary_genre,
                secondary_genres=taxonomy.secondary_genres,
                identity_confidence=float(row["identity_confidence"]),
                evidence_confidence=float(row["evidence_confidence"]),
                normalisation_confidence=taxonomy.normalisation_confidence,
                source_summary=[str(row["provider"])],
            )
            rebuilt += 1
        return rebuilt

    def can_retry(self, lookup_key: str) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT next_retry_at FROM lookup_failures WHERE lookup_key = ?", (lookup_key,)).fetchone()
        if not row or not row["next_retry_at"]:
            return True
        try:
            retry_at = datetime.fromisoformat(str(row["next_retry_at"]).replace("Z", "+00:00"))
        except ValueError:
            return True
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return retry_at <= datetime.now(timezone.utc)

    def blocked_lookup_keys(self) -> set[str]:
        """Load active negative-cache keys once for a whole enrichment batch."""
        now = datetime.now(timezone.utc)
        with self.connect() as conn:
            rows = conn.execute("SELECT lookup_key, next_retry_at FROM lookup_failures WHERE next_retry_at IS NOT NULL").fetchall()
        blocked: set[str] = set()
        for row in rows:
            try:
                retry_at = datetime.fromisoformat(str(row["next_retry_at"]).replace("Z", "+00:00"))
            except ValueError:
                continue
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            if retry_at > now:
                blocked.add(str(row["lookup_key"]))
        return blocked

    def record_failure(self, lookup_key: str, recording_id: str | None, provider: str, reason: str, next_retry_at: str | None = None) -> None:
        with self.connect() as conn:
            self._failure_in_connection(conn, lookup_key, recording_id, provider, reason, next_retry_at)

    @staticmethod
    def _failure_in_connection(conn: sqlite3.Connection, lookup_key: str, recording_id: str | None, provider: str, reason: str, next_retry_at: str | None = None) -> None:
        conn.execute(
            "INSERT INTO lookup_failures(lookup_key, recording_id, provider, reason, attempts, next_retry_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?) ON CONFLICT(lookup_key) DO UPDATE SET reason=excluded.reason, attempts=lookup_failures.attempts+1, next_retry_at=excluded.next_retry_at, updated_at=excluded.updated_at",
            (lookup_key, recording_id, provider, reason, next_retry_at, utc_now()),
        )

    def summary(self) -> dict[str, Any]:
        with self.connect() as conn:
            counts = {
                table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("recordings", "recording_identifiers", "recording_aliases", "listening_events", "genre_evidence", "genre_assignments", "lookup_failures")
            }
            auto = int(conn.execute("SELECT COUNT(*) FROM genre_assignments WHERE auto_applied = 1 AND taxonomy_version = ?", (TAXONOMY_VERSION,)).fetchone()[0])
            confidence = conn.execute("SELECT AVG(identity_confidence), AVG(evidence_confidence), AVG(normalisation_confidence), AVG(combined_confidence) FROM genre_assignments").fetchone()
        return {
            "schemaVersion": RECORDING_CATALOG_SCHEMA_VERSION,
            "taxonomyVersion": TAXONOMY_VERSION,
            **counts,
            "autoAppliedAssignments": auto,
            "averageConfidence": {
                "identity": round(float(confidence[0] or 0), 3),
                "evidence": round(float(confidence[1] or 0), 3),
                "normalisation": round(float(confidence[2] or 0), 3),
                "combined": round(float(confidence[3] or 0), 3),
            },
        }

    def details(self, recording_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            recording = conn.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
            if not recording:
                return None
            identifiers = conn.execute(
                "SELECT namespace, value, identity_confidence FROM recording_identifiers WHERE recording_id = ? ORDER BY namespace, value",
                (recording_id,),
            ).fetchall()
            evidence = conn.execute(
                "SELECT provider, provider_recording_id, raw_genres, identity_confidence, evidence_confidence, fetched_at FROM genre_evidence WHERE recording_id = ? ORDER BY fetched_at DESC",
                (recording_id,),
            ).fetchall()
        return {
            "recordingId": recording_id,
            "title": recording["title"],
            "primaryArtist": recording["primary_artist"],
            "album": recording["album"],
            "durationSeconds": recording["duration_seconds"],
            "versionModifiers": json.loads(recording["version_modifiers"]),
            "presentationModifiers": json.loads(recording["presentation_modifiers"]),
            "identifiers": [dict(row) for row in identifiers],
            "genreEvidence": [
                {
                    **dict(row),
                    "raw_genres": json.loads(row["raw_genres"]),
                }
                for row in evidence
            ],
            "genreAssignment": self.assignment(recording_id),
        }


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _profile_source(normalised: dict[str, Any]) -> str:
    source = normalise_recording_text((normalised.get("metadata") or {}).get("source"))
    return "spotify" if "spotify" in source else "youtube"
