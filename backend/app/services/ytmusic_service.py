from __future__ import annotations

import json
import logging
import re
import shutil
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
import unicodedata

import httpx

from app.analysis.duration import extract_duration_seconds
from app.analysis.media import (
    album_cache_failure,
    album_cache_lookup,
    album_cache_set,
    album_cache_success_entry,
    album_image_url as resolve_album_image_url,
    ensure_album_image_cache_schema,
    release_year_from_payload,
    artist_cache_lookup,
    artist_cache_set,
    ensure_artist_image_cache_schema,
    normalise_album_name,
)
from app.analysis.normalizer import UNKNOWN_ARTIST, extract_album, extract_artist_ids, extract_artist_names, extract_tracks
from app.analysis.track_metadata import (
    cache_track_metadata,
    display_recording_title,
    ensure_track_metadata_cache,
    metadata_alias_key,
    version_signature,
)
from app.analysis.thumbnails import best_thumbnail
from app.config import Settings


logger = logging.getLogger(__name__)


# A few artist names are genuinely ambiguous in YouTube Music search.  These are
# exact display-name matches from exports, rather than fuzzy aliases: using the
# official channel avoids caching an unrelated artist with the same name.
ARTIST_BROWSE_ID_OVERRIDES = {
    "g.e.m.": "UCBRh2Z_U1Lw9-YJ-XGZ8M2Q",
    "jay chou": "UCL2MDNdwEtV6aYUgNjFQGZA",
    "lane 8": "UCqjupXgFQVmnpYo-sJ47dHg",
    "周杰倫": "UCL2MDNdwEtV6aYUgNjFQGZA",
}
ARTIST_IMAGE_RESOLVER_VERSION = 2


