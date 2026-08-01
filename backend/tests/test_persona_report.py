from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.analysis.music_character import MUSIC_CHARACTER_CLASSIFIER_VERSION
from app.analysis.music_character import personality_catalogue
from app.analysis.musical_age import AGE_CATEGORIES, age_category_catalogue
from app.analysis.musical_age import MUSICAL_AGE_CALCULATION_VERSION
from app.analysis.period_profile import ANALYTICS_VERSION, GENRE_MAP_VERSION
from app.analysis.persona_report import (
    build_persona_report_evidence,
    compose_persona_report,
    deterministic_roast_body,
    report_background_albums,
    report_genres,
)
from app.analysis.normalizer import normalise_collection
from app.api.routes import (
    PERSONA_REPORT_PROMPT_VERSION,
    PERSONA_REPORT_SCHEMA_VERSION,
    persona_report_cache_key,
    persona_report_fingerprint,
    persona_report_pointer_is_current,
    settings,
)
from app.config import Settings
from app.schemas.responses import PersonaReportResponse
from app.services.ollama_service import OllamaService, PERSONA_LANGUAGE_FORMAT


def test_report_schema_is_v8_and_strict() -> None:
    payload = compose_persona_report(
        evidence(),
        fallback_language(),
        source="youtube",
        mode="serious",
        generated_at="2026-07-23T00:00:00+00:00",
        prompt_version=5,
        model="gemma3:4b",
        analytics_fingerprint="abc",
        cache_key="cache",
    )
    report = PersonaReportResponse.model_validate(payload)
    assert report.schemaVersion == 8
    assert report.period.key == "rolling_year"
    assert report.musicalAge.age == 29
    with pytest.raises(ValidationError):
        PersonaReportResponse.model_validate({**payload, "legacyStory": {}})


def test_persona_report_can_be_generated_for_current_month() -> None:
    normalised = normalise_collection(
        {
            "history": [
                {"videoId": "july", "title": "July Song", "artists": [{"name": "Oasis"}], "played": "2026-07-05", "duration_seconds": 180},
                {"videoId": "june", "title": "June Song", "artists": [{"name": "Oasis"}], "played": "2026-06-05", "duration_seconds": 180},
            ]
        },
        today=date(2026, 7, 7),
    )

    report = build_persona_report_evidence(
        normalised,
        "Asia/Kuala_Lumpur",
        "this_month",
        today=date(2026, 7, 7),
    )

    assert report["period"]["key"] == "this_month"
    assert report["topFive"]["songs"][0]["title"] == "July Song"
    assert report["topFive"]["songs"][0]["detectedPlays"] == 1
    assert report["musicalAge"]["sourcePeriod"]["key"] == "this_month"


def test_report_explainers_are_public_catalogues_and_current_data_is_typed() -> None:
    catalogue = age_category_catalogue()
    assert len(catalogue) == len(AGE_CATEGORIES)
    assert catalogue[0]["minAge"] == 0 and catalogue[-1]["maxAge"] == 100
    personalities = personality_catalogue()
    assert {"id", "name", "category", "profile"} == set(personalities[0])
    assert "score" not in personalities[0] and "priority" not in personalities[0]


def test_old_cached_report_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PersonaReportResponse.model_validate({"schemaVersion": 3, "personaName": "Legacy"})


def test_cache_key_includes_every_invalidation_version() -> None:
    fingerprint = persona_report_fingerprint(evidence())
    changed = evidence()
    changed["musicalAge"] = {**changed["musicalAge"], "age": 30}
    assert fingerprint != persona_report_fingerprint(changed)
    key = persona_report_cache_key("youtube", "serious", fingerprint)
    assert f"v{PERSONA_REPORT_SCHEMA_VERSION}" in key
    assert f"prompt{PERSONA_REPORT_PROMPT_VERSION}" in key
    assert f"classifier{MUSIC_CHARACTER_CLASSIFIER_VERSION}" in key
    assert "rolling_year" in key and fingerprint in key


def test_report_pointer_requires_current_genre_map_version() -> None:
    pointer = {
        "cacheKey": "report-cache",
        "source": "youtube",
        "period": "rolling_year",
        "schemaVersion": PERSONA_REPORT_SCHEMA_VERSION,
        "promptVersion": PERSONA_REPORT_PROMPT_VERSION,
        "analyticsVersion": ANALYTICS_VERSION,
        "genreMapVersion": GENRE_MAP_VERSION,
        "musicalAgeCalculationVersion": MUSICAL_AGE_CALCULATION_VERSION,
        "personalityClassifierVersion": MUSIC_CHARACTER_CLASSIFIER_VERSION,
        "model": settings.ollama_model,
        "normalisedUpdatedAt": "2026-07-25T00:00:00+00:00",
    }
    assert persona_report_pointer_is_current(pointer, "youtube", pointer["normalisedUpdatedAt"])
    assert not persona_report_pointer_is_current(
        {**pointer, "genreMapVersion": GENRE_MAP_VERSION - 1},
        "youtube",
        pointer["normalisedUpdatedAt"],
    )


