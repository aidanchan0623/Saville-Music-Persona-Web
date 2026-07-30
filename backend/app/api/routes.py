from __future__ import annotations

import copy
import shutil
import hashlib
import json
import time
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import RedirectResponse

from app.analysis.duration import annotate_normalised_durations
from app.analysis.demo_data import demo_raw_collection
from app.analysis.insights import insights_payload
from app.analysis.media import ensure_album_image_cache_schema, ensure_artist_image_cache_schema
from app.analysis.music_character import MUSIC_CHARACTER_CLASSIFIER_VERSION, character_payload
from app.analysis.musical_age import MUSICAL_AGE_CALCULATION_VERSION
from app.analysis.normalizer import NORMALISED_DATA_SCHEMA_VERSION, apply_release_year_cache, normalise_collection
from app.analysis.track_metadata import apply_track_metadata_cache, ensure_track_metadata_cache
from app.analysis.period_profile import ANALYTICS_VERSION, GENRE_MAP_VERSION, build_period_profile
from app.models.listening_event import LISTENING_EVENT_SCHEMA_VERSION
from app.analysis.overview import (
    OVERVIEW_LANGUAGE_CACHE_VERSION,
    OVERVIEW_SCHEMA_VERSION,
    apply_overview_language,
    build_overview_response,
    overview_language_evidence,
    overview_language_fingerprint,
)
from app.analysis.periods import (
    album_songs_payload,
    albums_payload,
    artist_songs_payload,
    filter_events,
    listening_minutes_payload,
    normalised_for_events,
    resolve_period,
    serialise_spec,
    taste_dna_comparison_payload,
    taste_dna_payload,
    top_payload,
)
from app.analysis.scoring import build_analysis
from app.analysis.persona_report import build_persona_report_evidence, compose_persona_report
from app.analysis.spotify_adapter import SPOTIFY_HISTORY_NOTE, SPOTIFY_LIMITATION_NOTE, spotify_raw_to_collection
from app.config import settings
from app.database.repository import JsonRepository
from app.database.recording_catalog import RecordingCatalog
from app.schemas.responses import (
    AuthStatusResponse,
    InsightsResponse,
    OverviewAnalysisResponse,
    PlaylistCreateRequest,
    PlaylistCreateResponse,
    PrerequisiteItem,
    PrerequisitesResponse,
    PersonaReportResponse,
    RefreshRequest,
    RefreshQueuedResponse,
    RefreshStatusResponse,
    DurationEnrichmentStatusResponse,
    GenreEnrichmentStatusResponse,
    ReportRequest,
    SessionDeleteResponse,
    SessionStatusResponse,
    TakeoutImportQueuedResponse,
    TakeoutImportStatusResponse,
)
from app.schemas.contracts import (
    API_SCHEMA_VERSION,
    AnalyticsEnvelope,
    ContractDataQuality,
    ContractPeriod,
    ContractProvenance,
    ContractWarning,
    RecommendationsContractData,
    Top10ContractData,
)
from app.services.duration_enrichment_jobs import (
    DurationEnrichmentAlreadyRunning,
    DurationEnrichmentCoordinator,
)
from app.services.genre_enrichment_jobs import (
    GenreEnrichmentAlreadyRunning,
    GenreEnrichmentCoordinator,
)
from app.services.genre_enrichment_service import (
    MusicBrainzGenreService,
    apply_genre_cache,
    ensure_genre_cache,
    seed_cache_from_source,
)
from app.services.ollama_service import OllamaService
from app.services.recording_genre_service import MusicBrainzRecordingGenreService
from app.services.refresh_jobs import RefreshAlreadyRunning, RefreshCoordinator
from app.services.recommendations import generate_recommendations
from app.services.spotify_service import SpotifyService
from app.services.spotify_history_service import (
    SPOTIFY_HISTORY_PARSER_SCHEMA_VERSION,
    SpotifyHistoryParseError,
    parse_spotify_history_file,
)
from app.services.takeout_import_jobs import (
    ImportCapacity,
    TakeoutImportAlreadyRunning,
    TakeoutImportCapacityReached,
    TakeoutImportCoordinator,
    TakeoutImportTimedOut,
)
from app.services.session_cleanup import SessionCleanupService
from app.services.takeout_service import (
    TAKEOUT_PARSER_SCHEMA_VERSION,
    TakeoutParseError,
    parse_takeout_file,
)
from app.services.ytmusic_service import YTMusicService
from app.session import current_session_id, current_session_namespace, is_shared_cache_key


router = APIRouter(prefix="/api")
repo = JsonRepository(
    settings.db_path,
    namespace_resolver=current_session_namespace if settings.anonymous_mode else None,
    shared_key_predicate=is_shared_cache_key if settings.anonymous_mode else None,
)
ytmusic = YTMusicService(settings)
ollama = OllamaService(settings)
spotify = SpotifyService(settings)
import_capacity = ImportCapacity(settings.anonymous_max_concurrent_imports) if settings.anonymous_mode else None
takeout_imports = TakeoutImportCoordinator(
    repo,
    settings.takeout_import_timeout_seconds,
    capacity=import_capacity,
)
spotify_history_imports = TakeoutImportCoordinator(
    repo,
    settings.takeout_import_timeout_seconds,
    job_prefix="spotify_history_import_job:",
    source_label="Spotify history",
    capacity=import_capacity,
)
duration_enrichment = DurationEnrichmentCoordinator(repo, settings.duration_enrichment_timeout_seconds)
genre_enrichment_service = MusicBrainzGenreService()
recording_genre_enrichment_service = MusicBrainzRecordingGenreService()
genre_enrichment = GenreEnrichmentCoordinator(repo, settings.genre_enrichment_timeout_seconds)
refresh_jobs = RefreshCoordinator(repo, settings.refresh_timeout_seconds)
session_cleanup = SessionCleanupService(
    repo,
    interval_seconds=settings.session_cleanup_interval_seconds,
    upload_ttl_hours=settings.session_ttl_hours,
)


def current_session_cleanup() -> SessionCleanupService:
    """Follow a monkeypatched repository in tests while reusing the production service."""
    global session_cleanup
    if session_cleanup.repo is not repo:
        session_cleanup = SessionCleanupService(
            repo,
            interval_seconds=settings.session_cleanup_interval_seconds,
            upload_ttl_hours=settings.session_ttl_hours,
        )
    return session_cleanup


def enforce_anonymous_upload_limit() -> None:
    if not settings.anonymous_mode:
        return
    allowed, _, retry_after = repo.consume_rate_limit(
        "usage:history_upload",
        limit=settings.anonymous_uploads_per_hour,
        window_seconds=60 * 60,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Upload limit reached",
                "detail": "This private session has reached its hourly upload limit. Try again later.",
                "code": "anonymous_upload_rate_limited",
            },
            headers={"Retry-After": str(retry_after)},
        )


def current_recording_catalog() -> RecordingCatalog:
    """Follow the active repository so tests and alternate data roots stay isolated."""
    return RecordingCatalog(repo.db_path)


def recording_profile_source(source: str) -> str:
    session_id = current_session_id()
    if settings.anonymous_mode and session_id:
        return f"session:{session_id}:{source}"
    return source


def sync_recording_catalog(normalised: dict[str, Any], source: str = "youtube") -> dict[str, int]:
    return current_recording_catalog().sync_normalised(
        normalised,
        profile_source=recording_profile_source(source),
    )


def require_account_connections() -> None:
    if settings.anonymous_mode:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "Account connections are disabled",
                "detail": "This anonymous deployment accepts YouTube Takeout and Spotify history uploads only.",
                "code": "account_connections_disabled",
            },
        )

PERSONA_REPORT_SCHEMA_VERSION = 8
PERSONA_REPORT_PROMPT_VERSION = 10
PERSONA_REPORT_PERIOD = "rolling_year"
PERSONA_REPORT_PERIODS = {"rolling_year", "this_month"}
OVERVIEW_FALLBACK_CACHE_SECONDS = 300
INSIGHTS_RESPONSE_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}
INSIGHTS_RESPONSE_CACHE_LIMIT = 24
PERIOD_PROFILE_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}
PERIOD_PROFILE_CACHE_LIMIT = 32
OVERVIEW_RESPONSE_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}
OVERVIEW_RESPONSE_CACHE_LIMIT = 16
TAKEOUT_CACHE_METADATA_KEY = "takeout_history_meta"

SPOTIFY_CACHE_KEYS = [
    "spotify_tokens",
    "spotify_profile",
    "spotify_raw",
    "spotify_normalised",
    "spotify_analysis",
    "spotify_last_refresh_meta",
    "spotify_latest_report",
    "spotify_recommendations",
    "spotify_oauth_state",
]


def persona_report_fingerprint(profile: dict[str, Any]) -> str:
    compact = {key: value for key, value in profile.items() if key != "languageEvidence"}
    payload = json.dumps(compact, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def persona_report_cache_key(source: str, mode: str, analytics_fingerprint: str, period: str = PERSONA_REPORT_PERIOD) -> str:
    model_fingerprint = hashlib.sha256(settings.ollama_model.encode("utf-8")).hexdigest()[:8]
    return (
        f"persona_report:{source}:{period}:v{PERSONA_REPORT_SCHEMA_VERSION}:"
        f"analytics{ANALYTICS_VERSION}:genre{GENRE_MAP_VERSION}:"
        f"calc{MUSICAL_AGE_CALCULATION_VERSION}:classifier{MUSIC_CHARACTER_CLASSIFIER_VERSION}:"
        f"prompt{PERSONA_REPORT_PROMPT_VERSION}:"
        f"model{model_fingerprint}:{analytics_fingerprint}:{mode}"
    )


def persona_report_pointer_key(source: str, period: str = PERSONA_REPORT_PERIOD) -> str:
    return f"persona_report_pointer:{source}:{period}:v{PERSONA_REPORT_SCHEMA_VERSION}"


def persona_report_pointer_is_current(pointer: Any, source: str, normalised_updated_at: str | None, period: str = PERSONA_REPORT_PERIOD) -> bool:
    return (
        isinstance(pointer, dict)
        and pointer.get("schemaVersion") == PERSONA_REPORT_SCHEMA_VERSION
        and pointer.get("period") == period
        and pointer.get("source") == source
        and pointer.get("promptVersion") == PERSONA_REPORT_PROMPT_VERSION
        and pointer.get("analyticsVersion") == ANALYTICS_VERSION
        and pointer.get("genreMapVersion") == GENRE_MAP_VERSION
        and pointer.get("musicalAgeCalculationVersion") == MUSICAL_AGE_CALCULATION_VERSION
        and pointer.get("personalityClassifierVersion") == MUSIC_CHARACTER_CLASSIFIER_VERSION
        and pointer.get("model") == settings.ollama_model
        and pointer.get("normalisedUpdatedAt") == normalised_updated_at
        and bool(pointer.get("cacheKey"))
    )


def save_persona_report_pointer(source: str, mode: str, analytics_fingerprint: str, report_cache_key: str, generated_at: str, period: str = PERSONA_REPORT_PERIOD) -> None:
    repo.save_json(
        persona_report_pointer_key(source, period),
        {
            "cacheKey": report_cache_key,
            "source": source,
            "mode": mode,
            "period": period,
            "schemaVersion": PERSONA_REPORT_SCHEMA_VERSION,
            "promptVersion": PERSONA_REPORT_PROMPT_VERSION,
            "analyticsVersion": ANALYTICS_VERSION,
            "genreMapVersion": GENRE_MAP_VERSION,
            "musicalAgeCalculationVersion": MUSICAL_AGE_CALCULATION_VERSION,
            "personalityClassifierVersion": MUSIC_CHARACTER_CLASSIFIER_VERSION,
            "model": settings.ollama_model,
            "analyticsFingerprint": analytics_fingerprint,
            "normalisedUpdatedAt": repo.updated_at(cache_key("normalised", source)),
            "generatedAt": generated_at,
        },
    )

def require_cache(key: str) -> Any:
    if key in {"normalised", "analysis", "recommendations"}:
        validate_takeout_cache_schema()
    value = repo.load_json_cached(key)
    if value is None:
        raise HTTPException(status_code=404, detail={"error": "No data yet", "detail": "Refresh music data first or enable demo data.", "code": "no_cached_data"})
    return value


def normalise_source(source: str | None) -> str:
    value = (source or "youtube").strip().lower()
    if value in {"youtube", "ytmusic", "youtube_music"}:
        return "youtube"
    if value == "spotify":
        return "spotify"
    raise HTTPException(status_code=400, detail={"error": "Unknown music source", "detail": "Use source=youtube or source=spotify.", "code": "unknown_source"})


def cache_key(key: str, source: str | None = "youtube") -> str:
    return key if normalise_source(source) == "youtube" else f"spotify_{key}"


def require_source_cache(key: str, source: str | None = "youtube") -> Any:
    resolved_source = normalise_source(source)
    if resolved_source == "youtube":
        return require_cache(key)
    value = repo.load_json_cached(cache_key(key, resolved_source))
    if value is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "No Spotify data yet",
                "detail": "Connect Spotify in Settings, then refresh Spotify data.",
                "code": "no_spotify_data",
            },
        )
    return value