class YTMusicService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def auth_status(self) -> dict[str, Any]:
        auth_file_exists = self.settings.ytmusic_auth_file.exists()
        browser_file_exists = self.settings.ytmusic_browser_auth_file.exists()
        oauth_configured = bool(self.settings.ytmusic_client_id and self.settings.ytmusic_client_secret)
        if browser_file_exists:
            try:
                yt = self.client(prefer_browser=True)
                info = yt.get_account_info()
                name = None
                if isinstance(info, dict):
                    name = info.get("name") or info.get("accountName")
                return {
                    "connected": True,
                    "auth_file_exists": True,
                    "auth_file_path": str(self.settings.ytmusic_browser_auth_file),
                    "oauth_client_configured": oauth_configured,
                    "account_name": name,
                    "message": "Authenticated YouTube Music access is working through manual browser-header auth.",
                }
            except Exception as exc:  # noqa: BLE001
                return {
                    "connected": False,
                    "auth_file_exists": True,
                    "auth_file_path": str(self.settings.ytmusic_browser_auth_file),
                    "oauth_client_configured": oauth_configured,
                    "account_name": None,
                    "message": f"Browser-header authentication check failed: {friendly_auth_error(exc, is_browser=True)}",
                }
        if not auth_file_exists:
            return {
                "connected": False,
                "auth_file_exists": False,
                "auth_file_path": str(self.settings.ytmusic_auth_file),
                "oauth_client_configured": oauth_configured,
                "account_name": None,
                "message": "No oauth.json file found. Use the Connect YouTube Music guide to create it locally.",
            }
        if not oauth_configured:
            return {
                "connected": False,
                "auth_file_exists": True,
                "auth_file_path": str(self.settings.ytmusic_auth_file),
                "oauth_client_configured": False,
                "account_name": None,
                "message": "oauth.json exists, but YTMUSIC_OAUTH_CLIENT_ID and YTMUSIC_OAUTH_CLIENT_SECRET are not configured.",
            }
        try:
            yt = self.client()
            info = yt.get_account_info()
            name = None
            if isinstance(info, dict):
                name = info.get("name") or info.get("accountName")
            return {
                "connected": True,
                "auth_file_exists": True,
                "auth_file_path": str(self.settings.ytmusic_auth_file),
                "oauth_client_configured": True,
                "account_name": name,
                "message": "Authenticated YouTube Music access is working.",
            }
        except Exception as exc:  # noqa: BLE001 - expose friendly failure only
            return {
                "connected": False,
                "auth_file_exists": True,
                "auth_file_path": str(self.settings.ytmusic_auth_file),
                "oauth_client_configured": True,
                "account_name": None,
                "message": f"Authentication check failed: {friendly_auth_error(exc)}",
            }

    def setup_instructions(self) -> dict[str, Any]:
        return {
            "preferred_method": "ytmusicapi OAuth",
            "auth_file_path": str(self.settings.ytmusic_auth_file),
            "private_directory": str(self.settings.private_dir),
            "steps": [
                "Install backend requirements.",
                "Create a Google Cloud OAuth client ID for TVs and Limited Input devices.",
                "Set YTMUSIC_OAUTH_CLIENT_ID and YTMUSIC_OAUTH_CLIENT_SECRET locally.",
                "Run ytmusicapi oauth from backend/private and complete the device login.",
                "Keep oauth.json in backend/private only.",
            ],
            "warning": "Do not commit oauth.json, browser headers, cookies, .env files, or raw listening exports.",
        }

    def client(self, prefer_browser: bool = True) -> Any:
        try:
            from ytmusicapi import OAuthCredentials, YTMusic
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("ytmusicapi is not installed. Run scripts/setup_windows.ps1 first.") from exc
        if prefer_browser and self.settings.ytmusic_browser_auth_file.exists():
            return YTMusic(str(self.settings.ytmusic_browser_auth_file))
        if not self.settings.ytmusic_auth_file.exists():
            raise RuntimeError(f"Missing auth file: {self.settings.ytmusic_auth_file}")
        if not self.settings.ytmusic_client_id or not self.settings.ytmusic_client_secret:
            raise RuntimeError("Missing YTMUSIC_OAUTH_CLIENT_ID or YTMUSIC_OAUTH_CLIENT_SECRET.")
        credentials = OAuthCredentials(
            client_id=self.settings.ytmusic_client_id,
            client_secret=self.settings.ytmusic_client_secret,
        )
        return YTMusic(str(self.settings.ytmusic_auth_file), oauth_credentials=credentials)

    def public_client(self) -> Any:
        try:
            from ytmusicapi import YTMusic
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("ytmusicapi is not installed. Run scripts/setup_windows.ps1 first.") from exc
        return YTMusic()

    def fetch_library(self) -> dict[str, Any]:
        yt = self.client()
        warnings: list[str] = []

        def safe_call(label: str, fn: Callable[[], Any], default: Any) -> Any:
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{label} failed: {exc}")
                return default

        history = safe_call("history", lambda: yt.get_history(), [])
        liked_songs = safe_call("liked songs", lambda: yt.get_liked_songs(limit=1000), {"tracks": []})
        library_songs = safe_call("library songs", lambda: yt.get_library_songs(limit=1000, order="recently_added"), [])
        library_artists = safe_call("library artists", lambda: yt.get_library_artists(limit=1000), [])
        library_albums = safe_call("library albums", lambda: yt.get_library_albums(limit=1000, order="recently_added"), [])
        library_playlists = safe_call("library playlists", lambda: yt.get_library_playlists(limit=None), [])
        playlist_tracks: dict[str, Any] = {}
        for playlist in library_playlists or []:
            playlist_id = playlist.get("playlistId") if isinstance(playlist, dict) else None
            if not playlist_id:
                continue
            result = safe_call(f"playlist {playlist_id}", lambda playlist_id=playlist_id: yt.get_playlist(playlist_id, limit=None), {"tracks": []})
            playlist_tracks[playlist_id] = result.get("tracks", []) if isinstance(result, dict) else []
        return {
            "source": "ytmusicapi",
            "history": history,
            "liked_songs": liked_songs,
            "library_songs": library_songs,
            "library_artists": library_artists,
            "library_albums": library_albums,
            "library_playlists": library_playlists,
            "playlist_tracks": playlist_tracks,
            "warnings": warnings,
        }

    def save_raw_snapshot(self, raw_dir: Path, raw: dict[str, Any]) -> None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        path = raw_dir / "latest_raw_collection.json"
        path.write_text(json.dumps(raw, ensure_ascii=True, indent=2, default=str), encoding="utf-8")

    def enrich_artist_image_cache(
        self,
        raw: dict[str, Any],
        artist_cache: dict[str, Any],
        limit: int = 25,
        preferred_artists: list[str] | None = None,
        checkpoint: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, int]:
        if limit <= 0:
            return {"seeded": 0, "attempted": 0, "added": 0, "failed": 0, "repaired": 0}
        artist_cache = ensure_artist_image_cache_schema(artist_cache)
        seeded = seed_artist_cache_from_library(raw, artist_cache)

        def save_checkpoint() -> None:
            raw["artist_image_cache_v2"] = artist_cache
            if checkpoint:
                checkpoint(artist_cache)

        if seeded:
            save_checkpoint()
        artist_targets = top_artist_targets(raw, preferred_artists=preferred_artists)
        if not artist_targets:
            raw["artist_image_cache_v2"] = artist_cache
            return {"seeded": seeded, "attempted": 0, "added": 0, "failed": 0, "repaired": 0}

        try:
            yt = self.client()
        except Exception:
            yt = self.public_client()
        attempted = 0
        added = 0
        failed = 0
        repaired = 0
        for artist, artist_id in artist_targets:
            override_browse_id = ARTIST_BROWSE_ID_OVERRIDES.get(str(artist).strip().casefold())
            if override_browse_id:
                repaired += remove_conflicting_artist_cache_entries(artist_cache, artist, override_browse_id)
            cached = artist_cache_lookup(artist_cache, artist, artist_id)
            if cached and override_browse_id:
                repaired += ensure_artist_cache_alias(cached, artist)
            curated_retry_required = bool(
                override_browse_id
                and cached
                and not artist_cache_has_thumbnail(cached)
                and cached.get("resolver_version") != ARTIST_IMAGE_RESOLVER_VERSION
            )
            # Replace an old ambiguous match when an exact, curated channel is
            # available.  Otherwise cache hits remain entirely offline.
            if artist_cache_has_result(cached) and not curated_retry_required and (not override_browse_id or cached.get("browse_id") == override_browse_id):
                continue
            if attempted >= limit:
                break
            attempted += 1
            try:
                payload = None
                matched_browse_id = override_browse_id or artist_id
                if matched_browse_id:
                    try:
                        payload = yt.get_artist(str(matched_browse_id))
                    except Exception:
                        payload = None
                if not artist_payload_has_thumbnail(payload):
                    search_match = first_artist_search_result(yt, artist)
                    matched_browse_id = browse_id_from_payload(search_match) or matched_browse_id
                    if matched_browse_id:
                        try:
                            payload = yt.get_artist(str(matched_browse_id))
                        except Exception:
                            failure_entry = artist_cache_failure_entry(artist, matched_browse_id, "artist_page_failed")
                            failure_entry["resolver_version"] = ARTIST_IMAGE_RESOLVER_VERSION
                            artist_cache_set(artist_cache, artist, failure_entry, matched_browse_id)
                            failed += 1
                            save_checkpoint()
                            continue
                    else:
                        payload = None
                entry = artist_cache_entry(artist, payload, artist_id=matched_browse_id)
                entry["resolver_version"] = ARTIST_IMAGE_RESOLVER_VERSION
                if entry.get("thumbnails"):
                    added += 1
                    logger.info('[artist-image] Resolved "%s" using browseId %s', artist, entry.get("browse_id") or "unknown")
                else:
                    failed += 1
                    logger.info('[artist-image] No official thumbnail found for "%s"', artist)
                artist_cache_set(artist_cache, artist, entry, entry.get("artist_id") or matched_browse_id)
            except Exception:
                failure_entry = artist_cache_failure_entry(artist, artist_id, "upstream_exception")
                failure_entry["resolver_version"] = ARTIST_IMAGE_RESOLVER_VERSION
                artist_cache_set(artist_cache, artist, failure_entry, artist_id)
                failed += 1
                logger.info('[artist-image] No official thumbnail found for "%s"', artist)
            save_checkpoint()
        save_checkpoint()
        return {"seeded": seeded, "attempted": attempted, "added": added, "failed": failed, "repaired": repaired}

    def enrich_album_image_cache(
        self,
        raw: dict[str, Any],
        album_cache: dict[str, Any],
        limit: int = 48,
        preferred_albums: list[dict[str, Any]] | None = None,
        checkpoint: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, int]:
        if limit <= 0:
            return {"seeded": 0, "attempted": 0, "added": 0, "failed": 0}
        album_cache = ensure_album_image_cache_schema(album_cache)
        seeded = seed_album_cache_from_library(raw, album_cache)

        def save_checkpoint() -> None:
            raw["album_image_cache_v1"] = album_cache
            if checkpoint:
                checkpoint(album_cache)

        if seeded:
            save_checkpoint()
        album_targets = top_album_targets(raw, preferred_albums=preferred_albums)
        preferred_keys = {
            (normalise_album_name(item.get("album")), normalise_artist_name(item.get("artist")))
            for item in preferred_albums or []
            if isinstance(item, dict)
        }
        if not album_targets:
            raw["album_image_cache_v1"] = album_cache
            return {"seeded": seeded, "attempted": 0, "added": 0, "failed": 0}

        yt = self.public_client()
        attempted = 0
        added = 0
        failed = 0
        for target in album_targets:
            album = target["album"]
            artist = target["artist"]
            album_id = target.get("album_id")
            cached = album_cache_lookup(album_cache, album_id=album_id, album=album, artist=artist)
            is_preferred = (normalise_album_name(album), normalise_artist_name(artist)) in preferred_keys
            # Revisit cached covers that have no release year.  This keeps the
            # existing artwork cache useful as a durable metadata-enrichment
            # source for Musical Age instead of treating a thumbnail as a
            # complete album record.
            cache_has_year = bool(cached and cached.get("release_year"))
            if album_cache_has_result(cached) and cache_has_year and (album_cache_has_thumbnail(cached) or not is_preferred):
                continue
            if attempted >= limit:
                break
            attempted += 1
            try:
                payload = None
                search_match = None
                matched_browse_id = album_id
                source = "ytmusicapi.get_album"
                if matched_browse_id:
                    try:
                        payload = yt.get_album(str(matched_browse_id))
                    except Exception:
                        payload = None
                if not album_payload_has_thumbnail(payload):
                    search_match = first_album_search_result(yt, str(album), str(artist))
                    matched_browse_id = browse_id_from_payload(search_match) or matched_browse_id
                    if matched_browse_id:
                        try:
                            payload = yt.get_album(str(matched_browse_id))
                            source = "ytmusicapi.get_album"
                        except Exception:
                            payload = search_match if album_payload_has_thumbnail(search_match) else None
                            source = "ytmusicapi.album_search"
                    else:
                        payload = None
                if not album_payload_has_thumbnail(payload) and album_payload_has_thumbnail(search_match):
                    payload = search_match
                    source = "ytmusicapi.album_search"
                    matched_browse_id = browse_id_from_payload(search_match) or matched_browse_id
                entry = album_cache_entry(str(album), str(artist), payload, album_id=matched_browse_id, source=source)
                if entry.get("album_image_url"):
                    added += 1
                    logger.info('[album-image] Resolved "%s" by "%s" using browseId %s', album, artist, entry.get("browse_id") or "unknown")
                else:
                    failed += 1
                    logger.info('[album-image] No album thumbnail found for "%s" by "%s"', album, artist)
                album_cache_set(album_cache, entry, album_id=entry.get("album_id") or matched_browse_id, album=album, artist=artist)
            except Exception:
                failure = album_cache_failure(str(album), str(artist), album_id, "upstream_exception")
                album_cache_set(album_cache, failure, album_id=album_id, album=album, artist=artist)
                failed += 1
                logger.info('[album-image] Album lookup failed for "%s" by "%s"', album, artist)
            save_checkpoint()
        save_checkpoint()
        return {"seeded": seeded, "attempted": attempted, "added": added, "failed": failed}

    def enrich_track_metadata_cache(
        self,
        normalised: dict[str, Any],
        metadata_cache: dict[str, Any],
        limit: int = 100,
        preferred_track_ids: list[str] | None = None,
    ) -> dict[str, int]:
        """Resolve authoritative per-video track, artist, album, and year metadata.

        Exact video-ID watch metadata is preferred. An exact title+artist song
        search is only used when a presentation upload has no album link. When
        an album is resolved, its authoritative track list seeds reusable alias
        records so Topic, audio, lyric, and official-video presentations can
        share album metadata without merging their listening events.
        """

        cache = ensure_track_metadata_cache(metadata_cache)
        if limit <= 0:
            return {"attempted": 0, "added": 0, "failed": 0, "albumAliases": 0, "remaining": 0}
        counts = Counter(str(event.get("track_id") or "") for event in normalised.get("play_events") or [])
        preferred = {str(value) for value in preferred_track_ids or [] if str(value)}
        now = datetime.now(timezone.utc)

        def due(track: dict[str, Any]) -> bool:
            video_id = str(track.get("video_id") or "")
            if not video_id:
                return False
            cached = cache["items"].get(video_id)
            if (
                isinstance(cached, dict)
                and cached.get("status") == "resolved"
                and cached.get("album")
                and cached.get("album_release_year")
                and cached.get("artwork_checked") is True
            ):
                return False
            failure = cache["failures"].get(video_id)
            if not isinstance(failure, dict):
                return True
            retry_at = failure.get("retryAfter")
            if not retry_at:
                return True
            try:
                return datetime.fromisoformat(str(retry_at).replace("Z", "+00:00")) <= now
            except ValueError:
                return True

        targets = [
            track
            for track in normalised.get("tracks") or []
            if isinstance(track, dict)
            and track.get("video_id")
            and track.get("title")
            and track.get("primary_artist") not in (None, "", UNKNOWN_ARTIST)
            and due(track)
        ]
        targets.sort(
            key=lambda track: (
                0 if str(track.get("track_id")) in preferred else 1,
                -counts.get(str(track.get("track_id") or ""), 0),
                str(track.get("title") or "").casefold(),
            )
        )
        selected = targets[:limit]
        if not selected:
            return {"attempted": 0, "added": 0, "failed": 0, "albumAliases": 0, "remaining": 0}

        yt = self.public_client()
        added = failed = album_aliases = 0
        fetched_albums: dict[str, dict[str, Any] | None] = {}
        for track in selected:
            video_id = str(track.get("video_id") or "")
            source_title = str(track.get("title") or "")
            source_artist = str(track.get("primary_artist") or "")
            candidate: dict[str, Any] | None = None
            match_method = "youtube_video_id_watch_playlist"
            identity_confidence = 1.0
            try:
                watch = yt.get_watch_playlist(videoId=video_id, limit=3)
                for item in watch.get("tracks") or [] if isinstance(watch, dict) else []:
                    if isinstance(item, dict) and str(item.get("videoId") or "") == video_id:
                        candidate = item
                        break
            except Exception:
                candidate = None

            album = candidate.get("album") if isinstance(candidate, dict) and isinstance(candidate.get("album"), dict) else {}
            if not album:
                query_title = display_recording_title(source_title, source_artist)
                try:
                    results = yt.search(f"{query_title} {source_artist}", filter="songs", limit=8)
                except Exception:
                    results = []
                source_key = metadata_alias_key(query_title, source_artist)
                for item in results if isinstance(results, list) else []:
                    if not isinstance(item, dict):
                        continue
                    names = [str(value.get("name") if isinstance(value, dict) else value) for value in item.get("artists") or []]
                    if normalise_artist_name(source_artist) not in {normalise_artist_name(name) for name in names}:
                        continue
                    if metadata_alias_key(item.get("title"), source_artist) != source_key:
                        continue
                    if version_signature(item.get("title")) != version_signature(query_title):
                        continue
                    candidate = item
                    album = item.get("album") if isinstance(item.get("album"), dict) else {}
                    match_method = "exact_title_artist_song_search"
                    identity_confidence = 0.90
                    break

            if not isinstance(candidate, dict):
                cache["failures"][video_id] = {
                    "reason": "no_authoritative_track_match",
                    "attemptedAt": now.isoformat(),
                    "retryAfter": (now + timedelta(days=7)).isoformat(),
                }
                failed += 1
                continue

            candidate_artists = [
                str(value.get("name") if isinstance(value, dict) else value).strip()
                for value in candidate.get("artists") or []
                if str(value.get("name") if isinstance(value, dict) else value).strip()
            ] or [source_artist]
            album_id = str(album.get("id") or album.get("browseId") or "").strip() or None
            album_payload: dict[str, Any] | None = None
            if album_id:
                if album_id not in fetched_albums:
                    try:
                        fetched_albums[album_id] = yt.get_album(album_id)
                    except Exception:
                        fetched_albums[album_id] = None
                album_payload = fetched_albums[album_id]
            album_year = release_year_from_payload(album_payload or {}) or release_year_from_payload(candidate)
            album_thumbnail = best_thumbnail((album_payload or {}).get("thumbnails") or album.get("thumbnails"))
            entry = {
                "status": "resolved",
                "video_id": video_id,
                "title": display_recording_title(candidate.get("title") or source_title, candidate_artists[0]),
                "primary_artist": candidate_artists[0],
                "artists": candidate_artists,
                "album": (album_payload or {}).get("title") or album.get("name") or album.get("title"),
                "album_id": album_id,
                "album_art_url": album_thumbnail.get("url") if album_thumbnail else None,
                "album_art_source": "youtube_album_cover" if album_thumbnail else None,
                "artwork_checked": True,
                "original_release_year": None,
                "edition_release_year": album_year,
                "album_release_year": album_year,
                "source": "ytmusicapi.public",
                "identity_confidence": identity_confidence,
                "match_confidence": identity_confidence,
                "release_year_confidence": "high" if match_method == "youtube_video_id_watch_playlist" else "medium",
                "match_method": match_method,
                "version_signature": list(version_signature(display_recording_title(source_title, source_artist))),
                "fetchedAt": now.isoformat(),
            }
            cache_track_metadata(cache, entry, video_id=video_id)
            added += 1

            if isinstance(album_payload, dict):
                album_name = str(album_payload.get("title") or entry.get("album") or "").strip()
                release_year = release_year_from_payload(album_payload)
                album_thumbnail = best_thumbnail(album_payload.get("thumbnails"))
                album_artists = [
                    str(value.get("name") if isinstance(value, dict) else value).strip()
                    for value in album_payload.get("artists") or []
                    if str(value.get("name") if isinstance(value, dict) else value).strip()
                ] or candidate_artists
                for album_track in album_payload.get("tracks") or []:
                    if not isinstance(album_track, dict) or not album_track.get("title"):
                        continue
                    artists = [
                        str(value.get("name") if isinstance(value, dict) else value).strip()
                        for value in album_track.get("artists") or []
                        if str(value.get("name") if isinstance(value, dict) else value).strip()
                    ] or album_artists
                    album_entry = {
                        "status": "resolved",
                        "video_id": album_track.get("videoId"),
                        "title": display_recording_title(album_track.get("title"), artists[0]),
                        "primary_artist": artists[0],
                        "artists": artists,
                        "album": album_name,
                        "album_id": album_id,
                        "album_art_url": album_thumbnail.get("url") if album_thumbnail else None,
                        "album_art_source": "youtube_album_cover" if album_thumbnail else None,
                        "artwork_checked": True,
                        "original_release_year": None,
                        "edition_release_year": release_year,
                        "album_release_year": release_year,
                        "source": "ytmusicapi.public.album_tracklist",
                        "identity_confidence": 0.95,
                        "match_confidence": 0.95,
                        "release_year_confidence": "medium",
                        "match_method": "authoritative_album_tracklist",
                        "version_signature": list(version_signature(album_track.get("title"))),
                        "fetchedAt": now.isoformat(),
                    }
                    cache_track_metadata(cache, album_entry, video_id=album_track.get("videoId"))
                    album_aliases += 1

        return {
            "attempted": len(selected),
            "added": added,
            "failed": failed,
            "albumAliases": album_aliases,
            "remaining": max(0, len(targets) - len(selected)),
        }

    def enrich_duration_cache(
        self,
        normalised: dict[str, Any],
        duration_cache: dict[str, Any],
        limit: int = 1000,
        checkpoint: Callable[[dict[str, Any]], None] | None = None,
        progress_callback: Callable[[int, int, int], None] | None = None,
    ) -> dict[str, Any]:
        if limit <= 0:
            return {"attempted": 0, "added": 0, "failed": 0, "api_batches": 0, "fallback_attempted": 0, "remaining": 0}
        all_targets = _duration_targets(normalised, duration_cache, limit)
        if not all_targets:
            return {"attempted": 0, "added": 0, "failed": 0, "api_batches": 0, "fallback_attempted": 0, "remaining": 0}
        # Public InnerTube calls are one-at-a-time. Keep hosted batches short,
        # checkpoint every result, and let the coordinator queue the next
        # batch. This avoids losing several minutes of work on a free-tier
        # process restart.
        public_batch_limit = max(1, int(getattr(self.settings, "duration_public_batch_limit", 100)))
        targets = all_targets if self.settings.youtube_data_api_key else all_targets[:public_batch_limit]
        remaining = len(all_targets) - len(targets)

        now = datetime.now(timezone.utc)
        added = 0
        processed = 0
        api_batches = 0
        unresolved = list(targets)

        def publish_checkpoint() -> None:
            if checkpoint:
                checkpoint(duration_cache)
            if progress_callback:
                progress_callback(processed, len(targets), added)

        if self.settings.youtube_data_api_key:
            unresolved = []
            for batch in _chunks(targets, 50):
                api_batches += 1
                try:
                    response = httpx.get(
                        "https://www.googleapis.com/youtube/v3/videos",
                        params={
                            "part": "contentDetails,snippet",
                            "id": ",".join(batch),
                            "key": self.settings.youtube_data_api_key,
                            "fields": "items(id,contentDetails(duration),snippet(categoryId,title,channelTitle))",
                        },
                        timeout=10.0,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    items = payload.get("items") if isinstance(payload, dict) else []
                    indexed_items = {
                        str(item.get("id")): item
                        for item in items or []
                        if isinstance(item, dict) and item.get("id")
                    }
                    for video_id in batch:
                        item = indexed_items.get(video_id) or {}
                        seconds = _parse_iso8601_duration((item.get("contentDetails") or {}).get("duration"))
                        if seconds:
                            snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
                            _set_duration_cache_success(
                                duration_cache,
                                video_id,
                                seconds,
                                "youtube_data_api.videos.list",
                                now,
                                music_classification="confirmed_music" if str(snippet.get("categoryId") or "") == "10" else None,
                                media_title=snippet.get("title"),
                                media_author=snippet.get("channelTitle"),
                                identity_confidence="medium",
                            )
                            added += 1
                            processed += 1
                            publish_checkpoint()
                        else:
                            unresolved.append(video_id)
                except (httpx.HTTPError, ValueError, TypeError):
                    # A public YTMusic lookup can still resolve individual IDs when the API key is misconfigured or quota-limited.
                    unresolved.extend(batch)

        fallback_attempted = 0
        if unresolved:
            try:
                yt = self.public_client()
            except Exception:
                yt = None
            for video_id in unresolved:
                fallback_attempted += 1
                try:
                    payload = yt.get_song(video_id) if yt is not None else None
                    seconds = duration_from_ytmusic_payload(payload) if isinstance(payload, dict) else None
                except Exception:
                    seconds = None
                if seconds:
                    details = payload.get("videoDetails") if isinstance(payload, dict) and isinstance(payload.get("videoDetails"), dict) else {}
                    music_video_type = str(details.get("musicVideoType") or "")
                    _set_duration_cache_success(
                        duration_cache,
                        video_id,
                        seconds,
                        "ytmusicapi.public.get_song",
                        now,
                        music_classification="confirmed_music" if music_video_type.startswith("MUSIC_VIDEO_TYPE_") and "PODCAST" not in music_video_type else None,
                        media_title=details.get("title"),
                        media_author=details.get("author"),
                        identity_confidence="high",
                    )
                    added += 1
                else:
                    _set_duration_cache_retry(duration_cache, video_id, "ytmusicapi.public.get_song", now)
                processed += 1
                publish_checkpoint()

        failed = len(targets) - added
        return {
            "attempted": len(targets),
            "added": added,
            "failed": failed,
            "api_batches": api_batches,
            "fallback_attempted": fallback_attempted,
            "remaining": remaining,
        }

    def enrich_release_year_cache(self, normalised: dict[str, Any], release_cache: dict[str, Any], limit: int = 100) -> dict[str, int]:
        """Resolve high-impact missing release years through exact song and artist matches.

        The cache is keyed by stable local track ID, so an import can be rebuilt
        without losing metadata acquired for the same YouTube video.
        """
        if limit <= 0:
            return {"attempted": 0, "added": 0, "failed": 0, "remaining": 0}
        counts = Counter(str(event.get("track_id") or "") for event in normalised.get("play_events") or [])
        targets = [
            track
            for track in normalised.get("tracks") or []
            if isinstance(track, dict)
            and track.get("track_id")
            and not track.get("release_year")
            and track.get("title")
            and track.get("primary_artist")
        ]
        targets.sort(key=lambda track: (-counts.get(str(track.get("track_id")), 0), str(track.get("title")).casefold()))
        now = datetime.now(timezone.utc)

        def release_lookup_due(track: dict[str, Any]) -> bool:
            cached = release_cache.get(str(track.get("track_id")))
            if not isinstance(cached, dict):
                return True
            if cached.get("release_year"):
                return False
            retry_at = cached.get("retry_after")
            if not retry_at:
                return True
            try:
                return datetime.fromisoformat(str(retry_at).replace("Z", "+00:00")) <= now
            except ValueError:
                return True

        pending = [track for track in targets if release_lookup_due(track)]
        selected = pending[:limit]
        if not selected:
            return {"attempted": 0, "added": 0, "failed": 0, "remaining": 0}
        yt = self.public_client()
        added = failed = 0
        for track in selected:
            track_id = str(track["track_id"])
            year = self._release_year_for_track(yt, track)
            if year:
                release_cache[track_id] = {
                    "release_year": year,
                    "source": "ytmusicapi.exact_song_album",
                    "confidence": "medium",
                    "matchMethod": "exact_title_artist_song_search",
                    "title": str(track.get("title")),
                    "artist": str(track.get("primary_artist")),
                    "resolvedAt": datetime.now(timezone.utc).isoformat(),
                }
                added += 1
            else:
                release_cache[track_id] = {
                    "release_year": None,
                    "source": "ytmusicapi.exact_song_album",
                    "failureReason": "no_exact_release_year",
                    "retry_after": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
                }
                failed += 1
        return {"attempted": len(selected), "added": added, "failed": failed, "remaining": max(0, len(pending) - len(selected))}

    def _release_year_for_track(self, yt: Any, track: dict[str, Any]) -> int | None:
        title = str(track.get("title") or "").strip()
        artist = str(track.get("primary_artist") or "").strip()
        try:
            results = yt.search(f"{title} {artist}", filter="songs", limit=5)
        except Exception:
            return None
        for candidate in results if isinstance(results, list) else []:
            if not isinstance(candidate, dict) or normalise_track_title(candidate.get("title")) != normalise_track_title(title):
                continue
            candidates = [normalise_artist_name(item.get("name") if isinstance(item, dict) else item) for item in candidate.get("artists") or []]
            if normalise_artist_name(artist) not in candidates:
                continue
            year = release_year_from_payload(candidate)
            if year:
                return year
            album = candidate.get("album") if isinstance(candidate.get("album"), dict) else {}
            browse_id = album.get("id") or album.get("browseId")
            if not browse_id:
                continue
            try:
                year = release_year_from_payload(yt.get_album(str(browse_id)))
            except Exception:
                year = None
            if year:
                return year
        return None

    def search_candidates(self, analysis: dict[str, Any], limit_per_seed: int = 8) -> list[dict[str, Any]]:
        yt = self.client()
        candidates: list[dict[str, Any]] = []
        top_artists = analysis.get("top_artists", [])[:8]
        top_tracks = analysis.get("top_tracks", [])[:5]
        for artist in top_artists:
            name = artist.get("artist")
            if not name:
                continue
            try:
                results = yt.search(str(name), filter="songs", limit=limit_per_seed)
                for item in results:
                    if isinstance(item, dict):
                        item["recommendation_source"] = "same or related artist search"
                        item["seed_artist"] = name
                        candidates.append(item)
            except Exception:
                continue
        for track in top_tracks:
            video_id = track.get("video_id")
            if not video_id:
                continue
            try:
                watch = yt.get_watch_playlist(videoId=video_id, limit=limit_per_seed)
                for item in watch.get("tracks", []) if isinstance(watch, dict) else []:
                    if isinstance(item, dict):
                        item["recommendation_source"] = "watch playlist / similar track"
                        item["seed_track"] = track.get("title")
                        candidates.append(item)
            except Exception:
                continue
        return candidates

    def create_private_playlist(self, title: str, video_ids: list[str]) -> str:
        yt = self.client()
        result = yt.create_playlist(
            title=title,
            description="Created locally by Saville Music Persona from evidence-based recommendations.",
            privacy_status="PRIVATE",
            video_ids=video_ids,
        )
        if isinstance(result, str):
            return result
        raise RuntimeError(f"YouTube Music returned an error while creating the playlist: {result}")


def executable_available(name: str) -> bool:
    return shutil.which(name) is not None


def friendly_auth_error(exc: Exception, is_browser: bool = False) -> str:
    text = str(exc)
    if "Unable to find 'header'" in text and "multiPageMenuRenderer" in text:
        return (
            "YouTube responded, but the account menu did not expose account details. "
            "Saved browser headers may be stale; imported Google Takeout data can still be used."
        )
    if "invalid argument" in text.lower():
        return "Google rejected the OAuth token or client configuration as invalid."
    if is_browser:
        return "YouTube Music rejected the saved browser headers. Copy fresh request headers from a logged-in music.youtube.com tab."
    cleaned = " ".join(text.split())
    if not cleaned:
        return exc.__class__.__name__
    return cleaned[:240]


def duration_from_ytmusic_payload(payload: Any) -> int | None:
    if not isinstance(payload, (dict, list)):
        return None
    stack: list[Any] = [payload]
    seen = 0
    while stack and seen < 1000:
        seen += 1
        item = stack.pop()
        if isinstance(item, dict):
            for key in ("duration_seconds", "durationSeconds", "lengthSeconds", "length_seconds", "duration"):
                seconds = extract_duration_seconds(item.get(key))
                if seconds:
                    return seconds
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return None


def seed_artist_cache_from_library(raw: dict[str, Any], artist_cache: dict[str, Any]) -> int:
    artist_cache = ensure_artist_image_cache_schema(artist_cache)
    seeded = 0
    for artist in raw.get("library_artists") or []:
        if not isinstance(artist, dict):
            continue
        name = artist.get("artist") or artist.get("name")
        thumbnails = artist.get("thumbnails") or []
        if not name or not best_thumbnail(thumbnails):
            continue
        key = str(name).strip()
        artist_id = artist.get("browseId") or artist.get("id")
        cached = artist_cache_lookup(artist_cache, key, artist_id)
        if artist_cache_has_thumbnail(cached):
            continue
        entry = artist_cache_entry(key, artist, artist_id=artist_id, source="ytmusicapi.library_artists")
        artist_cache_set(artist_cache, key, entry, artist_id)
        seeded += 1
    return seeded


def seed_album_cache_from_library(raw: dict[str, Any], album_cache: dict[str, Any]) -> int:
    album_cache = ensure_album_image_cache_schema(album_cache)
    seeded = 0
    for item in raw.get("library_albums") or []:
        if not isinstance(item, dict):
            continue
        album = item.get("title") or item.get("album") or item.get("name")
        artist = primary_album_artist(item)
        browse_id = browse_id_from_payload(item)
        if not album or not browse_id or not album_payload_has_thumbnail(item):
            continue
        cached = album_cache_lookup(album_cache, album_id=browse_id, album=album, artist=artist)
        if album_cache_has_thumbnail(cached):
            continue
        entry = album_cache_entry(str(album), artist, item, album_id=browse_id, source="ytmusicapi.library_albums")
        album_cache_set(album_cache, entry, album_id=browse_id, album=album, artist=artist)
        seeded += 1
    return seeded


def top_artist_targets(raw: dict[str, Any], limit: int = 40, preferred_artists: list[str] | None = None) -> list[tuple[str, str | None]]:
    history = extract_tracks(raw.get("takeout_history")) or extract_tracks(raw.get("history"))
    counts: Counter[str] = Counter()
    ids: dict[str, str] = {}
    for item in history:
        names = [name for name in extract_artist_names(item) if name and name != UNKNOWN_ARTIST]
        artist_ids = extract_artist_ids(item)
        for name in names:
            counts[name] += 1
            if name in artist_ids and name not in ids:
                ids[name] = artist_ids[name]
    ordered: list[str] = []
    seen: set[str] = set()
    for artist in preferred_artists or []:
        name = str(artist or "").strip()
        key = normalise_artist_name(name)
        if key and key not in seen:
            seen.add(key)
            ordered.append(name)
    for artist, _ in counts.most_common(limit):
        key = normalise_artist_name(artist)
        if key and key not in seen:
            seen.add(key)
            ordered.append(artist)
    return [(artist, ids.get(artist)) for artist in ordered[:limit]]


def top_album_targets(raw: dict[str, Any], limit: int = 50, preferred_albums: list[dict[str, Any]] | None = None) -> list[dict[str, str | None]]:
    history = extract_tracks(raw.get("takeout_history")) or extract_tracks(raw.get("history"))
    counts: Counter[tuple[str, str]] = Counter()
    ids: dict[tuple[str, str], str] = {}
    originals: dict[tuple[str, str], tuple[str, str]] = {}

    def remember(album: Any, artist: Any, album_id: Any = None, count: int = 1) -> None:
        album_text = str(album or "").strip()
        artist_text = str(artist or "").strip()
        if not album_text or not artist_text or artist_text == UNKNOWN_ARTIST:
            return
        key = (normalise_album_name(album_text), normalise_artist_name(artist_text))
        if not key[0] or not key[1]:
            return
        counts[key] += count
        originals.setdefault(key, (album_text, artist_text))
        if album_id and key not in ids:
            ids[key] = str(album_id)

    for item in preferred_albums or []:
        if isinstance(item, dict):
            remember(item.get("album") or item.get("title") or item.get("name"), item.get("artist"), item.get("album_id") or item.get("browseId"), count=10_000)

    for item in history:
        album, album_id = extract_album(item)
        if not album:
            continue
        artists = [name for name in extract_artist_names(item) if name and name != UNKNOWN_ARTIST]
        remember(album, artists[0] if artists else None, album_id)

    ordered = sorted(counts, key=lambda key: (-counts[key], originals[key][0].lower(), originals[key][1].lower()))
    return [
        {
            "album": originals[key][0],
            "artist": originals[key][1],
            "album_id": ids.get(key),
        }
        for key in ordered[:limit]
    ]


def artist_cache_has_thumbnail(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return bool(value.get("url") or value.get("thumbnail_url") or best_thumbnail(value.get("thumbnails")))


def artist_cache_has_result(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if artist_cache_has_thumbnail(value):
        return True
    retry_after = value.get("retry_after")
    if not retry_after:
        return False
    try:
        retry_at = datetime.fromisoformat(str(retry_after).replace("Z", "+00:00"))
    except ValueError:
        return True
    return retry_at > datetime.now(timezone.utc)


def album_cache_has_thumbnail(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return bool(resolve_album_image_url(value))


def album_cache_has_result(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if album_cache_has_thumbnail(value):
        return True
    retry_after = value.get("retry_after")
    if not retry_after:
        return False
    try:
        retry_at = datetime.fromisoformat(str(retry_after).replace("Z", "+00:00"))
    except ValueError:
        return True
    return retry_at > datetime.now(timezone.utc)


def artist_payload_has_thumbnail(payload: Any) -> bool:
    return isinstance(payload, dict) and bool(best_thumbnail(payload))


def remove_conflicting_artist_cache_entries(cache: dict[str, Any], artist: str, accepted_browse_id: str) -> int:
    """Discard a stale same-name cache record before applying an exact override."""
    items = cache.get("items")
    if not isinstance(items, dict):
        return 0
    wanted_name = normalise_artist_name(artist)
    removed = 0
    for key, entry in list(items.items()):
        if not isinstance(entry, dict) or entry.get("mediaType") != "artist":
            continue
        entry_name = entry.get("artist") or entry.get("name") or entry.get("entityName")
        entry_id = str(entry.get("browse_id") or entry.get("artist_id") or entry.get("entityId") or "")
        if normalise_artist_name(entry_name) == wanted_name and entry_id != accepted_browse_id:
            items.pop(key, None)
            removed += 1
    return removed


def ensure_artist_cache_alias(entry: dict[str, Any], artist: str) -> int:
    aliases = entry.get("aliases")
    values = [str(value).strip() for value in aliases] if isinstance(aliases, list) else []
    if any(normalise_artist_name(value) == normalise_artist_name(artist) for value in values):
        return 0
    entry["aliases"] = [*values, artist]
    return 1


def album_payload_has_thumbnail(payload: Any) -> bool:
    return isinstance(payload, dict) and bool(best_thumbnail(payload.get("thumbnails") or payload.get("thumbnail") or payload.get("images") or payload.get("image")))


def first_artist_search_result(yt: Any, artist: str) -> dict[str, Any] | None:
    results = yt.search(str(artist), filter="artists", limit=5)
    if not isinstance(results, list):
        return None
    normalised = normalise_artist_name(artist)
    for item in results:
        if not isinstance(item, dict):
            continue
        candidate_names = artist_candidate_names(item)
        if any(normalise_artist_name(candidate) == normalised for candidate in candidate_names):
            return item
    return None


def first_album_search_result(yt: Any, album: str, artist: str) -> dict[str, Any] | None:
    query = " ".join(part for part in (album, artist) if part).strip()
    results = yt.search(query, filter="albums", limit=5)
    if not isinstance(results, list):
        return None
    normalised_album = normalise_album_name(album)
    normalised_artist = normalise_artist_name(artist)
    title_matches: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        if not any(normalise_album_name(candidate) == normalised_album for candidate in album_candidate_names(item)):
            continue
        candidate_artists = [normalise_artist_name(candidate) for candidate in album_candidate_artists(item)]
        if normalised_artist and normalised_artist in candidate_artists:
            return item
        title_matches.append(item)
    # A title-only match is unsafe for common album names.  Only use it where
    # Takeout genuinely has no artist attribution; otherwise leave the cover
    # unresolved rather than showing somebody else's release.
    return title_matches[0] if title_matches and not normalised_artist else None


def artist_cache_entry(artist: str, payload: Any, artist_id: Any = None, source: str = "ytmusicapi.artist_lookup") -> dict[str, Any]:
    if not isinstance(payload, dict):
        return artist_cache_failure_entry(artist, artist_id, "no_exact_artist_match")
    selected = best_thumbnail(payload)
    canonical_name = str(payload.get("artist") or payload.get("name") or artist).strip() or artist
    browse_id = browse_id_from_payload(payload) or artist_id
    fetched_at = datetime.now(timezone.utc).isoformat()
    selected_url = selected.get("url") if selected else None
    entry = {
        "schemaVersion": 2,
        "mediaType": "artist",
        "entityId": browse_id,
        "entityName": canonical_name,
        "artist": canonical_name,
        "canonical_artist": canonical_name,
        "normalisedName": normalise_artist_name(canonical_name),
        "normalised_name": normalise_artist_name(canonical_name),
        "artist_id": browse_id,
        "browse_id": browse_id,
        "channel_id": payload.get("channelId") or payload.get("channel_id"),
        "subscribers": payload.get("subscribers"),
        "aliases": list(dict.fromkeys([artist, *artist_candidate_names(payload)])),
        "thumbnails": [selected] if selected else [],
        "thumbnail_url": selected_url,
        "url": selected_url,
        "thumbnail_width": selected.get("width") if selected else None,
        "thumbnail_height": selected.get("height") if selected else None,
        "source": source,
        "artist_image_source": "spotify_artist_profile" if "spotify" in source else "youtube_artist_profile",
        "resolvedAt": fetched_at,
        "fetched_at": fetched_at,
    }
    if selected:
        entry["last_successful_update_at"] = entry["fetched_at"]
        entry["failureReason"] = None
        entry["failure_reason"] = None
    else:
        entry["failure_reason"] = "missing_thumbnails"
        entry["failureReason"] = "missing_thumbnails"
        entry["retry_after"] = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    return entry


def album_cache_entry(album: str, artist: str, payload: Any, album_id: Any = None, source: str = "ytmusicapi.album_lookup") -> dict[str, Any]:
    if not isinstance(payload, dict):
        return album_cache_failure(album, artist, album_id, "no_exact_album_match")
    selected = best_thumbnail(payload.get("thumbnails") or payload.get("thumbnail") or payload.get("images") or payload.get("image"))
    selected_url = selected.get("url") if selected else None
    browse_id = browse_id_from_payload(payload) or album_id
    entry = album_cache_success_entry(album, artist, payload, selected_url, album_id=browse_id, source=source)
    if selected:
        entry["thumbnails"] = [selected]
        entry["thumbnail_width"] = selected.get("width")
        entry["thumbnail_height"] = selected.get("height")
    return entry


def normalise_artist_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().strip()
    text = re.sub(r"\s*-\s*topic$", "", text)
    text = text.replace("&", " and ")
    text = re.sub(r"[/_.\u00b7\u2022]+", " ", text)
    text = re.sub(r"[^\w\s'-]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalise_track_title(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char)).casefold()
    text = re.sub(r"\s*\((?:official|lyrics?|audio|visuali[sz]er)[^)]*\)", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def artist_candidate_names(payload: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for key in ("artist", "name", "title"):
        if payload.get(key):
            names.append(str(payload[key]).strip())
    aliases = payload.get("aliases") or payload.get("alternateNames") or []
    if isinstance(aliases, str):
        names.append(aliases.strip())
    elif isinstance(aliases, list):
        for alias in aliases:
            if alias:
                names.append(str(alias).strip())
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        normalised = normalise_artist_name(name)
        if normalised and normalised not in seen:
            seen.add(normalised)
            result.append(name)
    return result


def album_candidate_names(payload: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for key in ("title", "album", "name"):
        if payload.get(key):
            names.append(str(payload[key]).strip())
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        normalised = normalise_album_name(name)
        if normalised and normalised not in seen:
            seen.add(normalised)
            result.append(name)
    return result


def album_candidate_artists(payload: dict[str, Any]) -> list[str]:
    artists: list[str] = []
    raw_artists = payload.get("artists") or payload.get("artist")
    if isinstance(raw_artists, list):
        for item in raw_artists:
            if isinstance(item, dict):
                name = item.get("name") or item.get("artist")
            else:
                name = item
            if name:
                artists.append(str(name).strip())
    elif isinstance(raw_artists, dict):
        name = raw_artists.get("name") or raw_artists.get("artist")
        if name:
            artists.append(str(name).strip())
    elif isinstance(raw_artists, str):
        artists.append(raw_artists.strip())
    result: list[str] = []
    seen: set[str] = set()
    for artist in artists:
        normalised = normalise_artist_name(artist)
        if normalised and normalised not in seen:
            seen.add(normalised)
            result.append(artist)
    return result


def primary_album_artist(payload: dict[str, Any]) -> str:
    artists = album_candidate_artists(payload)
    return artists[0] if artists else UNKNOWN_ARTIST


def browse_id_from_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("browseId") or payload.get("artist_id") or payload.get("id")
    return str(value) if value else None


def artist_cache_failure_entry(artist: str, artist_id: Any, reason: str) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    return {
        "schemaVersion": 2,
        "mediaType": "artist",
        "entityId": artist_id,
        "entityName": artist,
        "artist": artist,
        "canonical_artist": artist,
        "normalisedName": normalise_artist_name(artist),
        "normalised_name": normalise_artist_name(artist),
        "artist_id": artist_id,
        "browse_id": artist_id,
        "thumbnails": [],
        "thumbnail_url": None,
        "url": None,
        "thumbnail_width": None,
        "thumbnail_height": None,
        "source": "ytmusicapi.artist_lookup",
        "artist_image_source": "youtube_artist_profile",
        "resolvedAt": fetched_at,
        "fetched_at": fetched_at,
        "failureReason": reason,
        "failure_reason": reason,
        "retry_after": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
    }


def _duration_targets(normalised: dict[str, Any], duration_cache: dict[str, Any], limit: int) -> list[str]:
    plays = Counter(
        str(event.get("video_id"))
        for event in [*(normalised.get("play_events") or []), *(normalised.get("excluded_play_events") or [])]
        if isinstance(event, dict) and event.get("video_id")
    )
    unknown_video_ids = {
        str(event.get("video_id"))
        for event in normalised.get("excluded_play_events") or []
        if isinstance(event, dict) and event.get("video_id") and event.get("music_classification") == "unknown"
    }
    now = datetime.now(timezone.utc)
    candidates: list[tuple[int, str]] = []
    for track in normalised.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        video_id = str(track.get("video_id") or "").strip()
        cached = duration_cache.get(video_id)
        needs_duration = not extract_duration_seconds(track.get("duration_seconds")) and _duration_cache_needs_refresh(cached, now)
        needs_music_identity = video_id in unknown_video_ids and _music_identity_needs_refresh(cached, now)
        if not video_id or not (needs_duration or needs_music_identity):
            continue
        candidates.append((plays.get(video_id, 0), video_id))
    candidates.sort(key=lambda candidate: (-candidate[0], candidate[1]))
    return [video_id for _, video_id in candidates[:limit]]


def _duration_cache_needs_refresh(entry: Any, now: datetime) -> bool:
    if not isinstance(entry, dict):
        return True
    if extract_duration_seconds(entry.get("duration_seconds")):
        expires_at = _parse_cache_timestamp(entry.get("expires_at"))
        return expires_at is not None and expires_at <= now
    retry_at = _parse_cache_timestamp(entry.get("next_retry_at"))
    return retry_at is None or retry_at <= now


def _music_identity_needs_refresh(entry: Any, now: datetime) -> bool:
    """Respect a separate retry window when an exact video is not confirmed as music."""
    if not isinstance(entry, dict):
        return True
    if entry.get("music_classification"):
        return False
    retry_at = _parse_cache_timestamp(entry.get("music_classification_next_retry_at") or entry.get("next_retry_at"))
    return retry_at is None or retry_at <= now


def _parse_cache_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _set_duration_cache_success(
    cache: dict[str, Any],
    video_id: str,
    seconds: int,
    source: str,
    now: datetime,
    *,
    music_classification: str | None = None,
    media_title: Any = None,
    media_author: Any = None,
    identity_confidence: str | None = None,
) -> None:
    existing = cache.get(video_id) if isinstance(cache.get(video_id), dict) else {}
    cache[video_id] = {
        **existing,
        "duration_seconds": seconds,
        "duration_source": source,
        "duration_confidence": "high",
        "status": "resolved",
        "fetched_at": now.isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
        "next_retry_at": None,
        "schema_version": 2,
        "music_classification": music_classification or existing.get("music_classification"),
        "music_classification_source": source if music_classification else existing.get("music_classification_source"),
        "music_classification_status": "confirmed_music" if music_classification else "not_confirmed",
        "music_classification_checked_at": now.isoformat(),
        "music_classification_next_retry_at": None if music_classification else (now + timedelta(days=30)).isoformat(),
        "media_title": str(media_title).strip() if media_title else existing.get("media_title"),
        "media_author": str(media_author).strip() if media_author else existing.get("media_author"),
        "identity_confidence": identity_confidence if music_classification else existing.get("identity_confidence"),
    }


def _set_duration_cache_retry(cache: dict[str, Any], video_id: str, source: str, now: datetime) -> None:
    existing = cache.get(video_id) if isinstance(cache.get(video_id), dict) else {}
    existing_seconds = extract_duration_seconds(existing.get("duration_seconds"))
    cache[video_id] = {
        **existing,
        "duration_seconds": existing_seconds,
        "duration_source": existing.get("duration_source") if existing_seconds else source,
        "duration_confidence": existing.get("duration_confidence") if existing_seconds else "missing",
        "status": "resolved" if existing_seconds else "transient_error",
        "fetched_at": now.isoformat(),
        "next_retry_at": (now + timedelta(hours=6)).isoformat(),
        "music_classification_checked_at": now.isoformat(),
        "music_classification_next_retry_at": (now + timedelta(hours=6)).isoformat(),
        "schema_version": 2,
    }


def _parse_iso8601_duration(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?", value)
    if not match:
        return None
    units = {name: int(match.group(name) or 0) for name in ("days", "hours", "minutes", "seconds")}
    total = units["days"] * 86400 + units["hours"] * 3600 + units["minutes"] * 60 + units["seconds"]
    return total or None


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]