def test_genre_percentages_include_unclassified_without_exceeding_total() -> None:
    genres = report_genres(
        {
            "cluster_shares": [
                {"name": "Alternative / Rock", "share": 42.1},
                {"name": "Cinematic", "share": 17.9},
            ],
            "coverage": {"genre_coverage_percent": 60},
        },
        1000,
    )
    assert sum(item["percentage"] for item in genres) == 100
    assert genres[-1]["label"] == "Other / Unclassified"
    assert genres[0]["detectedPlays"] == 421


def test_report_genres_do_not_hide_a_seventh_classified_family() -> None:
    shares = [{"name": f"Family {index}", "share": 5} for index in range(1, 8)]
    genres = report_genres({"cluster_shares": shares, "coverage": {"genre_coverage_percent": 35}}, 100)
    assert [item["label"] for item in genres[:-1]] == [f"Family {index}" for index in range(1, 8)]
    assert genres[-1]["label"] == "Other / Unclassified"


def test_background_albums_dedupe_and_skip_missing_artwork() -> None:
    albums = report_background_albums(
        [
            {"album_id": "A", "album": "Album", "artist": "Artist", "album_image_url": "https://img/a.jpg", "plays": 8},
            {"album_id": "A", "album": "Album", "artist": "Artist", "album_image_url": "https://img/a2.jpg", "plays": 4},
            {"album_id": "B", "album": "No Art", "artist": "Artist", "album_image_url": None, "plays": 2},
        ],
        [{"album": "Second", "artist": "Artist", "album_art_url": "https://img/b.jpg", "play_count": 3}],
    )
    assert [item["albumTitle"] for item in albums] == ["Album", "Second"]
    assert all(item["albumImageUrl"] for item in albums)


def test_background_albums_use_available_takeout_thumbnail_fallbacks() -> None:
    albums = report_background_albums(
        [{"album": "Album", "artist": "Artist", "thumbnail": "https://img/album-thumb.jpg", "plays": 2}],
        [{"album": "Second", "artist": "Artist", "track_image_url": "https://img/track-thumb.jpg", "play_count": 3}],
    )
    assert [(item["albumTitle"], item["albumImageUrl"]) for item in albums] == [
        ("Album", "https://img/album-thumb.jpg"),
        ("Second", "https://img/track-thumb.jpg"),
    ]


def test_gemma_language_validation_accepts_bounded_prose() -> None:
    service = OllamaService(Settings())
    parsed = service.parse_persona_language(valid_language_json(), evidence()["languageEvidence"])
    assert parsed.generationSource == "gemma"
    assert len(parsed.finalRoastBody.split()) >= 70


def test_gemma_language_rejects_numbers_and_unknown_artist() -> None:
    service = OllamaService(Settings())
    with pytest.raises(ValueError, match="numeric"):
        service.parse_persona_language(valid_language_json().replace("carefully", "carefully 42", 1), evidence()["languageEvidence"])
    with pytest.raises(ValueError, match="unknown artist"):
        service.parse_persona_language(valid_language_json().replace("the familiar rotation", "the band Invented Artist"), evidence()["languageEvidence"])


def test_gemma_language_keeps_valid_sections_when_the_closing_body_is_too_short() -> None:
    service = OllamaService(Settings())
    raw = valid_language_json().replace(
        "Your music taste treats atmosphere like a basic utility and the repeat button like a trusted advisor. Intensity is welcome, but only when it arrives with melody, drama, and enough emotional architecture to survive another listen. Discovery gets invited in, shown around politely, and then asked whether it can match the standards set by the familiar rotation. There is a reflective, cinematic streak running through everything, plus a suspicious talent for making an ordinary commute feel like the final scene of a film. You call it curation; the favourites call it permanent residency.",
        "A carefully built listening world.",
    )
    parsed = service.parse_persona_language(raw, evidence()["languageEvidence"])
    assert parsed.generationSource == "gemma"
    assert len(parsed.finalRoastBody.split()) >= 70


def test_gemma_unavailable_and_malformed_json_have_complete_fallbacks() -> None:
    unavailable = FakeLanguageService(status={"reachable": False, "model_installed": False})
    report = unavailable.generate_persona_language(evidence()["languageEvidence"])
    assert report.generationSource == "fallback"
    assert report.fallbackReason == "ollama_unavailable"
    malformed = FakeLanguageService(response={"response": "not json"})
    report = malformed.generate_persona_language(evidence()["languageEvidence"])
    assert report.generationSource == "fallback"
    assert report.fallbackReason == "invalid_language_json"


