from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.analysis.music_character import character_payload, personality_catalogue
from app.analysis.musical_age import age_category_catalogue
from app.analysis.overview import build_overview_response
from app.analysis.period_profile import build_period_profile
from app.analysis.periods import albums_payload, listening_minutes_payload, taste_dna_payload, top_payload


REPORT_PERIOD = "rolling_year"


def build_persona_report_evidence(
    normalised: dict[str, Any],
    timezone_name: str,
    period_key: str = REPORT_PERIOD,
    today: date | None = None,
) -> dict[str, Any]:
    """Build every report fact from the same canonical period services."""

    # Build the expensive period analytics once.  Before this shared profile,
    # one report independently rebuilt the same 365-day taste and ranking data
    # through Overview, Character, Taste DNA, and Top endpoints.
    profile = build_period_profile(normalised, period_key, timezone_name=timezone_name, today=today)
    taste = taste_dna_payload(normalised, period_key, None, timezone_name, profile=profile)
    character = character_payload(normalised, period_key, timezone_name=timezone_name, profile=profile, taste=taste)
    overview = build_overview_response(
        normalised,
        period_key,
        timezone_name=timezone_name,
        profile=profile,
        character=character,
        musical_age_period=period_key,
    )
    listening = profile["minutes"]
    tracks = {"items": [{**item, "rank": rank} for rank, item in enumerate(profile["top_tracks"], 1)]}
    artists = {"items": [{**item, "rank": rank} for rank, item in enumerate(profile["top_artists"], 1)]}
    albums = albums_payload(normalised, period_key, None, timezone_name, limit=20, profile=profile)
    period = overview["selectedPeriod"]
    primary = character["primary"]
    musical_age = overview["musicalAge"]
    duration_quality = listening["duration_quality"]
    taste_interpretation = taste["taste_interpretation"]
    total_music_plays = int(duration_quality.get("detected_music_plays") or 0)

    genres = report_genres(taste_interpretation, total_music_plays)
    top_songs = [report_song(item) for item in tracks.get("items", [])[:5]]
    top_artists = [report_artist(item) for item in artists.get("items", [])[:5]]
    background_albums = report_background_albums(albums.get("albums", []), tracks.get("items", []))

    return {
        "period": report_period(period),
        "personality": {
            "id": str(primary.get("id") or "forming"),
            "title": str(primary.get("name") or "The Music Character Still Forming"),
            "fallbackDescription": str(primary.get("profile") or "Your listening profile is still gathering enough evidence to settle into one clear character."),
            "fallbackRoast": str(primary.get("roast") or "Your taste is still warming up, but the repeat button already has opinions."),
            "confidence": round(float(primary.get("match_score") or 0) / 100, 2),
            "habits": [str(value) for value in character.get("habits", [])[:3]],
        },
        "listeningWorld": {
            "detectedMinutes": float(listening["metrics"].get("selected_period_total_minutes") or 0),
            "formattedTime": str(listening["metrics"].get("selected_period_total_formatted") or "0 minutes"),
            "durationCoverage": round(float(duration_quality.get("duration_coverage_percent") or 0) / 100, 3),
            "genreCoverage": round(float(taste_interpretation.get("coverage", {}).get("genre_coverage_percent") or 0) / 100, 3),
            "genres": genres,
            "interpretation": str(taste.get("summary") or "Your listening world is still taking shape."),
        },
        "musicalAge": {
            "age": int(musical_age.get("age") or 0),
            "likelyMin": int(musical_age.get("likelyMin") or 0),
            "likelyMax": int(musical_age.get("likelyMax") or 0),
            "title": str(musical_age.get("title") or "The Profile Still Forming"),
            "confidence": float(musical_age.get("confidence") or 0),
            "confidenceLabel": str(musical_age.get("confidenceLabel") or "Limited confidence"),
            "fallbackExplanation": str(musical_age.get("explanation") or musical_age.get("summary") or "The estimate is based on the available rolling-year listening profile."),
            "typicalRange": [int(musical_age.get("likelyMin") or 0), int(musical_age.get("likelyMax") or 0)],
            "weightedMedianReleaseYear": int(musical_age.get("weightedMedianReleaseYear") or 0),
            "dominantDecade": str(musical_age.get("dominantDecade") or "Unknown"),
            "releaseYearCoverage": float(musical_age.get("releaseYearCoverage") or 0),
            "sourcePeriod": report_period(musical_age.get("sourcePeriod") or period),
        },
        "topFive": {"songs": top_songs, "artists": top_artists},
        "backgroundAlbums": background_albums,
        "languageEvidence": {
            "personality": {"id": primary.get("id"), "title": primary.get("name")},
            "period": period.get("label"),
            "strongestSignals": [str(value) for value in character.get("evidence_chips", [])[:5]],
            "knownArtists": [item["name"] for item in top_artists],
            "knownGenres": [item["label"] for item in genres if item["key"] != "other_unclassified"],
            "musicalAge": {
                "age": musical_age.get("age"),
                "title": musical_age.get("title"),
                "confidence": musical_age.get("confidenceLabel"),
                "isResolved": musical_age.get("isResolved", True),
                "weightedMedianReleaseYear": musical_age.get("weightedMedianReleaseYear"),
                "dominantDecade": musical_age.get("dominantDecade"),
                "releaseYearCoverage": musical_age.get("releaseYearCoverage"),
                "strongestFactors": musical_age.get("strongestFactors", [])[:3],
            },
            "behaviour": {
                "repeat": character.get("key_scores", {}).get("repeat"),
                "discovery": character.get("key_scores", {}).get("discovery"),
                "sonicTraits": character.get("sonic_traits", [])[:6],
                "secondaryCharacter": (character.get("secondary") or {}).get("name"),
                "habits": character.get("habits", []),
            },
        },
        "explainers": {"musicalAges": age_category_catalogue(), "personalities": personality_catalogue()},
    }