def load_current_takeout_history() -> list[dict[str, Any]] | None:
    validate_takeout_cache_schema()
    history = repo.load_json("takeout_history")
    return history if isinstance(history, list) and history else None


def validate_takeout_cache_schema() -> None:
    if repo.updated_at("takeout_history") is None:
        return
    metadata = repo.load_json(TAKEOUT_CACHE_METADATA_KEY)
    status = takeout_reimport_status(metadata)
    if status["requiresReimport"]:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Google Takeout data needs to be re-imported",
                "detail": "The listening-event schema changed. Re-upload the Takeout JSON, HTML, or ZIP so analytics can be rebuilt accurately.",
                "code": "takeout_event_schema_outdated",
            },
        )


def takeout_reimport_status(metadata: Any) -> dict[str, Any]:
    stored = metadata if isinstance(metadata, dict) else {}
    parser = stored.get("parser_schema_version")
    event = stored.get("event_schema_version")
    data = stored.get("data_schema_version")
    required = (
        parser != TAKEOUT_PARSER_SCHEMA_VERSION
        or event != LISTENING_EVENT_SCHEMA_VERSION
        or data != NORMALISED_DATA_SCHEMA_VERSION
    )
    return {
        "requiresReimport": required,
        "storedParserVersion": parser,
        "currentParserVersion": TAKEOUT_PARSER_SCHEMA_VERSION,
        "storedEventSchemaVersion": event,
        "currentEventSchemaVersion": LISTENING_EVENT_SCHEMA_VERSION,
        "storedDataSchemaVersion": data,
        "currentDataSchemaVersion": NORMALISED_DATA_SCHEMA_VERSION,
    }


def ensure_youtube_artist_images() -> None:
    analysis = repo.load_json("analysis")
    normalised_cache = repo.load_json("normalised")
    missing_artists = missing_artist_image_names(analysis, normalised_cache)
    if not missing_artists:
        return
    raw = repo.load_json("raw")
    if not isinstance(raw, dict):
        return
    warnings: list[str] = []
    normalised = normalise_with_duration_cache(raw, warnings, allow_artist_image_enrichment=True, preferred_artist_images=missing_artists)
    refreshed_at = (repo.load_json("last_refresh_meta") or {}).get("refreshed_at") or datetime.now(timezone.utc).isoformat()
    normalised["refreshed_at"] = refreshed_at
    normalised = annotate_normalised_durations(normalised, repo.load_json("duration_cache") or {})
    rebuilt = build_analysis(normalised)
    repo.save_json("raw", raw)
    repo.save_json("normalised", normalised)
    repo.save_json("analysis", rebuilt)
    if warnings:
        meta = repo.load_json("last_refresh_meta") or {"refreshed_at": refreshed_at, "use_demo": False, "warnings": []}
        meta["warnings"] = list(dict.fromkeys([*(meta.get("warnings") or []), *warnings]))
        repo.save_json("last_refresh_meta", meta)


def top_artist_images_missing(analysis: Any) -> bool:
    if not isinstance(analysis, dict):
        return False
    top_artists = analysis.get("top_artists") or []
    return any(isinstance(artist, dict) and not artist.get("image") for artist in top_artists[:5])


def missing_artist_image_names(analysis: Any, normalised: Any) -> list[str]:
    names: list[str] = []
    if isinstance(analysis, dict):
        for key, limit in (("top_3_artists", 3), ("top_artists", 8)):
            for artist in (analysis.get(key) or [])[:limit]:
                if isinstance(artist, dict) and artist.get("artist") and not artist.get("image"):
                    names.append(str(artist["artist"]))
    if isinstance(normalised, dict):
        try:
            current = top_payload(normalised, "artists", "this_month", timezone_name=settings.local_timezone)
            for artist in (current.get("items") or [])[:10]:
                if isinstance(artist, dict) and artist.get("artist") and not artist.get("thumbnail"):
                    names.append(str(artist["artist"]))
        except Exception:  # noqa: BLE001
            pass
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        key = " ".join(name.lower().split())
        if key and key not in seen:
            seen.add(key)
            result.append(name)
    return result


def normalise_with_duration_cache(
    raw: dict[str, Any],
    warnings: list[str] | None = None,
    allow_enrichment: bool = False,
    allow_artist_image_enrichment: bool = False,
    allow_album_image_enrichment: bool = False,
    preferred_artist_images: list[str] | None = None,
) -> dict[str, Any]:
    artist_cache = ensure_artist_image_cache_schema(repo.load_json("artist_image_cache_v2") or {})
    album_cache = ensure_album_image_cache_schema(repo.load_json("album_image_cache_v1") or {})
    release_year_cache = repo.load_json("release_year_cache_v1") or {}
    track_metadata_cache = ensure_track_metadata_cache(repo.load_json("track_metadata_cache_v1") or {})
    raw.pop("artist_image_cache", None)
    raw.pop("album_image_cache", None)
    raw["artist_image_cache_v2"] = artist_cache
    raw["album_image_cache_v1"] = album_cache
    raw["release_year_cache_v1"] = release_year_cache if isinstance(release_year_cache, dict) else {}
    raw["track_metadata_cache_v1"] = track_metadata_cache
    repo.delete_json("artist_image_cache")
    repo.delete_json("album_image_cache")
    if artist_cache:
        raw["artist_image_cache_v2"] = artist_cache
    if album_cache:
        raw["album_image_cache_v1"] = album_cache
    if allow_artist_image_enrichment:
        try:
            stats = ytmusic.enrich_artist_image_cache(raw, artist_cache, preferred_artists=preferred_artist_images)
            if stats.get("seeded") or stats.get("attempted") or stats.get("repaired"):
                repo.save_json("artist_image_cache_v2", artist_cache)
                if warnings is not None:
                    warnings.append(
                        f"Artist image cache checked {stats['attempted']} artist(s), added {stats['added']} official image(s), and reused {stats['seeded']} library artist image(s)."
                    )
        except Exception as exc:  # noqa: BLE001
            if warnings is not None:
                warnings.append(f"Artist image enrichment skipped: {exc}")
    normalised = normalise_collection(raw)
    apply_track_metadata_cache(normalised, track_metadata_cache)
    if allow_album_image_enrichment:
        try:
            preferred_albums = preferred_album_image_targets(normalised)
            stats = ytmusic.enrich_album_image_cache(raw, album_cache, preferred_albums=preferred_albums)
            if stats.get("seeded") or stats.get("attempted"):
                repo.save_json("album_image_cache_v1", album_cache)
                normalised = normalise_collection(raw)
                apply_track_metadata_cache(normalised, track_metadata_cache)
                if warnings is not None:
                    warnings.append(
                        f"Album image cache checked {stats['attempted']} album(s), added {stats['added']} official cover(s), and reused {stats['seeded']} library album cover(s)."
                    )
        except Exception as exc:  # noqa: BLE001
            if warnings is not None:
                warnings.append(f"Album image enrichment skipped: {exc}")
    duration_cache = repo.load_json("duration_cache") or {}
    if duration_cache:
        normalised = annotate_normalised_durations(normalised, duration_cache)
    if allow_enrichment:
        try:
            stats = ytmusic.enrich_duration_cache(normalised, duration_cache, settings.duration_enrichment_limit)
            if stats.get("attempted"):
                repo.save_json("duration_cache", duration_cache)
                normalised = annotate_normalised_durations(normalised, duration_cache)
                if warnings is not None:
                    warnings.append(
                        f"Duration enrichment checked {stats['attempted']} track(s), added {stats['added']} usable duration(s), and cached {stats['failed']} unavailable result(s)."
                    )
        except Exception as exc:  # noqa: BLE001
            if warnings is not None:
                warnings.append(f"Duration enrichment skipped: {exc}")
    genre_cache = durable_genre_cache()
    applied_genres = apply_genre_cache(normalised, genre_cache)
    if applied_genres and warnings is not None:
        warnings.append(f"Reapplied durable genre metadata for {applied_genres} artist(s).")
    catalog_stats = sync_recording_catalog(normalised, "youtube")
    if catalog_stats["recordingsLinked"] and warnings is not None:
        warnings.append(
            f"Linked {catalog_stats['recordingsLinked']} track(s) to reusable recording metadata without changing listening-event totals."
        )
    return normalised


def durable_genre_cache() -> dict[str, Any]:
    """Return the canonical genre cache, including exact Spotify catalogue evidence."""
    stored = repo.load_json("genre_metadata_cache")
    prepared = ensure_genre_cache(stored)
    prepared, _ = seed_cache_from_source(
        prepared,
        repo.load_json("spotify_normalised"),
        provider="spotify",
    )
    if prepared != stored:
        repo.save_json("genre_metadata_cache", prepared)
    return prepared


def preferred_album_image_targets(normalised: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for period in ("this_month", "rolling_year"):
        payload = albums_payload(normalised, period=period, timezone_name=settings.local_timezone, limit=10)
        for album in payload.get("albums") or []:
            key = str(album.get("key") or "").strip().casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "album": album.get("album"),
                    "artist": album.get("artist"),
                    "album_id": album.get("album_id"),
                }
            )
    return result


def analysis_for_period(period: str, month: str | None, timezone_name: str | None, source: str | None = "youtube") -> tuple[dict[str, Any], dict[str, Any], int]:
    normalised = require_source_cache("normalised", source)
    spec = resolve_period(normalised, period, month, timezone_name or settings.local_timezone)
    events = filter_events(normalised, spec)
    period_normalised = normalised_for_events(normalised, events, spec)
    return build_analysis(period_normalised), spec, len(events)


