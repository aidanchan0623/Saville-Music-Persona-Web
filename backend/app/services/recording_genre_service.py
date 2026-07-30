from __future__ import annotations

import math
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx

from app.analysis.taste_model import has_usable_artist, primary_genre_for_profile, profile_for_artist, source_genres_for_artist
from app.analysis.track_metadata import cache_track_metadata, display_recording_title, version_signature
from app.data.genre_taxonomy import normalise_external_genres
from app.database.recording_catalog import RecordingCatalog, base_title, modifiers_for, normalise_recording_text
from app.services.genre_enrichment_service import MUSICBRAINZ_API_URL, lucene_phrase


RECORDING_LOOKUP_TTL_DAYS = 30


class MusicBrainzRecordingGenreService:
    """Track-level fallback used only after Saville's fast genre pass fails."""

    def __init__(self, request_interval_seconds: float = 1.05, timeout_seconds: float = 12.0) -> None:
        self.request_interval_seconds = request_interval_seconds
        self.timeout_seconds = timeout_seconds
        self._last_request_at = 0.0

    def enrich(
        self,
        normalised: dict[str, Any],
        catalog: RecordingCatalog,
        *,
        limit: int,
        deadline: float,
        metadata_cache: dict[str, Any] | None = None,
        on_update: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        catalog.sync_normalised(normalised)
        candidates = unresolved_track_play_counts(normalised, catalog)
        attempted = matched = applied = failed = metadata_added = 0
        provider_error: str | None = None
        with httpx.Client(timeout=self.timeout_seconds) as client:
            for track, plays in candidates[: max(0, limit)]:
                try:
                    self._check_deadline(deadline)
                    result = self.resolve_recording(client, track, deadline)
                except TimeoutError:
                    provider_error = "musicbrainz_time_limit_reached"
                    break
                except httpx.HTTPError:
                    failed += 1
                    provider_error = "musicbrainz_temporarily_unavailable"
                    break
                attempted += 1
                recording_id = str(track.get("recording_id") or "")
                lookup_key = lookup_key_for(track)
                if not result or not recording_id:
                    failed += 1
                    catalog.record_failure(lookup_key, recording_id or None, "musicbrainz", "not_found_or_ambiguous", retry_after())
                    continue
                if result["identityConfidence"] >= 0.85:
                    catalog.add_identifier(recording_id, "musicbrainz_recording_id", result["providerRecordingId"], result["identityConfidence"])
                    for isrc in result["isrcs"]:
                        catalog.add_identifier(recording_id, "isrc", isrc, result["identityConfidence"])
                assignment: dict[str, Any] = {}
                if result.get("primaryGenre"):
                    catalog.save_evidence(
                        recording_id,
                        provider="musicbrainz",
                        provider_recording_id=result["providerRecordingId"],
                        raw_genres=result["rawGenres"],
                        identity_confidence=result["identityConfidence"],
                        evidence_confidence=result["evidenceConfidence"],
                    )
                    assignment = catalog.save_assignment(
                        recording_id,
                        primary_genre=result["primaryGenre"],
                        secondary_genres=result["secondaryGenres"],
                        identity_confidence=result["identityConfidence"],
                        evidence_confidence=result["evidenceConfidence"],
                        normalisation_confidence=result["normalisationConfidence"],
                        source_summary=["musicbrainz_recording"],
                    )
                if metadata_cache is not None and result["identityConfidence"] >= 0.85 and any(
                    result.get(field) for field in ("album", "releaseYear", "coverArtUrl")
                ):
                    cache_track_metadata(
                        metadata_cache,
                        {
                            "status": "resolved",
                            "title": display_recording_title(track.get("title"), track.get("primary_artist")),
                            "primary_artist": track.get("primary_artist"),
                            "artists": [
                                str(value).strip()
                                for value in (track.get("artists") or [track.get("primary_artist")])
                                if str(value or "").strip()
                            ],
                            "album": result.get("album"),
                            "album_id": result.get("releaseGroupId"),
                            "album_art_url": result.get("coverArtUrl"),
                            "album_art_source": "cover_art_archive" if result.get("coverArtUrl") else None,
                            "original_release_year": result.get("releaseYear"),
                            "album_release_year": result.get("releaseYear"),
                            "identity_confidence": result["identityConfidence"],
                            "match_confidence": result["identityConfidence"],
                            "release_year_confidence": "high",
                            "match_method": "exact_musicbrainz_recording",
                            "source": "musicbrainz.recording",
                            "version_signature": list(version_signature(track.get("title"))),
                            "musicbrainz_recording_id": result["providerRecordingId"],
                        },
                        video_id=track.get("video_id"),
                    )
                    metadata_added += 1
                matched += 1
                if assignment.get("autoApplied"):
                    applied += plays
                if on_update:
                    on_update()
        catalog.sync_normalised(normalised)
        return {
            "recordingAttempted": attempted,
            "recordingMatched": matched,
            "recordingAppliedEventCount": applied,
            "recordingFailed": failed,
            "recordingRemainingCandidates": max(0, len(candidates) - attempted),
            "recordingProviderError": provider_error,
            "recordingMetadataAdded": metadata_added,
        }

    def resolve_recording(self, client: httpx.Client, track: dict[str, Any], deadline: float) -> dict[str, Any] | None:
        title = str(track.get("title") or "").strip()
        artist = str(track.get("primary_artist") or "").strip()
        if not title or not has_usable_artist(artist):
            return None
        search = self._get_json(
            client,
            f"{MUSICBRAINZ_API_URL}/recording/",
            {"query": f'recording:"{lucene_phrase(base_title(title))}" AND artist:"{lucene_phrase(artist)}"', "fmt": "json", "limit": 8},
            deadline,
        )
        candidates = exact_recording_candidates(search.get("recordings"), track)
        if len(candidates) != 1:
            return None
        candidate, identity_confidence = candidates[0]
        recording_id = str(candidate.get("id") or "")
        if not recording_id:
            return None
        detail = self._get_json(
            client,
            f"{MUSICBRAINZ_API_URL}/recording/{recording_id}",
            {"inc": "genres+tags+isrcs+releases+release-groups", "fmt": "json"},
            deadline,
        )
        genres = _ordered_labels(detail.get("genres"))
        tags = _ordered_labels(detail.get("tags"))
        raw_genres = list(dict.fromkeys([*genres, *tags]))
        taxonomy = normalise_external_genres(raw_genres)
        release_metadata = _release_metadata(detail.get("releases"))
        cover_art_url = self._cover_art_url(client, release_metadata.get("releaseGroupId"), deadline)
        if not taxonomy and not release_metadata:
            return None
        evidence_confidence = 0.92 if genres else 0.82 if tags else 0.0
        return {
            "providerRecordingId": recording_id,
            "isrcs": [str(value).strip() for value in detail.get("isrcs") or [] if str(value).strip()],
            "rawGenres": raw_genres,
            "primaryGenre": taxonomy.primary_genre if taxonomy else None,
            "secondaryGenres": list(taxonomy.secondary_genres) if taxonomy else [],
            "identityConfidence": identity_confidence,
            "evidenceConfidence": evidence_confidence,
            "normalisationConfidence": taxonomy.normalisation_confidence if taxonomy else 0.0,
            **release_metadata,
            "coverArtUrl": cover_art_url,
        }

    def _cover_art_url(self, client: httpx.Client, release_group_id: Any, deadline: float) -> str | None:
        value = str(release_group_id or "").strip()
        if not value:
            return None
        self._check_deadline(deadline)
        try:
            response = client.get(
                f"https://coverartarchive.org/release-group/{value}",
                headers={"User-Agent": "SavilleMusicPersona/0.4 (https://github.com/aidanchan0623/Saville-Music-Persona-Web)"},
            )
            self._last_request_at = time.monotonic()
            if response.status_code == 404:
                return None
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        images = payload.get("images") if isinstance(payload, dict) else None
        if not isinstance(images, list):
            return None
        preferred = next((item for item in images if isinstance(item, dict) and item.get("front")), None)
        selected = preferred or next((item for item in images if isinstance(item, dict)), None)
        if not isinstance(selected, dict):
            return None
        thumbnails = selected.get("thumbnails") if isinstance(selected.get("thumbnails"), dict) else {}
        return str(thumbnails.get("500") or thumbnails.get("large") or selected.get("image") or "").strip() or None

    def _get_json(self, client: httpx.Client, url: str, params: dict[str, Any], deadline: float) -> dict[str, Any]:
        response: httpx.Response | None = None
        for attempt in range(3):
            self._wait(deadline)
            response = client.get(
                url,
                params=params,
                headers={"User-Agent": "SavilleMusicPersona/0.4 (https://github.com/aidanchan0623/Saville-Music-Persona-Web)"},
            )
            self._last_request_at = time.monotonic()
            if response.status_code not in {429, 503} or attempt == 2:
                break
            delay = float(2 ** (attempt + 1))
            if time.monotonic() + delay > deadline:
                raise TimeoutError
            time.sleep(delay)
        if response is None:
            raise httpx.RequestError("MusicBrainz request failed", request=httpx.Request("GET", url))
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _wait(self, deadline: float) -> None:
        remaining = self.request_interval_seconds - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            if time.monotonic() + remaining > deadline:
                raise TimeoutError
            time.sleep(remaining)
        self._check_deadline(deadline)

    @staticmethod
    def _check_deadline(deadline: float) -> None:
        if time.monotonic() > deadline:
            raise TimeoutError


def _release_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, list):
        return {}
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for row in value:
        if not isinstance(row, dict):
            continue
        date_value = str(row.get("date") or "")
        match = next((part for part in date_value.split("-") if len(part) == 4 and part.isdigit()), None)
        year = int(match) if match else 0
        if year < 1900 or year > datetime.now(timezone.utc).year + 1:
            continue
        release_group = row.get("release-group") if isinstance(row.get("release-group"), dict) else {}
        primary_type = str(release_group.get("primary-type") or "").casefold()
        type_rank = {"album": 0, "single": 1, "ep": 2}.get(primary_type, 3)
        candidates.append((year, type_rank, row))
    if not candidates:
        return {}
    _, _, selected = min(candidates, key=lambda item: (item[0], item[1]))
    release_group = selected.get("release-group") if isinstance(selected.get("release-group"), dict) else {}
    return {
        "releaseYear": min(item[0] for item in candidates),
        "album": str(selected.get("title") or release_group.get("title") or "").strip() or None,
        "releaseGroupId": str(release_group.get("id") or "").strip() or None,
    }