def compose_persona_report(
    evidence: dict[str, Any],
    language: dict[str, Any],
    *,
    source: str,
    mode: str,
    generated_at: str,
    prompt_version: int,
    model: str,
    analytics_fingerprint: str,
    cache_key: str,
) -> dict[str, Any]:
    generation_source = str(language.get("generationSource") or "fallback")
    personality = evidence["personality"]
    musical_age = evidence["musicalAge"]
    return {
        "schemaVersion": 8,
        "source": source,
        "mode": mode,
        "period": evidence["period"],
        "personality": {
            "id": personality["id"],
            "title": personality["title"],
            "shortDescription": language.get("openingDescription") or personality["fallbackDescription"],
            "roastDescription": language.get("personalityRoast") or personality["fallbackRoast"],
            "confidence": personality["confidence"],
            "habits": personality["habits"],
            "generationSource": generation_source,
        },
        "listeningWorld": evidence["listeningWorld"],
        "musicalAge": {
            **{key: value for key, value in musical_age.items() if key != "fallbackExplanation"},
            # Musical Age is a factual release-year calculation.  Keep its
            # explanatory prose deterministic so a language model cannot turn
            # metadata confidence into a generic personality claim.
            "explanation": musical_age["fallbackExplanation"],
        },
        "topFive": evidence["topFive"],
        "summary": {
            "headline": language.get("finalRoastHeadline") or "Your repeat button has entered the chat",
            "body": language.get("finalRoastBody") or deterministic_roast_body(evidence),
            "finalLine": language.get("finalLine") or "Keep the soundtrack dramatic and the evidence local.",
            "generationSource": generation_source,
        },
        "backgroundAlbums": evidence["backgroundAlbums"],
        "explainers": evidence["explainers"],
        "generation": {
            "source": generation_source,
            "model": model,
            "promptVersion": prompt_version,
            "generatedAt": generated_at,
            "fallbackReason": language.get("fallbackReason"),
            "durationMs": language.get("durationMs"),
        },
        "analyticsFingerprint": analytics_fingerprint,
        "cacheKey": cache_key,
    }


def report_period(period: dict[str, Any]) -> dict[str, str]:
    return {
        "key": str(period.get("key") or period.get("period") or REPORT_PERIOD),
        "label": str(period.get("label") or "Rolling year"),
        "startDate": str(period.get("startDate") or period.get("start_date") or ""),
        "endDate": str(period.get("endDate") or period.get("end_date") or ""),
        "timezone": str(period.get("timezone") or "UTC"),
    }