def canonical_period_profile(source: str, period: str, month: str | None, timezone_name: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Cache one canonical PeriodProfile for all contract projections in a request cycle."""
    resolved_source = normalise_source(source)
    normalised = require_source_cache("normalised", resolved_source)
    metadata = normalised.get("metadata") or {}
    cache_identity = (
        current_session_namespace(),
        resolved_source,
        period,
        month,
        timezone_name or settings.local_timezone,
        normalised.get("refreshed_at"),
        metadata.get("data_schema_version"),
        metadata.get("play_count"),
        ANALYTICS_VERSION,
        GENRE_MAP_VERSION,
    )
    profile = PERIOD_PROFILE_CACHE.get(cache_identity)
    if profile is None:
        profile = build_period_profile(normalised, period, month, timezone_name or settings.local_timezone)
        if len(PERIOD_PROFILE_CACHE) >= PERIOD_PROFILE_CACHE_LIMIT:
            PERIOD_PROFILE_CACHE.clear()
        PERIOD_PROFILE_CACHE[cache_identity] = profile
    return profile, normalised


def canonical_overview_payload(
    source: str,
    period: str,
    month: str | None,
    timezone_name: str | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    profile, normalised = canonical_period_profile(source, period, month, timezone_name)
    cache_identity = (
        current_session_namespace(),
        normalise_source(source),
        period,
        month,
        timezone_name or settings.local_timezone,
        profile["dataFingerprint"],
        OVERVIEW_SCHEMA_VERSION,
        MUSICAL_AGE_CALCULATION_VERSION,
        MUSIC_CHARACTER_CLASSIFIER_VERSION,
    )
    payload = OVERVIEW_RESPONSE_CACHE.get(cache_identity)
    if payload is None:
        payload = build_overview_response(
            normalised,
            period,
            month,
            timezone_name or settings.local_timezone,
            profile=profile,
        )
        if len(OVERVIEW_RESPONSE_CACHE) >= OVERVIEW_RESPONSE_CACHE_LIMIT:
            OVERVIEW_RESPONSE_CACHE.clear()
        OVERVIEW_RESPONSE_CACHE[cache_identity] = payload
    return copy.deepcopy(payload), profile, normalised


def clear_analytics_memory_caches() -> None:
    INSIGHTS_RESPONSE_CACHE.clear()
    PERIOD_PROFILE_CACHE.clear()
    OVERVIEW_RESPONSE_CACHE.clear()


def analytics_envelope(source: str, profile: dict[str, Any], normalised: dict[str, Any], data: Any) -> AnalyticsEnvelope[Any]:
    figures = profile["figures"]
    metadata = normalised.get("metadata") or {}
    import_meta = repo.load_json(TAKEOUT_CACHE_METADATA_KEY) if source == "youtube" else {}
    reimport = takeout_reimport_status(import_meta) if source == "youtube" else {"requiresReimport": False}
    warnings: list[ContractWarning] = []
    if figures["duration_coverage"] < 100:
        warnings.append(ContractWarning(code="LOW_DURATION_COVERAGE", severity="warning", message="Detected listening time excludes plays without known duration.", affectedFields=["detectedMinutes"]))
    if figures["genre_coverage"] < 50:
        warnings.append(ContractWarning(code="LOW_GENRE_COVERAGE", severity="warning", message="Genre-based insights use only classified listening events.", affectedFields=["genreShares", "musicProfile"]))
    if figures["release_year_coverage"] < 50:
        warnings.append(ContractWarning(code="LOW_RELEASE_YEAR_COVERAGE", severity="info", message="Release-year metadata is incomplete.", affectedFields=["musicalAge"]))
    if reimport["requiresReimport"]:
        status = "stale_import"
        warnings.append(ContractWarning(code="TAKEOUT_REIMPORT_REQUIRED", severity="error", message="This Takeout import uses an incompatible parser or event schema. Re-import it before relying on analytics.", affectedFields=[]))
    elif figures["accepted_play_count"] == 0:
        status = "insufficient_data"
        warnings.append(ContractWarning(code="PROFILE_INSUFFICIENT_DATA", severity="warning", message="No accepted plays are available for this period.", affectedFields=[]))
    else:
        status = "partial" if warnings else "complete"
    period = profile["period"]
    return AnalyticsEnvelope[Any](
        status=status,
        source=source,
        period=ContractPeriod(type=period["period"], month=period.get("month"), start=period["start_date"], end=period["end_date"], timezone=period["timezone"], label=period["label"]),
        provenance=ContractProvenance(
            importBatchId=(import_meta or {}).get("import_batch_id") if isinstance(import_meta, dict) else None,
            dataFingerprint=profile["dataFingerprint"],
            parserVersion=metadata.get("parser_schema_version"),
            eventSchemaVersion=int(metadata.get("listening_event_schema_version") or LISTENING_EVENT_SCHEMA_VERSION),
            analyticsVersion=ANALYTICS_VERSION,
        ),
        dataQuality=ContractDataQuality(
            acceptedPlayCount=figures["accepted_play_count"],
            timestampCoverage=figures["timestamp_coverage"],
            durationCoverage=figures["duration_coverage"],
            genreCoverage=figures["genre_coverage"],
            releaseYearCoverage=figures["release_year_coverage"],
        ),
        warnings=warnings,
        data=data,
    )


def rebuild_spotify_cache() -> dict[str, Any]:
    settings.ensure_local_dirs()
    previous_raw = repo.load_json("spotify_raw") or {}
    raw = spotify.fetch_all(repo)
    if isinstance(previous_raw, dict) and isinstance(previous_raw.get("streaming_history"), list):
        raw["streaming_history"] = previous_raw["streaming_history"]
        raw["spotify_history_import_batch_id"] = previous_raw.get("spotify_history_import_batch_id")
        raw["spotify_history_import_diagnostics"] = previous_raw.get("spotify_history_import_diagnostics")
    repo.save_json("spotify_profile", raw.get("profile") or {})
    collection = spotify_raw_to_collection(raw)
    normalised = normalise_collection(collection)
    genre_cache, _ = seed_cache_from_source(durable_genre_cache(), normalised, provider="spotify")
    apply_genre_cache(normalised, genre_cache)
    sync_recording_catalog(normalised, "spotify")
    refreshed_at = datetime.now(timezone.utc).isoformat()
    normalised["refreshed_at"] = refreshed_at
    normalised = annotate_normalised_durations(normalised, repo.load_json("duration_cache") or {})
    analysis = build_analysis(normalised)
    warnings = list(collection.get("warnings") or [SPOTIFY_LIMITATION_NOTE])
    repo.save_json_batch(
        {
            "spotify_raw": raw,
            "spotify_normalised": normalised,
            "spotify_analysis": analysis,
            "spotify_last_refresh_meta": {"refreshed_at": refreshed_at, "warnings": warnings, "use_demo": False},
            "genre_metadata_cache": genre_cache,
        }
    )
    return {
        "refreshed_at": refreshed_at,
        "warnings": warnings,
        "coverage": analysis["coverage"],
        "track_count": normalised["metadata"]["track_count"],
        "play_count": normalised["metadata"]["play_count"],
        "profile": raw.get("profile") or {},
    }


def quick_youtube_auth_status() -> dict[str, Any]:
    browser_file_exists = settings.ytmusic_browser_auth_file.exists()
    oauth_file_exists = settings.ytmusic_auth_file.exists()
    cached_data_available = repo.load_json("normalised") is not None
    if browser_file_exists:
        auth_file_path = settings.ytmusic_browser_auth_file
    else:
        auth_file_path = settings.ytmusic_auth_file
    if cached_data_available:
        message = "Cached YouTube Music profile is available. Use Recheck Connection to test live YouTube auth."
    elif browser_file_exists or oauth_file_exists:
        message = "Saved YouTube auth file exists. Use Recheck Connection to verify live YouTube access."
    else:
        message = "No YouTube auth file found. Import Google Takeout or set up YouTube Music auth in Settings."
    return {
        "connected": False,
        "auth_file_exists": browser_file_exists or oauth_file_exists,
        "auth_file_path": str(auth_file_path),
        "oauth_client_configured": bool(settings.ytmusic_client_id and settings.ytmusic_client_secret),
        "account_name": None,
        "message": message,
    }


@router.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "app": "Saville Music Persona Web",
        "version": "0.3.0",
        "mode": settings.deployment_mode,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ready")
def readiness() -> dict[str, Any]:
    database_ready = repo.healthcheck()
    frontend_ready = not settings.serve_frontend or (settings.frontend_dist_dir / "index.html").is_file()
    if not database_ready or not frontend_ready:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Hosted runtime is not ready",
                "detail": "Persistent storage or the compiled frontend is unavailable.",
                "code": "runtime_not_ready",
            },
        )
    return {
        "ok": True,
        "database": "writable",
        "frontend": "bundled" if settings.serve_frontend else "external",
        "workerTopology": "single-process",
    }


@router.get("/session", response_model=SessionStatusResponse)
def session_status() -> SessionStatusResponse:
    if not settings.anonymous_mode:
        return SessionStatusResponse(
            mode="local",
            anonymous=False,
            accountConnectionsEnabled=True,
        )
    session_id = current_session_id()
    if not session_id:
        raise HTTPException(status_code=500, detail={"error": "Anonymous session unavailable", "code": "session_unavailable"})
    now = datetime.now(timezone.utc)
    existing = repo.load_json("session_meta")
    created_at = existing.get("createdAt") if isinstance(existing, dict) else now.isoformat()
    stored_expiry = existing.get("expiresAt") if isinstance(existing, dict) else None
    try:
        expires_at = datetime.fromisoformat(str(stored_expiry).replace("Z", "+00:00")) if stored_expiry else now + timedelta(hours=settings.session_ttl_hours)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    except ValueError:
        expires_at = now + timedelta(hours=settings.session_ttl_hours)
    repo.save_json(
        "session_meta",
        {
            "createdAt": created_at,
            "lastSeenAt": now.isoformat(),
            "expiresAt": expires_at.isoformat(),
        },
    )
    return SessionStatusResponse(
        mode="anonymous",
        anonymous=True,
        sessionHint=session_id[-8:],
        expiresAt=expires_at.isoformat(),
        accountConnectionsEnabled=False,
    )


@router.delete("/session", response_model=SessionDeleteResponse)
def delete_session(response: Response) -> SessionDeleteResponse:
    if not settings.anonymous_mode:
        raise HTTPException(
            status_code=403,
            detail={"error": "Session deletion is only available in anonymous mode", "code": "session_delete_unavailable"},
        )
    namespace = current_session_namespace()
    if not namespace:
        raise HTTPException(status_code=500, detail={"error": "Anonymous session unavailable", "code": "session_unavailable"})
    if takeout_imports.active_for_scope(namespace) or spotify_history_imports.active_for_scope(namespace):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Import still running",
                "detail": "Wait for the current import to finish, then delete the session.",
                "code": "session_import_active",
            },
        )
    deleted = current_session_cleanup().purge_namespace(namespace)
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite=settings.session_cookie_samesite,
    )
    return SessionDeleteResponse(
        deleted=True,
        cacheRowsDeleted=deleted["cacheRows"],
        listeningEventsDeleted=deleted["events"],
    )


@router.get("/prerequisites", response_model=PrerequisitesResponse)
def prerequisites() -> PrerequisitesResponse:
    if settings.anonymous_mode:
        return PrerequisitesResponse(
            ok=True,
            items=[PrerequisiteItem(name="Anonymous import service", available=True, detail="Ready")],
            ollama_model="deterministic-fallback",
            ollama_reachable=False,
            model_installed=False,
            local_timezone=settings.local_timezone,
            duration_enrichment_limit=settings.duration_enrichment_limit,
        )
    ollama_status = ollama.status()
    items = [
        PrerequisiteItem(name="Git", available=shutil.which("git") is not None, detail=shutil.which("git") or "git not found on PATH"),
        PrerequisiteItem(name="Node.js", available=shutil.which("node") is not None, detail=shutil.which("node") or "node not found on PATH"),
        PrerequisiteItem(name="npm", available=shutil.which("npm.cmd") is not None or shutil.which("npm") is not None, detail=shutil.which("npm.cmd") or shutil.which("npm") or "npm not found on PATH"),
        PrerequisiteItem(name="Ollama", available=ollama_status["reachable"], detail=ollama_status["message"]),
    ]
    return PrerequisitesResponse(
        ok=all(item.available for item in items[:-1]) and ollama_status["reachable"] and ollama_status["model_installed"],
        items=items,
        ollama_model=settings.ollama_model,
        ollama_reachable=ollama_status["reachable"],
        model_installed=ollama_status["model_installed"],
        local_timezone=settings.local_timezone,
        duration_enrichment_limit=settings.duration_enrichment_limit,
    )


@router.get("/auth/status", response_model=AuthStatusResponse)
def auth_status(live: bool = Query(False)) -> AuthStatusResponse:
    if settings.anonymous_mode:
        return AuthStatusResponse(
            connected=False,
            auth_file_exists=False,
            auth_file_path="",
            oauth_client_configured=False,
            account_name=None,
            message="Anonymous mode accepts Google Takeout uploads without connecting an account.",
            cached_data_available=repo.load_json("normalised") is not None,
            last_refreshed_at=(repo.load_json("last_refresh_meta") or {}).get("refreshed_at"),
        )
    status = ytmusic.auth_status() if live else quick_youtube_auth_status()
    meta = repo.load_json("last_refresh_meta") or {}
    return AuthStatusResponse(
        **status,
        cached_data_available=repo.load_json("normalised") is not None,
        last_refreshed_at=meta.get("refreshed_at"),
    )


@router.post("/auth/setup")
def auth_setup() -> dict[str, Any]:
    require_account_connections()
    return ytmusic.setup_instructions()


@router.get("/spotify/status")
def spotify_status() -> dict[str, Any]:
    status = spotify.status(repo)
    if settings.anonymous_mode:
        status.update(
            {
                "configured": False,
                "connected": False,
                "display_name": None,
                "profile_image": None,
                "spotify_user_id": None,
                "message": "Anonymous mode accepts Spotify streaming-history uploads without connecting an account.",
            }
        )
    return status


@router.get("/spotify/health")
def spotify_health() -> dict[str, Any]:
    return {"ok": True, "spotify_router": "registered"}


@router.get("/spotify/login")
def spotify_login() -> RedirectResponse:
    require_account_connections()
    state = spotify.new_state()
    repo.save_json("spotify_oauth_state", {"state": state, "created_at": datetime.now(timezone.utc).isoformat()})
    try:
        return RedirectResponse(spotify.login_url(state))
    except RuntimeError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                "Spotify is not configured. Set SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, "
                "and SPOTIFY_REDIRECT_URI in backend/private/.env."
            ),
        ) from exc


@router.get("/spotify/callback")
def spotify_callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
) -> RedirectResponse:
    require_account_connections()
    if error:
        return RedirectResponse(f"{settings.frontend_url}/settings?source=spotify&spotify_error={error}")
    if not code:
        raise HTTPException(status_code=400, detail={"error": "Spotify callback failed", "detail": "Spotify did not return an authorization code.", "code": "spotify_missing_code"})
    stored_state = repo.load_json("spotify_oauth_state") or {}
    if stored_state.get("state") and state != stored_state.get("state"):
        raise HTTPException(status_code=400, detail={"error": "Spotify callback failed", "detail": "OAuth state did not match.", "code": "spotify_state_mismatch"})
    tokens = spotify.exchange_code(code)
    repo.save_json("spotify_tokens", tokens)
    repo.delete_json("spotify_oauth_state")
    try:
        rebuild_spotify_cache()
    except Exception as exc:  # noqa: BLE001
        repo.save_json(
            "spotify_last_refresh_meta",
            {
                "refreshed_at": datetime.now(timezone.utc).isoformat(),
                "warnings": [f"Spotify connected, but initial data refresh failed: {exc}"],
                "use_demo": False,
            },
        )
    return RedirectResponse(f"{settings.frontend_url}/settings?source=spotify&spotify_connected=1")


@router.post("/spotify/disconnect")
def spotify_disconnect() -> dict[str, Any]:
    require_account_connections()
    repo.delete_json_many(SPOTIFY_CACHE_KEYS)
    return {"connected": False, "message": "Spotify disconnected. YouTube Music and Google Takeout data were left untouched."}


@router.post("/spotify/refresh")
def spotify_refresh() -> dict[str, Any]:
    require_account_connections()
    try:
        return rebuild_spotify_cache()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail={"error": "Spotify refresh failed", "detail": str(exc), "code": "spotify_refresh_failed"}) from exc


def process_refresh(options: dict[str, bool], coordinator: RefreshCoordinator, deadline: float) -> None:
    settings.ensure_local_dirs()
    use_demo = bool(options.get("use_demo"))
    enrich_durations = bool(options.get("enrich_durations"))
    warnings: list[str] = []
    coordinator.stage("fetching", "Loading local listening sources...")
    if use_demo:
        raw = demo_raw_collection()
        warnings.append("Demo data is enabled; no private account data was fetched.")
        live_connected = False
    else:
        takeout_history = load_current_takeout_history()
        status = ytmusic.auth_status()
        live_connected = bool(status["connected"])
        if not status["connected"] and not takeout_history:
            coordinator.fail(f"YouTube Music is not connected: {status['message']}", "ytmusic_not_connected")
            return
        if status["connected"]:
            raw = ytmusic.fetch_library()
            warnings.extend(raw.get("warnings") or [])
            ytmusic.save_raw_snapshot(settings.raw_dir, raw)
        else:
            raw = {"source": "google_takeout", "history": [], "warnings": []}
            warnings.append(f"Live YouTube Music sync skipped: {status['message']}")
    coordinator.check_timeout(deadline)
    coordinator.stage("normalizing", "Merging listening events into the canonical local profile...", warnings=warnings)
    takeout_history = None if use_demo else load_current_takeout_history()
    if takeout_history:
        raw["takeout_history"] = takeout_history
        raw["takeout_import_batch_id"] = (repo.load_json(TAKEOUT_CACHE_METADATA_KEY) or {}).get("import_batch_id")
        warnings.append("Google Takeout history is merged as the longest available play-history source.")
    coordinator.check_timeout(deadline)
    coordinator.stage("enriching", "Resolving available track, artist, and album metadata...", warnings=warnings)
    normalised = normalise_with_duration_cache(
        raw,
        warnings,
        allow_enrichment=(not use_demo and enrich_durations),
        allow_artist_image_enrichment=not use_demo,
        allow_album_image_enrichment=not use_demo,
    )
    coordinator.check_timeout(deadline)
    refreshed_at = datetime.now(timezone.utc).isoformat()
    normalised["refreshed_at"] = refreshed_at
    normalised = annotate_normalised_durations(normalised, repo.load_json("duration_cache") or {})
    coordinator.stage("rebuilding", "Rebuilding listening analytics from the refreshed profile...", warnings=warnings)
    analysis = build_analysis(normalised)
    coordinator.check_timeout(deadline)
    coordinator.stage("saving", "Saving the refreshed profile atomically...", warnings=warnings)
    repo.save_json_batch(
        {
            "raw": raw,
            "normalised": normalised,
            "analysis": analysis,
            "last_refresh_meta": {"refreshed_at": refreshed_at, "use_demo": use_demo, "warnings": warnings},
        }
    )
    clear_analytics_memory_caches()
    coordinator.stage(
        "complete",
        "Music refresh complete.",
        refreshedAt=refreshed_at,
        useDemo=use_demo,
        warnings=warnings,
        coverage=analysis["coverage"],
        trackCount=normalised["metadata"]["track_count"],
        playCount=normalised["metadata"]["play_count"],
    )


@router.post("/data/refresh", response_model=RefreshQueuedResponse, status_code=202)
def refresh_data(request: RefreshRequest) -> RefreshQueuedResponse:
    require_account_connections()
    try:
        job = refresh_jobs.start(request.model_dump(), process_refresh)
    except RefreshAlreadyRunning as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "Music refresh already running", "detail": str(exc), "code": "refresh_already_running"},
        ) from exc
    return RefreshQueuedResponse(jobId=str(job["jobId"]), status="queued")


@router.get("/data/refresh/{job_id}", response_model=RefreshStatusResponse)
def refresh_status(job_id: str) -> RefreshStatusResponse:
    job = refresh_jobs.status()
    if not job or job.get("jobId") != job_id:
        raise HTTPException(status_code=404, detail={"error": "Refresh job not found", "detail": "Start a new music refresh.", "code": "refresh_job_not_found"})
    return RefreshStatusResponse(**job)


@router.post("/data/import-takeout", response_model=TakeoutImportQueuedResponse, status_code=202)
async def import_takeout(file: UploadFile = File(...)) -> TakeoutImportQueuedResponse:
    settings.ensure_local_dirs()
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".zip", ".json", ".html", ".htm"}:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Unsupported Takeout file",
                "detail": "Upload a Google Takeout watch-history JSON, HTML, or ZIP file.",
                "code": "takeout_file_type_invalid",
            },
        )
    enforce_anonymous_upload_limit()
    try:
        job_id = takeout_imports.reserve(suffix)
    except TakeoutImportAlreadyRunning as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "Takeout import already running", "detail": str(exc), "code": "takeout_import_in_progress"},
        ) from exc
    except TakeoutImportCapacityReached as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "Importer busy", "detail": str(exc), "code": "anonymous_import_capacity_reached"},
            headers={"Retry-After": "60"},
        ) from exc

    import_dir = (
        Path(tempfile.gettempdir()) / "saville-music-persona" / "takeout-imports"
        if settings.anonymous_mode
        else settings.private_dir / "takeout-imports"
    )
    import_dir.mkdir(parents=True, exist_ok=True)
    upload_path = import_dir / f"{job_id}{suffix}"
    file_size = 0
    try:
        with upload_path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                file_size += len(chunk)
                if file_size > settings.effective_upload_limit_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail={
                            "error": "Takeout upload is too large",
                            "detail": f"The upload exceeds the configured {settings.effective_upload_limit_bytes // (1024 * 1024)} MB limit.",
                            "code": "takeout_upload_too_large",
                        },
                    )
                destination.write(chunk)
        if file_size == 0:
            raise HTTPException(
                status_code=400,
                detail={"error": "Takeout file is empty", "detail": "Choose a non-empty Takeout export.", "code": "takeout_upload_empty"},
            )
        takeout_imports.queue(job_id, upload_path, file_size, process_takeout_import)
    except HTTPException:
        upload_path.unlink(missing_ok=True)
        takeout_imports.release_reservation(job_id)
        raise
    except Exception as exc:  # noqa: BLE001
        upload_path.unlink(missing_ok=True)
        takeout_imports.release_reservation(job_id)
        raise HTTPException(
            status_code=500,
            detail={"error": "Takeout upload failed", "detail": "The file could not be stored locally.", "code": "takeout_upload_failed"},
        ) from exc
    finally:
        await file.close()
    takeout_imports.log(job_id, "response_returned", status="queued")
    return TakeoutImportQueuedResponse(jobId=job_id, status="queued")


@router.get("/data/import-takeout/{job_id}", response_model=TakeoutImportStatusResponse)
def takeout_import_status(job_id: str) -> TakeoutImportStatusResponse:
    job = takeout_imports.get(job_id)
    if not job:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "Takeout import job not found",
                "detail": "The backend may have restarted before the upload was queued. Retry the import.",
                "code": "takeout_import_job_not_found",
            },
        )
    return TakeoutImportStatusResponse.model_validate(job)


def process_takeout_import(
    job_id: str,
    upload_path: Path,
    coordinator: TakeoutImportCoordinator,
    deadline: float,
) -> None:
    coordinator.stage(job_id, "parsing", "Opening and parsing the Takeout export.")
    try:
        parsed = parse_takeout_file(
            upload_path,
            on_event=lambda event, fields: coordinator.log(job_id, event, **fields),
            check_timeout=lambda: coordinator.check_timeout(deadline),
        )
    except TakeoutParseError as exc:
        coordinator.fail(job_id, str(exc), "takeout_parse_failed", "parsing")
        return
    coordinator.check_timeout(deadline)
    coordinator.log(
        job_id,
        "deduplication_completed",
        rawEventCount=parsed.raw_event_count,
        acceptedEventCount=len(parsed.entries),
    )
    if not parsed.entries:
        coordinator.fail(
            job_id,
            "No usable YouTube Music play events were found. Check that the export contains watch history.",
            "takeout_no_accepted_events",
            "parsing",
        )
        return
    if settings.anonymous_mode and len(parsed.entries) > settings.anonymous_max_events:
        coordinator.fail(
            job_id,
            f"This export contains more than the hosted limit of {settings.anonymous_max_events:,} music events.",
            "anonymous_event_limit_exceeded",
            "parsing",
        )
        return

    coordinator.stage(
        job_id,
        "normalizing",
        "Canonical events are ready. Building the local listening dataset.",
        importedCount=len(parsed.entries),
    )


    previous_raw = repo.load_json("raw")
    if not isinstance(previous_raw, dict) or previous_raw.get("source") == "demo":
        raw: dict[str, Any] = {"source": "google_takeout", "history": [], "warnings": []}
    else:
        raw = dict(previous_raw)
        raw["source"] = "google_takeout"
    # One local profile represents one Takeout dataset. Replacing it on upload
    # prevents stale rows (or a previous tester's history) from contaminating
    # the next user's totals. A Google Takeout ZIP already contains all matching
    # watch-history files from that export.
    combined_entries = list(parsed.entries)
    raw["takeout_history"] = combined_entries
    raw["takeout_parser_schema_version"] = TAKEOUT_PARSER_SCHEMA_VERSION
    raw["takeout_import_batch_id"] = job_id
    raw["takeout_import_diagnostics"] = parsed.diagnostics
    for key in ("artist_image_cache_v2", "album_image_cache_v1", "release_year_cache_v1", "track_metadata_cache_v1"):
        cached = repo.load_json(key)
        if cached:
            raw[key] = cached
    coordinator.check_timeout(deadline)
    try:
        warnings = ["Google Takeout history imported and rebuilt from canonical local events."]
        normalised = normalise_with_duration_cache(
            raw,
            warnings,
            # Both lookups can use the public YouTube Music catalogue. A
            # Takeout-only setup should not need a live account session just to
            # display its own artists and album covers.
            allow_artist_image_enrichment=True,
            allow_album_image_enrichment=True,
        )
        normalised = annotate_normalised_durations(normalised, repo.load_json("duration_cache") or {})
    except Exception:  # noqa: BLE001
        coordinator.fail(
            job_id,
            "Canonical event normalization failed. Your previous profile was preserved.",
            "takeout_normalization_failed",
            "normalizing",
        )
        return
    coordinator.check_timeout(deadline)
    if not normalised.get("play_events") or not normalised.get("tracks"):
        coordinator.fail(
            job_id,
            "The export contained no events usable for analysis. Your previous profile was preserved.",
            "takeout_profile_empty",
            "normalizing",
        )
        return

    coordinator.stage(job_id, "rebuilding", "Rebuilding Overview and listening profiles from local events.")
    coordinator.log(job_id, "profile_rebuild_started", playCount=len(normalised["play_events"]))
    try:
        refreshed_at = datetime.now(timezone.utc).isoformat()
        normalised["refreshed_at"] = refreshed_at
        analysis = build_analysis(normalised)
        if not analysis.get("top_tracks") or not analysis.get("coverage"):
            raise ValueError("analysis profile is incomplete")
        overview_profile = build_overview_response(
            normalised,
            "this_month",
            None,
            settings.local_timezone,
        )
        if overview_profile.get("schemaVersion") != OVERVIEW_SCHEMA_VERSION or not overview_profile.get("identity"):
            raise ValueError("overview profile is incomplete")
    except Exception:  # noqa: BLE001
        coordinator.fail(
            job_id,
            "Analytics rebuild failed. Your previous profile was preserved and remains usable.",
            "takeout_analytics_rebuild_failed",
            "rebuilding",
        )
        return
    coordinator.check_timeout(deadline)
    coordinator.log(
        job_id,
        "profile_rebuild_completed",
        trackCount=normalised["metadata"]["track_count"],
        playCount=normalised["metadata"]["play_count"],
    )

    unknown_tracks = sum(1 for track in normalised.get("tracks", []) if track.get("primary_artist") == "Unknown Artist")
    if unknown_tracks:
        warnings.append(f"{unknown_tracks} track(s) have partial artist metadata; play counts are still included.")
    metadata = {
        "parser_schema_version": TAKEOUT_PARSER_SCHEMA_VERSION,
        "event_schema_version": LISTENING_EVENT_SCHEMA_VERSION,
        "data_schema_version": NORMALISED_DATA_SCHEMA_VERSION,
        "imported_at": refreshed_at,
        "import_batch_id": job_id,
        "diagnostics": normalised.get("import_diagnostics") or parsed.diagnostics,
    }
    coordinator.stage(job_id, "saving", "Saving the new profile and invalidating dependent caches.")
    coordinator.log(job_id, "cache_invalidation_started", cacheGroups=["persona_report", "overview_language", "recommendations"])
    try:
        repo.save_json_batch(
            {
                "takeout_history": combined_entries,
                TAKEOUT_CACHE_METADATA_KEY: metadata,
                "raw": raw,
                "normalised": normalised,
                "analysis": analysis,
                "last_refresh_meta": {"refreshed_at": refreshed_at, "use_demo": False, "warnings": warnings},
            },
            delete_keys=["latest_report", "recommendations"],
            delete_prefixes=["persona_report:", "persona_report_pointer:", "overview_language:"],
        )
    except Exception:  # noqa: BLE001
        coordinator.fail(
            job_id,
            "The rebuilt profile could not be saved. Your previous profile was preserved.",
            "takeout_persistence_failed",
            "saving",
        )
        return
    coordinator.log(job_id, "cache_invalidated", cacheGroups=["persona_report", "overview_language", "recommendations"])
    clear_analytics_memory_caches()
    coordinator.log(job_id, "persistence_completed", acceptedEventCount=len(parsed.entries))
    coordinator.stage(
        job_id,
        "complete",
        "Google Takeout history imported. Overview is ready.",
        importedCount=len(parsed.entries),
        totalImportedCount=len(combined_entries),
        duplicateCount=int(parsed.diagnostics.get("duplicates") or 0),
        trackCount=normalised["metadata"]["track_count"],
        playCount=normalised["metadata"]["play_count"],
    )


@router.post("/data/import-spotify-history", response_model=TakeoutImportQueuedResponse, status_code=202)
async def import_spotify_history(file: UploadFile = File(...)) -> TakeoutImportQueuedResponse:
    settings.ensure_local_dirs()
    suffix = Path(file.filename or "").suffix.casefold()
    if suffix not in {".zip", ".json"}:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Unsupported Spotify history file",
                "detail": "Upload the ZIP from Spotify's data download, or a Spotify streaming-history JSON file.",
                "code": "spotify_history_file_type_invalid",
            },
        )
    enforce_anonymous_upload_limit()
    try:
        job_id = spotify_history_imports.reserve(suffix)
    except TakeoutImportAlreadyRunning as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "Spotify history import already running", "detail": str(exc), "code": "spotify_history_import_in_progress"},
        ) from exc
    except TakeoutImportCapacityReached as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "Importer busy", "detail": str(exc), "code": "anonymous_import_capacity_reached"},
            headers={"Retry-After": "60"},
        ) from exc

    import_dir = Path(tempfile.gettempdir()) / "saville-music-persona" / "spotify-history-imports"
    import_dir.mkdir(parents=True, exist_ok=True)
    upload_path = import_dir / f"{job_id}{suffix}"
    file_size = 0
    try:
        with upload_path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                file_size += len(chunk)
                if file_size > settings.effective_upload_limit_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail={
                            "error": "Spotify history upload is too large",
                            "detail": f"The upload exceeds the configured {settings.effective_upload_limit_bytes // (1024 * 1024)} MB limit.",
                            "code": "spotify_history_upload_too_large",
                        },
                    )
                destination.write(chunk)
        if file_size == 0:
            raise HTTPException(
                status_code=400,
                detail={"error": "Spotify history file is empty", "detail": "Choose a non-empty Spotify export.", "code": "spotify_history_upload_empty"},
            )
        spotify_history_imports.queue(job_id, upload_path, file_size, process_spotify_history_import)
    except HTTPException:
        upload_path.unlink(missing_ok=True)
        spotify_history_imports.release_reservation(job_id)
        raise
    except Exception as exc:  # noqa: BLE001
        upload_path.unlink(missing_ok=True)
        spotify_history_imports.release_reservation(job_id)
        raise HTTPException(
            status_code=500,
            detail={"error": "Spotify history upload failed", "detail": "The file could not be stored locally.", "code": "spotify_history_upload_failed"},
        ) from exc
    finally:
        await file.close()
    return TakeoutImportQueuedResponse(jobId=job_id, status="queued")


@router.get("/data/import-spotify-history/{job_id}", response_model=TakeoutImportStatusResponse)
def spotify_history_import_status(job_id: str) -> TakeoutImportStatusResponse:
    job = spotify_history_imports.get(job_id)
    if not job:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "Spotify history import job not found",
                "detail": "The backend may have restarted before the upload was queued. Retry the import.",
                "code": "spotify_history_import_job_not_found",
            },
        )
    return TakeoutImportStatusResponse.model_validate(job)


def process_spotify_history_import(
    job_id: str,
    upload_path: Path,
    coordinator: TakeoutImportCoordinator,
    deadline: float,
) -> None:
    coordinator.stage(job_id, "parsing", "Opening and validating the Spotify streaming-history export.")
    try:
        parsed = parse_spotify_history_file(
            upload_path,
            on_event=lambda event, fields: coordinator.log(job_id, event, **fields),
            check_timeout=lambda: coordinator.check_timeout(deadline),
        )
    except SpotifyHistoryParseError as exc:
        coordinator.fail(job_id, str(exc), "spotify_history_parse_failed", "parsing")
        return
    if not parsed.entries:
        coordinator.fail(
            job_id,
            "No usable Spotify music plays were found. Podcast, audiobook, and zero-playback rows are ignored.",
            "spotify_history_no_accepted_events",
            "parsing",
        )
        return
    if settings.anonymous_mode and len(parsed.entries) > settings.anonymous_max_events:
        coordinator.fail(
            job_id,
            f"This export contains more than the hosted limit of {settings.anonymous_max_events:,} music events.",
            "anonymous_event_limit_exceeded",
            "parsing",
        )
        return

    coordinator.stage(
        job_id,
        "normalizing",
        "Building a Spotify profile from canonical listening events.",
        importedCount=len(parsed.entries),
    )
    previous_raw = repo.load_json("spotify_raw")
    raw = dict(previous_raw) if isinstance(previous_raw, dict) else {"source": "spotify"}
    raw["source"] = "spotify"
    # A new export replaces the previous imported export. Spotify's export
    # already contains its complete requested range, so appending would double
    # count overlapping downloads. OAuth catalogue/top-item data is retained.
    raw["streaming_history"] = list(parsed.entries)
    raw["spotify_history_parser_schema_version"] = SPOTIFY_HISTORY_PARSER_SCHEMA_VERSION
    raw["spotify_history_import_batch_id"] = job_id
    raw["spotify_history_import_diagnostics"] = parsed.diagnostics
    try:
        collection = spotify_raw_to_collection(raw)
        normalised = normalise_collection(collection)
        genre_cache, _ = seed_cache_from_source(durable_genre_cache(), normalised, provider="spotify")
        apply_genre_cache(normalised, genre_cache)
        normalised = annotate_normalised_durations(normalised, repo.load_json("duration_cache") or {})
        sync_recording_catalog(normalised, "spotify")
    except Exception:  # noqa: BLE001
        coordinator.fail(
            job_id,
            "Spotify event normalization failed. Your previous Spotify profile was preserved.",
            "spotify_history_normalization_failed",
            "normalizing",
        )
        return
    coordinator.check_timeout(deadline)
    if not normalised.get("play_events"):
        coordinator.fail(
            job_id,
            "The Spotify export contained no music events usable for analysis. Your previous profile was preserved.",
            "spotify_history_profile_empty",
            "normalizing",
        )
        return

    coordinator.stage(job_id, "rebuilding", "Rebuilding Spotify listening totals and period profiles.")
    try:
        refreshed_at = datetime.now(timezone.utc).isoformat()
        normalised["refreshed_at"] = refreshed_at
        analysis = build_analysis(normalised)
        if not analysis.get("top_tracks") or not analysis.get("coverage"):
            raise ValueError("analysis profile is incomplete")
        build_overview_response(normalised, "this_month", None, settings.local_timezone)
    except Exception:  # noqa: BLE001
        coordinator.fail(
            job_id,
            "Spotify analytics rebuild failed. Your previous Spotify profile was preserved.",
            "spotify_history_analytics_rebuild_failed",
            "rebuilding",
        )
        return

    warnings = [SPOTIFY_HISTORY_NOTE]
    coordinator.stage(job_id, "saving", "Saving the Spotify profile and invalidating dependent reports.")
    try:
        repo.save_json_batch(
            {
                "spotify_raw": raw,
                "spotify_normalised": normalised,
                "spotify_analysis": analysis,
                "spotify_last_refresh_meta": {"refreshed_at": refreshed_at, "use_demo": False, "warnings": warnings},
                "genre_metadata_cache": genre_cache,
            },
            delete_keys=["spotify_latest_report", "spotify_recommendations"],
            delete_prefixes=["persona_report:spotify:", "persona_report_pointer:spotify:", "overview_language:spotify:"],
        )
    except Exception:  # noqa: BLE001
        coordinator.fail(
            job_id,
            "The Spotify profile could not be saved. Your previous Spotify profile was preserved.",
            "spotify_history_persistence_failed",
            "saving",
        )
        return
    clear_analytics_memory_caches()
    coordinator.stage(
        job_id,
        "complete",
        "Spotify streaming history imported. Spotify analysis is ready.",
        importedCount=len(parsed.entries),
        duplicateCount=int(parsed.diagnostics.get("duplicates") or 0),
        trackCount=normalised["metadata"]["track_count"],
        playCount=normalised["metadata"]["play_count"],
    )


def process_duration_enrichment(coordinator: DurationEnrichmentCoordinator, deadline: float) -> None:
    """Resolve exact video durations without making Takeout imports wait on upstream metadata."""
    cached_normalised = repo.load_json("normalised")
    if not isinstance(cached_normalised, dict) or not cached_normalised.get("tracks"):
        coordinator.fail("No listening profile is available to enrich yet. Import or refresh YouTube data first.", "duration_profile_missing")
        return

    coordinator.stage("resolving", "Resolving missing track durations from exact YouTube video IDs.")
    duration_cache = repo.load_json("duration_cache") or {}
    if not isinstance(duration_cache, dict):
        duration_cache = {}
    stats = ytmusic.enrich_duration_cache(cached_normalised, duration_cache, settings.duration_enrichment_limit)
    release_year_cache = repo.load_json("release_year_cache_v1") or {}
    if not isinstance(release_year_cache, dict):
        release_year_cache = {}
    release_stats = ytmusic.enrich_release_year_cache(
        cached_normalised,
        release_year_cache,
        settings.release_year_enrichment_limit,
    )
    track_metadata_cache = ensure_track_metadata_cache(repo.load_json("track_metadata_cache_v1") or {})
    metadata_stats = ytmusic.enrich_track_metadata_cache(
        cached_normalised,
        track_metadata_cache,
        settings.track_metadata_enrichment_limit,
    )
    coordinator.check_timeout(deadline)
    if not stats.get("attempted") and not release_stats.get("attempted") and not metadata_stats.get("attempted"):
        coordinator.stage("complete", "Track duration, identity metadata, and release-year coverage are already up to date.", **stats, releaseYearEnrichment=release_stats, trackMetadataEnrichment=metadata_stats)
        return

    coordinator.stage("rebuilding", "Applying resolved durations and rebuilding listening totals.", **stats)
    # Refreshes can enrich artwork while a duration job is awaiting upstream
    # metadata. Reload the current profile before saving so this background job
    # applies its cache to the newest data instead of overwriting album covers
    # and artist portraits with its older snapshot.
    latest_normalised = repo.load_json("normalised")
    if isinstance(latest_normalised, dict) and latest_normalised.get("tracks"):
        cached_normalised = latest_normalised
    rebuilt_normalised = annotate_normalised_durations(cached_normalised, duration_cache)
    apply_track_metadata_cache(rebuilt_normalised, track_metadata_cache)
    rebuilt_normalised = apply_release_year_cache(rebuilt_normalised, release_year_cache)
    apply_genre_cache(rebuilt_normalised, durable_genre_cache())
    sync_recording_catalog(rebuilt_normalised, "youtube")
    rebuilt_analysis = build_analysis(rebuilt_normalised)
    if "coverage" not in rebuilt_analysis:
        raise ValueError("duration rebuild produced an incomplete analysis")
    coordinator.check_timeout(deadline)
    raw = repo.load_json("raw") or {}
    if isinstance(raw, dict):
        raw["release_year_cache_v1"] = release_year_cache
        raw["track_metadata_cache_v1"] = track_metadata_cache
    repo.save_json_batch(
        {"duration_cache": duration_cache, "release_year_cache_v1": release_year_cache, "track_metadata_cache_v1": track_metadata_cache, "raw": raw, "normalised": rebuilt_normalised, "analysis": rebuilt_analysis},
        delete_keys=["latest_report", "recommendations"],
        delete_prefixes=["persona_report:", "persona_report_pointer:", "overview_language:"],
    )
    clear_analytics_memory_caches()
    remaining_total = int(stats.get("remaining") or 0) + int(release_stats.get("remaining") or 0) + int(metadata_stats.get("remaining") or 0)
    remaining_note = f" {remaining_total} more track(s) remain queued for the next local batch." if remaining_total else ""
    coordinator.stage(
        "complete",
        f"Resolved {stats['added']} track duration(s), {metadata_stats['added']} authoritative track metadata record(s), and {release_stats['added']} release year(s). Listening totals are updated.{remaining_note}",
        # Duration lookups are cheap to resume automatically.  Release-year
        # matching makes upstream searches and album reads, so leave additional
        # metadata for the next explicit/background refresh instead of chaining
        # an unbounded job on a laptop.
        continueQueued=bool(stats.get("remaining")),
        **stats,
        releaseYearEnrichment=release_stats,
        trackMetadataEnrichment=metadata_stats,
    )


@router.post("/data/duration-enrichment", response_model=DurationEnrichmentStatusResponse, status_code=202)
def start_duration_enrichment() -> DurationEnrichmentStatusResponse:
    try:
        job = duration_enrichment.start(process_duration_enrichment)
    except DurationEnrichmentAlreadyRunning as exc:
        existing = duration_enrichment.status()
        if existing:
            return DurationEnrichmentStatusResponse.model_validate(existing)
        raise HTTPException(status_code=409, detail={"error": "Duration enrichment is already running", "code": "duration_enrichment_in_progress"}) from exc
    return DurationEnrichmentStatusResponse.model_validate(job)


@router.get("/data/duration-enrichment", response_model=DurationEnrichmentStatusResponse)
def duration_enrichment_status() -> DurationEnrichmentStatusResponse:
    job = duration_enrichment.status()
    if not job:
        return DurationEnrichmentStatusResponse(status="idle", progress=0, message="No track duration enrichment is running.")
    return DurationEnrichmentStatusResponse.model_validate(job)


def process_genre_enrichment(coordinator: GenreEnrichmentCoordinator, deadline: float) -> None:
    cached_normalised = repo.load_json("normalised")
    if not isinstance(cached_normalised, dict) or not cached_normalised.get("tracks"):
        coordinator.fail("No YouTube listening profile is available to enrich yet.", "genre_profile_missing")
        return

    working_normalised = copy.deepcopy(cached_normalised)
    recording_catalog = current_recording_catalog()
    cache = durable_genre_cache()
    apply_genre_cache(working_normalised, cache)
    recording_catalog.sync_normalised(working_normalised, profile_source=recording_profile_source("youtube"))
    before_analysis = build_analysis(working_normalised)
    before_coverage = genre_coverage_payload(before_analysis)
    coordinator.stage(
        "resolving",
        "Checking high-impact unclassified artists, then recordings, against MusicBrainz.",
        provider="musicbrainz",
        beforeCoverage=before_coverage["genreCoveragePercent"],
    )
    # Exact artist evidence has much higher coverage per request. Give it the
    # first three minutes, then use recording identity as the narrow fallback
    # for tracks whose artist still has no trusted genres. Keep rebuild time
    # outside both provider budgets.
    resolution_deadline = max(time.monotonic(), deadline - 20)
    artist_deadline = min(resolution_deadline, time.monotonic() + 180)
    cache, stats = genre_enrichment_service.enrich(
        working_normalised,
        cache,
        limit=settings.genre_enrichment_limit,
        deadline=artist_deadline,
        # Persist provider evidence after every resolved artist. If the laptop
        # sleeps or the backend restarts, the completed requests are still
        # available and will be reapplied on the next rebuild.
        on_cache_update=lambda updated: repo.save_json("genre_metadata_cache", updated),
    )
    apply_genre_cache(working_normalised, cache)
    coordinator.stage(
        "resolving",
        "Artist evidence applied; checking the remaining high-impact recordings.",
        provider="musicbrainz",
        beforeCoverage=before_coverage["genreCoveragePercent"],
        **stats,
    )
    recording_stats = recording_genre_enrichment_service.enrich(
        working_normalised,
        recording_catalog,
        limit=settings.recording_genre_enrichment_limit,
        deadline=resolution_deadline,
    )
    stats.update(recording_stats)

    coordinator.stage(
        "rebuilding",
        "Applying trusted genre matches and rebuilding local analytics.",
        provider="musicbrainz",
        beforeCoverage=before_coverage["genreCoveragePercent"],
        **stats,
    )
    # Another import or metadata job may have completed while MusicBrainz was
    # rate-limited. Always apply this batch to the newest canonical profile.
    latest_normalised = repo.load_json("normalised")
    if isinstance(latest_normalised, dict) and latest_normalised.get("tracks"):
        working_normalised = copy.deepcopy(latest_normalised)
    apply_genre_cache(working_normalised, cache)
    recording_catalog.sync_normalised(working_normalised, profile_source=recording_profile_source("youtube"))
    rebuilt_analysis = build_analysis(working_normalised)
    after_coverage = genre_coverage_payload(rebuilt_analysis)
    repo.save_json_batch(
        {
            "genre_metadata_cache": cache,
            "normalised": working_normalised,
            "analysis": rebuilt_analysis,
        },
        delete_keys=["latest_report", "recommendations"],
        delete_prefixes=["persona_report:", "persona_report_pointer:", "overview_language:"],
    )
    clear_analytics_memory_caches()
    gain = round(after_coverage["genreCoveragePercent"] - before_coverage["genreCoveragePercent"], 1)
    provider_errors = {stats.get("providerError"), stats.get("recordingProviderError")}
    if "musicbrainz_temporarily_unavailable" in provider_errors:
        provider_note = " MusicBrainz became temporarily unavailable; completed matches were kept."
    elif "musicbrainz_time_limit_reached" in provider_errors:
        provider_note = " The bounded lookup window ended; completed matches were kept for the next batch."
    else:
        provider_note = ""
    coordinator.stage(
        "complete",
        f"Genre enrichment complete. Coverage is {after_coverage['genreCoveragePercent']:.1f}% ({gain:+.1f} points).{provider_note}",
        provider="musicbrainz",
        beforeCoverage=before_coverage["genreCoveragePercent"],
        afterCoverage=after_coverage["genreCoveragePercent"],
        unknownEventCount=after_coverage["unknownEventCount"],
        **stats,
    )


def genre_coverage_payload(analysis: dict[str, Any]) -> dict[str, Any]:
    coverage = ((analysis.get("overview") or {}).get("taste_interpretation") or {}).get("coverage") or {}
    return {
        "genreCoveragePercent": float(coverage.get("genreCoveragePercent") or coverage.get("genre_coverage_percent") or 0),
        "unknownEventCount": int(coverage.get("unknownEventCount") or 0),
    }


@router.post("/data/genre-enrichment", response_model=GenreEnrichmentStatusResponse, status_code=202)
def start_genre_enrichment() -> GenreEnrichmentStatusResponse:
    try:
        job = genre_enrichment.start(process_genre_enrichment)
    except GenreEnrichmentAlreadyRunning as exc:
        existing = genre_enrichment.status()
        if existing:
            return GenreEnrichmentStatusResponse.model_validate(existing)
        raise HTTPException(status_code=409, detail={"error": "Genre enrichment is already running", "code": "genre_enrichment_in_progress"}) from exc
    return GenreEnrichmentStatusResponse.model_validate(job)


@router.get("/data/genre-enrichment", response_model=GenreEnrichmentStatusResponse)
def genre_enrichment_status() -> GenreEnrichmentStatusResponse:
    job = genre_enrichment.status()
    if not job:
        return GenreEnrichmentStatusResponse(status="idle", progress=0, message="No genre enrichment is running.")
    return GenreEnrichmentStatusResponse.model_validate(job)


@router.get("/data/genre-catalog")
def genre_catalog_status() -> dict[str, Any]:
    """Return local catalog diagnostics without exposing listening-history rows."""
    return current_recording_catalog().summary()


@router.get("/data/genre-catalog/{recording_id}")
def genre_catalog_recording(recording_id: str) -> dict[str, Any]:
    details = current_recording_catalog().details(recording_id)
    if not details:
        raise HTTPException(status_code=404, detail={"error": "Recording not found", "code": "recording_not_found"})
    return details


@router.get("/data/coverage")
def coverage(source: str = Query("youtube")) -> dict[str, Any]:
    return require_source_cache("analysis", source)["coverage"]


@router.get("/analytics/diagnostics")
def analytics_diagnostics(
    period: str = Query("rolling_year"),
    month: str | None = Query(None),
    timezone_name: str | None = Query(None, alias="timezone"),
    source: str = Query("youtube"),
) -> dict[str, Any]:
    """Developer-safe reconciliation; it exposes counts and versions, never events."""
    reimport = takeout_reimport_status(repo.load_json(TAKEOUT_CACHE_METADATA_KEY)) if normalise_source(source) == "youtube" else {"requiresReimport": False}
    if reimport["requiresReimport"]:
        return {"cache": {"status": "stale"}, "reimport": reimport}
    profile, normalised = canonical_period_profile(source, period, month, timezone_name)
    metadata = normalised.get("metadata") or {}
    return {
        "parserVersion": metadata.get("parser_schema_version"),
        "eventSchemaVersion": metadata.get("listening_event_schema_version"),
        "importBatchId": next((event.get("import_batch_id") for event in normalised.get("listening_events") or [] if event.get("import_batch_id")), None),
        "dataFingerprint": profile["dataFingerprint"],
        "analyticsVersion": ANALYTICS_VERSION,
        "genreMapVersion": GENRE_MAP_VERSION,
        "cache": {"status": "miss", "reason": "period profiles are computed from canonical local events"},
        "reimport": reimport,
        "import": profile["reconciliation"],
        "profile": {**profile["period"], **profile["figures"]},
    }


@router.get("/analysis/overview", response_model=OverviewAnalysisResponse)
def overview(
    period: str = Query("this_month"),
    month: str | None = Query(None),
    timezone_name: str | None = Query(None, alias="timezone"),
    source: str = Query("youtube"),
) -> dict[str, Any]:
    resolved_source = normalise_source(source)
    meta = repo.load_json(cache_key("last_refresh_meta", resolved_source)) or {}
    payload, _, _ = canonical_overview_payload(resolved_source, period, month, timezone_name)
    evidence = overview_language_evidence(payload)
    fingerprint = overview_language_fingerprint(evidence, resolved_source, settings.ollama_model)
    language_key = f"overview_language:v{OVERVIEW_LANGUAGE_CACHE_VERSION}:{resolved_source}:{fingerprint}"
    cached_language = repo.load_json(language_key)
    language: dict[str, Any] | None = None
    generation_source = "fallback"
    cache_matches = (
        isinstance(cached_language, dict)
        and cached_language.get("schemaVersion") == OVERVIEW_SCHEMA_VERSION
        and cached_language.get("fingerprint") == fingerprint
        and isinstance(cached_language.get("language"), dict)
    )
    cached_generation = str(cached_language.get("generationSource") or "") if cache_matches else ""
    fallback_cache_fresh = cache_matches and cached_generation == "fallback" and _cache_age_seconds(cached_language) < OVERVIEW_FALLBACK_CACHE_SECONDS
    if cache_matches and cached_generation != "fallback" and cached_language.get("language"):
        language = cached_language["language"]
        generation_source = "cache-gemma"
    elif fallback_cache_fresh:
        generation_source = "fallback"
    elif settings.anonymous_mode or payload["selectedPeriod"]["key"] != PERSONA_REPORT_PERIOD:
        generation_source = "fallback"
    else:
        language = ollama.generate_overview_language(evidence)
        if isinstance(language, dict):
            generation_source = "gemma"
        repo.save_json(
            language_key,
            {
                "schemaVersion": OVERVIEW_SCHEMA_VERSION,
                "languageVersion": OVERVIEW_LANGUAGE_CACHE_VERSION,
                "fingerprint": fingerprint,
                "source": resolved_source,
                "model": settings.ollama_model,
                "generationSource": generation_source,
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "language": language or {},
            },
        )
    payload = apply_overview_language(payload, language, generation_source)
    payload["source"] = resolved_source
    payload["sourceLabel"] = "Spotify" if resolved_source == "spotify" else "YouTube Music"
    payload["languageFingerprint"] = fingerprint
    payload["overview"]["last_refreshed_at"] = meta.get("refreshed_at")
    payload["overview"]["use_demo"] = meta.get("use_demo", False)
    payload["overview"]["warnings"] = meta.get("warnings", [])
    payload["overview"]["source"] = resolved_source
    payload["overview"]["source_label"] = payload["sourceLabel"]
    age = payload["musicalAge"]
    print(f"[overview] period={payload['selectedPeriod']['key']} schema={OVERVIEW_SCHEMA_VERSION}", flush=True)
    print(
        f"[musical-age] age={age['age']} range={age['likelyMin']}-{age['likelyMax']} "
        f"confidence={age['confidence']:.2f} version={MUSICAL_AGE_CALCULATION_VERSION}",
        flush=True,
    )
    return payload


@router.get("/v1/analysis/overview", response_model=AnalyticsEnvelope[OverviewAnalysisResponse])
def contract_overview(
    period: str = Query("this_month"),
    month: str | None = Query(None),
    timezone_name: str | None = Query(None, alias="timezone"),
    source: str = Query("youtube"),
) -> AnalyticsEnvelope[OverviewAnalysisResponse]:
    resolved_source = normalise_source(source)
    profile, normalised = canonical_period_profile(resolved_source, period, month, timezone_name)
    payload = OverviewAnalysisResponse.model_validate(overview(period, month, timezone_name, resolved_source))
    return analytics_envelope(resolved_source, profile, normalised, payload)


@router.get("/analysis/top-tracks")
def top_tracks(source: str = Query("youtube")) -> list[dict[str, Any]]:
    profile, _ = canonical_period_profile(source, "this_month", None, settings.local_timezone)
    return [{**item, "rank": index} for index, item in enumerate(profile["top_tracks"][:10], 1)]


@router.get("/analysis/top-artists")
def top_artists(source: str = Query("youtube")) -> list[dict[str, Any]]:
    profile, _ = canonical_period_profile(source, "this_month", None, settings.local_timezone)
    return [{**item, "rank": index} for index, item in enumerate(profile["top_artists"][:10], 1)]


@router.get("/analysis/scores")
def scores(
    period: str = Query("rolling_year"),
    month: str | None = Query(None),
    timezone_name: str | None = Query(None, alias="timezone"),
    source: str = Query("youtube"),
) -> list[dict[str, Any]]:
    analysis, spec, event_count = analysis_for_period(period, month, timezone_name, source)
    scores_payload = analysis["scores"]
    if spec["period"] in {"this_month", "month"} and event_count < 50:
        for score in scores_payload:
            score.setdefault("inputs", {})["confidence_note"] = "Limited sample for this month"
    for score in scores_payload:
        score.setdefault("inputs", {})["period_label"] = spec["label"]
        score.setdefault("inputs", {})["period_detected_plays"] = event_count
    return scores_payload


@router.get("/analysis/charts")
def charts(
    period: str = Query("rolling_year"),
    month: str | None = Query(None),
    timezone_name: str | None = Query(None, alias="timezone"),
    source: str = Query("youtube"),
) -> dict[str, Any]:
    analysis, _, _ = analysis_for_period(period, month, timezone_name, source)
    return analysis["charts"]


@router.get("/insights", response_model=InsightsResponse)
def insights(
    period: str = Query("rolling_year"),
    month: str | None = Query(None),
    timezone_name: str | None = Query(None, alias="timezone"),
    source: str = Query("youtube"),
) -> InsightsResponse:
    normalised = require_source_cache("normalised", source)
    metadata = normalised.get("metadata") or {}
    coverage = normalised.get("coverage") or {}
    cache_key = (
        current_session_namespace(),
        source,
        period,
        month,
        timezone_name or settings.local_timezone,
        normalised.get("refreshed_at"),
        metadata.get("play_count"),
        coverage.get("latest_detected_play"),
        ANALYTICS_VERSION,
        GENRE_MAP_VERSION,
    )
    cached = INSIGHTS_RESPONSE_CACHE.get(cache_key)
    if cached is not None:
        return InsightsResponse(**cached)
    payload = insights_payload(
        normalised,
        period,
        month,
        timezone_name or settings.local_timezone,
    )
    if len(INSIGHTS_RESPONSE_CACHE) >= INSIGHTS_RESPONSE_CACHE_LIMIT:
        INSIGHTS_RESPONSE_CACHE.clear()
    INSIGHTS_RESPONSE_CACHE[cache_key] = payload
    return InsightsResponse(**payload)


@router.get("/v1/insights", response_model=AnalyticsEnvelope[InsightsResponse])
def contract_insights(
    period: str = Query("rolling_year"),
    month: str | None = Query(None),
    timezone_name: str | None = Query(None, alias="timezone"),
    source: str = Query("youtube"),
) -> AnalyticsEnvelope[InsightsResponse]:
    resolved_source = normalise_source(source)
    profile, normalised = canonical_period_profile(resolved_source, period, month, timezone_name)
    payload = InsightsResponse.model_validate(insights(period, month, timezone_name, resolved_source))
    return analytics_envelope(resolved_source, profile, normalised, payload)


@router.get("/analytics/listening-minutes")
def listening_minutes(
    period: str = Query("rolling_year"),
    month: str | None = Query(None),
    timezone_name: str | None = Query(None, alias="timezone"),
    source: str = Query("youtube"),
) -> dict[str, Any]:
    return listening_minutes_payload(require_source_cache("normalised", source), period, month, timezone_name or settings.local_timezone)


@router.get("/analytics/listening-minutes/daily")
def listening_minutes_daily(
    period: str = Query("rolling_year"),
    month: str | None = Query(None),
    timezone_name: str | None = Query(None, alias="timezone"),
    source: str = Query("youtube"),
) -> list[dict[str, Any]]:
    return listening_minutes_payload(require_source_cache("normalised", source), period, month, timezone_name or settings.local_timezone)["daily"]


@router.get("/analytics/listening-minutes/heatmap")
def listening_minutes_heatmap(
    period: str = Query("rolling_year"),
    month: str | None = Query(None),
    timezone_name: str | None = Query(None, alias="timezone"),
    source: str = Query("youtube"),
) -> list[dict[str, Any]]:
    return listening_minutes_payload(require_source_cache("normalised", source), period, month, timezone_name or settings.local_timezone)["heatmap"]


@router.get("/top")
def period_top(
    period: str = Query("this_month"),
    type: str = Query("tracks"),
    month: str | None = Query(None),
    timezone_name: str | None = Query(None, alias="timezone"),
    source: str = Query("youtube"),
) -> dict[str, Any]:
    kind = "artists" if type == "artists" else "tracks"
    profile, _ = canonical_period_profile(source, period, month, timezone_name)
    ranked_items = profile["top_artists"] if kind == "artists" else profile["top_tracks"]
    items = ranked_items[:10]
    return {
        "period": profile["period"],
        "type": kind,
        "total_play_count": profile["figures"]["accepted_play_count"],
        "ranked_music_play_count": profile["figures"]["accepted_play_count"],
        "duration_quality": profile["minutes"]["duration_quality"],
        "sample_warning": None,
        "items": [{**item, "rank": index} for index, item in enumerate(items, 1)],
        "totalAvailableResults": len(ranked_items),
        "methodology": "Top lists are ranked by canonical primary-artist and track play events. Detected listening minutes use only events with usable duration metadata.",
        "classification_rules": [],
        "canonicalFigures": profile["figures"],
        "genreShares": profile["genre_shares"]["items"],
        "dataFingerprint": profile["dataFingerprint"],
    }


@router.get("/v1/top", response_model=AnalyticsEnvelope[dict[str, Any]])
def contract_period_top(
    period: str = Query("this_month"),
    type: str = Query("tracks"),
    month: str | None = Query(None),
    timezone_name: str | None = Query(None, alias="timezone"),
    source: str = Query("youtube"),
) -> AnalyticsEnvelope[dict[str, Any]]:
    resolved_source = normalise_source(source)
    profile, normalised = canonical_period_profile(resolved_source, period, month, timezone_name)
    data = period_top(period, type, month, timezone_name, resolved_source)
    return analytics_envelope(resolved_source, profile, normalised, data)


@router.get("/top/artist-songs")
def period_artist_songs(
    artist: str = Query(...),
    period: str = Query("this_month"),
    month: str | None = Query(None),
    timezone_name: str | None = Query(None, alias="timezone"),
    source: str = Query("youtube"),
) -> dict[str, Any]:
    return artist_songs_payload(require_source_cache("normalised", source), artist, period, month, timezone_name or settings.local_timezone)


@router.get("/top/albums")
def period_albums(
    period: str = Query("this_month"),
    month: str | None = Query(None),
    timezone_name: str | None = Query(None, alias="timezone"),
    source: str = Query("youtube"),
    limit: int = Query(10, ge=1, le=20),
) -> dict[str, Any]:
    return albums_payload(require_source_cache("normalised", source), period, month, timezone_name or settings.local_timezone, limit=limit)


@router.get("/top/album-songs")
def period_album_songs(
    album: str = Query(...),
    artist: str | None = Query(None),
    period: str = Query("this_month"),
    month: str | None = Query(None),
    timezone_name: str | None = Query(None, alias="timezone"),
    source: str = Query("youtube"),
) -> dict[str, Any]:
    return album_songs_payload(require_source_cache("normalised", source), album, artist, period, month, timezone_name or settings.local_timezone)


@router.get("/taste-dna")
def taste_dna(
    period: str = Query("rolling_year"),
    month: str | None = Query(None),
    timezone_name: str | None = Query(None, alias="timezone"),
    source: str = Query("youtube"),
) -> dict[str, Any]:
    return taste_dna_payload(require_source_cache("normalised", source), period, month, timezone_name or settings.local_timezone)


@router.get("/taste-dna/compare")
def taste_dna_compare(
    base: str = Query("rolling_year"),
    compare: str = Query("this_month"),
    month: str | None = Query(None),
    timezone_name: str | None = Query(None, alias="timezone"),
    source: str = Query("youtube"),
) -> dict[str, Any]:
    return taste_dna_comparison_payload(require_source_cache("normalised", source), base, compare, month, timezone_name or settings.local_timezone)


@router.get("/scores/interpretations")
def score_interpretations(
    period: str = Query("rolling_year"),
    month: str | None = Query(None),
    timezone_name: str | None = Query(None, alias="timezone"),
    source: str = Query("youtube"),
) -> list[dict[str, Any]]:
    return scores(period, month, timezone_name, source)


@router.get("/persona/character")
def persona_character(
    period: str = Query("rolling_year"),
    month: str | None = Query(None),
    timezone_name: str | None = Query(None, alias="timezone"),
    source: str = Query("youtube"),
) -> dict[str, Any]:
    return character_payload(require_source_cache("normalised", source), period, month, timezone_name or settings.local_timezone)


@router.post("/persona/character/rewrite")
def persona_character_rewrite(payload: dict[str, Any]) -> dict[str, Any]:
    period = str(payload.get("period") or "rolling_year")
    month = payload.get("month")
    mode = str(payload.get("mode") or "playful")
    source = normalise_source(str(payload.get("source") or "youtube"))
    profile = character_payload(require_source_cache("normalised", source), period, str(month) if month else None, settings.local_timezone)
    if settings.anonymous_mode:
        raise HTTPException(status_code=503, detail={"error": "Hosted rewrite unavailable", "detail": "Anonymous hosted mode currently uses deterministic report language.", "code": "hosted_rewrite_unavailable"})
    status = ollama.status()
    if not status["reachable"] or not status["model_installed"]:
        raise HTTPException(status_code=503, detail={"error": "Ollama rewrite unavailable", "detail": status["message"], "code": "ollama_unavailable"})
    return ollama.generate_character_rewrite(profile, mode)


def report_profile_with_characters(source: str | None = "youtube", period: str = PERSONA_REPORT_PERIOD) -> dict[str, Any]:
    resolved_source = normalise_source(source)
    normalised = require_cache("normalised") if resolved_source == "youtube" else require_source_cache("normalised", resolved_source)
    return build_persona_report_evidence(normalised, settings.local_timezone, period)


@router.post("/report/generate", response_model=PersonaReportResponse)
def generate_report(request: ReportRequest) -> PersonaReportResponse:
    source = normalise_source(request.source)
    period = request.period
    # A repeated click should never rebuild analytics and wake Gemma for the
    # exact same local dataset.  The pointer is invalidated whenever data that
    # changes report facts is persisted.
    pointer = repo.load_json(persona_report_pointer_key(source, period))
    normalised_updated_at = repo.updated_at(cache_key("normalised", source))
    if (
        persona_report_pointer_is_current(pointer, source, normalised_updated_at, period)
        and isinstance(pointer, dict)
        and pointer.get("mode") == request.mode
    ):
        cached = repo.load_json(str(pointer.get("cacheKey") or ""))
        if (
            isinstance(cached, dict)
            and cached.get("schemaVersion") == PERSONA_REPORT_SCHEMA_VERSION
            and (cached.get("generation") or {}).get("source") in {"gemma", "cache-gemma"}
        ):
            return PersonaReportResponse.model_validate(cached)
    profile = report_profile_with_characters(source, period)
    analytics_fingerprint = persona_report_fingerprint(profile)
    report_cache_key = persona_report_cache_key(source, request.mode, analytics_fingerprint, period)
    language = (
        ollama.fallback_persona_language(profile["languageEvidence"], "anonymous_hosted_mode").model_dump()
        if settings.anonymous_mode
        else ollama.generate_persona_language(profile["languageEvidence"], request.mode).model_dump()
    )
    generated_at = datetime.now(timezone.utc).isoformat()
    payload = compose_persona_report(
        profile,
        language,
        source=source,
        mode=request.mode,
        generated_at=generated_at,
        prompt_version=PERSONA_REPORT_PROMPT_VERSION,
        model=settings.ollama_model,
        analytics_fingerprint=analytics_fingerprint,
        cache_key=report_cache_key,
    )
    validated = PersonaReportResponse.model_validate(payload)
    repo.save_json(report_cache_key, payload)
    save_persona_report_pointer(source, request.mode, analytics_fingerprint, report_cache_key, generated_at, period)
    return validated


@router.get("/report/latest", response_model=PersonaReportResponse)
def latest_report(source: str = Query("youtube"), period: str = Query(PERSONA_REPORT_PERIOD)) -> PersonaReportResponse:
    resolved_source = normalise_source(source)
    if period not in PERSONA_REPORT_PERIODS:
        raise HTTPException(status_code=400, detail={"error": "Unknown report period", "detail": "Use rolling_year or this_month.", "code": "unknown_report_period"})
    pointer = repo.load_json(persona_report_pointer_key(resolved_source, period))
    normalised_updated_at = repo.updated_at(cache_key("normalised", resolved_source))
    if persona_report_pointer_is_current(pointer, resolved_source, normalised_updated_at, period):
        cached = repo.load_json(str(pointer["cacheKey"]))
        if isinstance(cached, dict) and cached.get("schemaVersion") == PERSONA_REPORT_SCHEMA_VERSION:
            payload = dict(cached)
            if (payload.get("generation") or {}).get("source") == "gemma":
                payload["generation"] = {**payload["generation"], "source": "cache-gemma"}
                payload["personality"] = {**payload["personality"], "generationSource": "cache-gemma"}
                payload["summary"] = {**payload["summary"], "generationSource": "cache-gemma"}
            return PersonaReportResponse.model_validate(payload)

    profile = report_profile_with_characters(resolved_source, period)
    analytics_fingerprint = persona_report_fingerprint(profile)
    mode = str(pointer.get("mode") or "roast") if isinstance(pointer, dict) else "roast"
    language = ollama.fallback_persona_language(profile["languageEvidence"], "no_matching_gemma_cache").model_dump()
    generated_at = datetime.now(timezone.utc).isoformat()
    report_cache_key = persona_report_cache_key(resolved_source, mode, analytics_fingerprint, period)
    fallback_payload = compose_persona_report(
        profile,
        language,
        source=resolved_source,
        mode=mode,
        generated_at=generated_at,
        prompt_version=PERSONA_REPORT_PROMPT_VERSION,
        model=settings.ollama_model,
        analytics_fingerprint=analytics_fingerprint,
        cache_key=report_cache_key,
    )
    validated = PersonaReportResponse.model_validate(fallback_payload)
    repo.save_json(report_cache_key, fallback_payload)
    save_persona_report_pointer(resolved_source, mode, analytics_fingerprint, report_cache_key, generated_at, period)
    return validated


@router.get("/v1/report/latest", response_model=AnalyticsEnvelope[PersonaReportResponse])
def contract_latest_report(source: str = Query("youtube"), period: str = Query(PERSONA_REPORT_PERIOD)) -> AnalyticsEnvelope[PersonaReportResponse]:
    resolved_source = normalise_source(source)
    if period not in PERSONA_REPORT_PERIODS:
        raise HTTPException(status_code=400, detail={"error": "Unknown report period", "detail": "Use rolling_year or this_month.", "code": "unknown_report_period"})
    profile, normalised = canonical_period_profile(resolved_source, period, None, settings.local_timezone)
    return analytics_envelope(resolved_source, profile, normalised, latest_report(resolved_source, period))


@router.get("/recommendations")
def latest_recommendations() -> list[dict[str, Any]]:
    return require_cache("recommendations")


@router.get("/v1/recommendations", response_model=AnalyticsEnvelope[RecommendationsContractData])
def contract_latest_recommendations() -> AnalyticsEnvelope[RecommendationsContractData]:
    profile, normalised = canonical_period_profile("youtube", "rolling_year", None, settings.local_timezone)
    return analytics_envelope("youtube", profile, normalised, RecommendationsContractData(items=latest_recommendations()))


def _cache_age_seconds(payload: dict[str, Any]) -> float:
    try:
        created_at = datetime.fromisoformat(str(payload.get("createdAt") or "").replace("Z", "+00:00"))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - created_at).total_seconds())
    except (TypeError, ValueError):
        return float("inf")


@router.post("/recommendations/generate")
def generate_recommendation_endpoint() -> list[dict[str, Any]]:
    normalised = require_cache("normalised")
    analysis = require_cache("analysis")
    candidates: list[dict[str, Any]] = []
    if not settings.anonymous_mode and (repo.load_json("last_refresh_meta") or {}).get("use_demo") is not True:
        try:
            candidates = ytmusic.search_candidates(analysis)
        except Exception:
            candidates = []
    recommendations = generate_recommendations(normalised, analysis, candidates)
    explanations = [] if settings.anonymous_mode else ollama.generate_recommendation_explanations(analysis["report_profile"], recommendations)
    if explanations:
        explanation_map = {f"{item['track_title']}::{item['artist']}": item["why_this_fits"] for item in explanations}
        recommendations = generate_recommendations(normalised, analysis, candidates, explanation_map)
    repo.save_json("recommendations", recommendations)
    return recommendations


@router.post("/recommendations/create-playlist", response_model=PlaylistCreateResponse)
def create_playlist(request: PlaylistCreateRequest) -> PlaylistCreateResponse:
    require_account_connections()
    if not request.confirm:
        raise HTTPException(status_code=400, detail={"error": "Confirmation required", "detail": "Playlist creation only runs after explicit confirmation.", "code": "confirmation_required"})
    recommendations = require_cache("recommendations")
    video_ids = [item["video_id"] for item in recommendations if item.get("video_id")]
    if not video_ids:
        raise HTTPException(status_code=400, detail={"error": "No playlist items", "detail": "Recommendations do not include YouTube video IDs.", "code": "missing_video_ids"})
    status = ytmusic.auth_status()
    if not status["connected"]:
        raise HTTPException(status_code=400, detail={"error": "YouTube Music is not connected", "detail": status["message"], "code": "ytmusic_not_connected"})
    playlist_id = ytmusic.create_private_playlist(request.title, video_ids)
    return PlaylistCreateResponse(
        playlist_id=playlist_id,
        title=request.title,
        added_count=len(video_ids),
        message="Private YouTube Music playlist created.",
    )