def unresolved_track_play_counts(normalised: dict[str, Any], catalog: RecordingCatalog) -> list[tuple[dict[str, Any], int]]:
    tracks = {str(track.get("track_id")): track for track in normalised.get("tracks") or [] if isinstance(track, dict)}
    metadata = normalised.get("artist_metadata") if isinstance(normalised.get("artist_metadata"), dict) else {}
    counts: Counter[str] = Counter()
    blocked_lookup_keys = catalog.blocked_lookup_keys()
    for event in normalised.get("play_events") or []:
        if isinstance(event, dict) and event.get("track_id") in tracks:
            counts[str(event["track_id"])] += 1
    result: list[tuple[dict[str, Any], int]] = []
    for track_id, plays in counts.most_common():
        track = tracks[track_id]
        artist = str(track.get("primary_artist") or "")
        if not has_usable_artist(artist) or not track.get("recording_id"):
            continue
        if lookup_key_for(track) in blocked_lookup_keys:
            continue
        genres = source_genres_for_artist(track, metadata, artist)
        if primary_genre_for_profile(profile_for_artist(artist, genres)):
            continue
        result.append((track, plays))
    return result


def exact_recording_candidates(value: Any, track: dict[str, Any]) -> list[tuple[dict[str, Any], float]]:
    if not isinstance(value, list):
        return []
    target_title = base_title(track.get("title"))
    target_artist = normalise_recording_text(track.get("primary_artist"))
    target_versions, _ = modifiers_for(track.get("title"))
    duration = _int_or_none(track.get("duration_seconds"))
    target_album = normalise_recording_text(track.get("album"))
    matches: list[tuple[dict[str, Any], float]] = []
    for candidate in value:
        if not isinstance(candidate, dict) or int(candidate.get("score") or 0) < 95:
            continue
        if base_title(candidate.get("title")) != target_title:
            continue
        credits = candidate.get("artist-credit") or []
        artist_names = {
            normalise_recording_text((credit.get("artist") or {}).get("name") or credit.get("name"))
            for credit in credits
            if isinstance(credit, dict)
        }
        if target_artist not in artist_names:
            continue
        candidate_versions, _ = modifiers_for(candidate.get("title"))
        if tuple(candidate_versions) != tuple(target_versions):
            continue
        candidate_duration = round(int(candidate.get("length") or 0) / 1000) or None
        release_titles = {
            normalise_recording_text(release.get("title"))
            for release in candidate.get("releases") or []
            if isinstance(release, dict) and release.get("title")
        }
        album_match = bool(target_album and target_album in release_titles)
        if duration and candidate_duration:
            delta = abs(duration - candidate_duration)
            if delta > 5:
                continue
            confidence = 0.95 if album_match and delta <= 2 else 0.92 if delta <= 2 else 0.88
        elif album_match:
            confidence = 0.86
        else:
            # Title and artist alone are inspectable evidence, but are below
            # the automatic-application threshold by design.
            confidence = 0.65
        matches.append((candidate, confidence))
    # Several MusicBrainz releases can point to the same recording MBID.
    unique = {str(candidate.get("id")): (candidate, confidence) for candidate, confidence in matches if candidate.get("id")}
    return list(unique.values())


def _ordered_labels(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    rows = sorted(
        (item for item in value if isinstance(item, dict) and item.get("name")),
        key=lambda item: int(item.get("count") or 0),
        reverse=True,
    )
    if not rows:
        return []
    top = int(rows[0].get("count") or 0)
    minimum = max(1, math.ceil(top * 0.1))
    return [str(item["name"]).strip() for item in rows if int(item.get("count") or 0) >= minimum][:12]


def lookup_key_for(track: dict[str, Any]) -> str:
    return f"recording:{normalise_recording_text(track.get('primary_artist'))}:{base_title(track.get('title'))}"


def retry_after() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=RECORDING_LOOKUP_TTL_DAYS)).isoformat()


def _int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