def report_genres(taste: dict[str, Any], total_music_plays: int) -> list[dict[str, Any]]:
    shares = list(taste.get("cluster_shares") or [])
    coverage = max(0.0, min(100.0, float(taste.get("coverage", {}).get("genre_coverage_percent") or 0)))
    result = [
        {
            "key": slug(item.get("name")),
            "label": str(item.get("name") or "Other"),
            "percentage": round(max(0.0, float(item.get("share") or 0)), 1),
            "detectedPlays": max(0, round(total_music_plays * float(item.get("share") or 0) / 100)),
        }
        for item in shares
        if item.get("name") and float(item.get("share") or 0) > 0
    ]
    unclassified = round(max(0.0, 100 - coverage), 1)
    if unclassified > 0:
        result.append(
            {
                "key": "other_unclassified",
                "label": "Other / Unclassified",
                "percentage": unclassified,
                "detectedPlays": max(0, round(total_music_plays * unclassified / 100)),
            }
        )
    displayed_total = sum(item["percentage"] for item in result)
    if displayed_total > 100 and result:
        result[-1]["percentage"] = round(max(0, result[-1]["percentage"] - (displayed_total - 100)), 1)
    return result


def report_song(item: dict[str, Any]) -> dict[str, Any]:
    minutes = float(item.get("detected_minutes") or 0)
    return {
        "rank": int(item.get("rank") or 1),
        "albumImageUrl": item.get("album_art_url"),
        "trackImageUrl": item.get("track_image_url"),
        "title": str(item.get("title") or "Unknown song"),
        "artist": str(item.get("artist") or "Unknown artist"),
        "album": item.get("album"),
        "detectedPlays": int(item.get("play_count") or 0),
        "detectedMinutes": minutes,
        "formattedMinutes": str(item.get("detected_minutes_formatted") or f"{minutes:g} min"),
    }


def report_artist(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": int(item.get("rank") or 1),
        "artistImageUrl": item.get("artist_image_url"),
        "name": str(item.get("artist") or "Unknown artist"),
        "detectedPlays": int(item.get("play_count") or 0),
        "uniqueSongs": int(item.get("unique_songs") or 0),
    }


def report_background_albums(albums: list[Any], tracks: list[Any], limit: int = 20) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(album_id: Any, title: Any, artist: Any, image: Any, plays: Any) -> None:
        if len(result) >= limit or not title or not artist or not image:
            return
        identity = str(album_id or f"{slug(title)}::{slug(artist)}").casefold()
        if identity in seen:
            return
        seen.add(identity)
        result.append(
            {
                "albumBrowseId": str(album_id) if album_id else None,
                "albumTitle": str(title),
                "artistName": str(artist),
                "albumImageUrl": str(image),
                "detectedPlays": max(0, int(plays or 0)),
            }
        )

    for item in albums:
        if isinstance(item, dict):
            add(
                item.get("album_id"),
                item.get("album"),
                item.get("artist"),
                item.get("album_image_url") or item.get("thumbnail") or item.get("track_art_fallback"),
                item.get("plays"),
            )
    for item in tracks:
        if isinstance(item, dict):
            # Takeout records often have a video thumbnail before an album cover
            # has been resolved. It is still useful as a visual in the ambient
            # album dome, and later enrichment replaces it with the proper cover.
            add(
                None,
                item.get("album"),
                item.get("artist"),
                item.get("album_art_url") or item.get("track_image_url"),
                item.get("play_count"),
            )
    return result


def deterministic_roast_body(evidence: dict[str, Any]) -> str:
    personality = evidence["personality"]
    genres = [item["label"] for item in evidence["listeningWorld"]["genres"] if item["key"] != "other_unclassified"]
    top_artist = evidence["topFive"]["artists"][0]["name"] if evidence["topFive"]["artists"] else "your favourite artists"
    habit = (personality.get("habits") or ["repeat listening"])[0]
    genre = genres[0].lower() if genres else "carefully chosen sound"
    return (
        f"Your listening taste treats {genre} less like a genre and more like a climate system. "
        f"The strongest clue is {habit.lower()}, while {top_artist} sits near the centre of a rotation that clearly believes favourites should earn permanent residency. "
        "You leave just enough room for discovery to claim this is a living ecosystem, then return to the trusted songs with the confidence of someone reopening a book at the best chapter. "
        "It is dramatic, curated, and remarkably committed to making an ordinary day feel like it has closing credits."
    )


def slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_") or "signal"