def test_gemma_persona_generation_uses_constrained_json_schema() -> None:
    class CapturingLanguageService(FakeLanguageService):
        payload: dict[str, object] | None = None

        def _request_json(self, method: str, path: str, payload: dict[str, object] | None = None, timeout: float = 10.0) -> dict[str, object]:
            self.payload = payload
            return self.response

    service = CapturingLanguageService()
    report = service.generate_persona_language(evidence()["languageEvidence"])

    assert report.generationSource == "gemma"
    assert service.payload and service.payload["format"] == PERSONA_LANGUAGE_FORMAT


def test_stalled_gemma_returns_the_complete_report_fallback() -> None:
    stalled = FakeLanguageService()
    stalled._request_json = lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("stalled"))  # type: ignore[method-assign]
    report = stalled.generate_persona_language(evidence()["languageEvidence"])
    assert report.generationSource == "fallback"
    assert report.fallbackReason == "ollama_timeout"


def test_final_roast_fallback_is_natural_and_metric_free() -> None:
    roast = deterministic_roast_body(evidence())
    assert len(roast.split()) >= 70
    assert not any(character.isdigit() for character in roast)
    assert "climate system" in roast


class FakeLanguageService(OllamaService):
    def __init__(self, response: dict[str, object] | None = None, status: dict[str, object] | None = None) -> None:
        super().__init__(Settings())
        self.response = response or {"response": valid_language_json()}
        self.status_payload = status or {"reachable": True, "model_installed": True}

    def status(self) -> dict[str, object]:
        return self.status_payload

    def _request_json(self, method: str, path: str, payload: dict[str, object] | None = None, timeout: float = 10.0) -> dict[str, object]:
        return self.response


def evidence() -> dict[str, object]:
    period = {"key": "rolling_year", "label": "Rolling year", "startDate": "2025-07-24", "endDate": "2026-07-23", "timezone": "Asia/Kuala_Lumpur"}
    return {
        "period": period,
        "personality": {
            "id": "main_character_rain_scene",
            "title": "The Main Character in a Rain Scene",
            "fallbackDescription": "Atmospheric songs become places worth revisiting.",
            "fallbackRoast": "The walk home always has closing credits.",
            "confidence": 0.82,
            "habits": ["Comfort Repeater"],
        },
        "listeningWorld": {"detectedMinutes": 100.0, "formattedTime": "1 hr 40 min", "durationCoverage": 0.5, "genreCoverage": 0.6, "genres": [{"key": "alternative", "label": "Alternative", "percentage": 60.0, "detectedPlays": 60}], "interpretation": "Alternative sound leads."},
        "musicalAge": {"age": 29, "likelyMin": 25, "likelyMax": 33, "title": "The Y2K Archive", "confidence": 0.76, "confidenceLabel": "Good confidence", "fallbackExplanation": "Release years centre the rotation.", "typicalRange": [25, 33], "weightedMedianReleaseYear": 1997, "dominantDecade": "1990s", "releaseYearCoverage": 75.0, "sourcePeriod": period},
        "topFive": {"songs": [], "artists": [{"rank": 1, "artistImageUrl": None, "name": "Known Artist", "detectedPlays": 10, "uniqueSongs": 3}]},
        "backgroundAlbums": [],
        "explainers": {"musicalAges": [{"minAge": 0, "maxAge": 100, "title": "The Y2K Archive", "summary": "Summary"}], "personalities": [{"id": "main_character_rain_scene", "name": "The Main Character in a Rain Scene", "category": "sound", "profile": "Profile"}]},
        "languageEvidence": {"personality": {"id": "main_character_rain_scene", "title": "The Main Character in a Rain Scene"}, "strongestSignals": ["atmospheric signal"], "knownArtists": ["Known Artist"], "knownGenres": ["Alternative"]},
    }


def fallback_language() -> dict[str, object]:
    return {"generationSource": "fallback", "fallbackReason": "test", "durationMs": 1}


def valid_language_json() -> str:
    return """{
      "openingDescription": "You turn the familiar rotation into a weather system and keep the chorus close until it earns permanent residency.",
      "personalityRoast": "Your repeat button has a more stable career than most people.",
      "musicalAgeExplanation": "Familiar anchors and selective discovery share a carefully kept rotation. Album depth gives the estimate its shape without pretending it is a physical age.",
      "finalRoastHeadline": "Your soundtrack has permanent residents",
      "finalRoastBody": "Your music taste treats atmosphere like a basic utility and the repeat button like a trusted advisor. Intensity is welcome, but only when it arrives with melody, drama, and enough emotional architecture to survive another listen. Discovery gets invited in, shown around politely, and then asked whether it can match the standards set by the familiar rotation. There is a reflective, cinematic streak running through everything, plus a suspicious talent for making an ordinary commute feel like the final scene of a film. You call it curation; the favourites call it permanent residency.",
      "finalLine": "Keep the soundtrack dramatic and the evidence local."
    }"""
