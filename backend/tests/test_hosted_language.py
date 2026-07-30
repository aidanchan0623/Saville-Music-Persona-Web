from __future__ import annotations

from app.config import Settings
from app.services.hosted_language_service import HostedLanguageService


VALID_LANGUAGE = """{
  "openingDescription": "You turn the familiar rotation into a weather system and keep every chorus close until it earns permanent residency.",
  "personalityRoast": "Your repeat button has a more stable career than most executives.",
  "musicalAgeExplanation": "Familiar anchors and selective discovery share a carefully kept rotation. Album depth gives the estimate shape without pretending it is a physical age.",
  "finalRoastHeadline": "Your soundtrack has permanent residents",
  "finalRoastBody": "Your music taste treats atmosphere like a basic utility and the repeat button like a trusted adviser. Intensity is welcome only when it arrives with melody, drama, and enough emotional architecture to survive another listen. Discovery gets invited in, shown around politely, and then asked whether it can match the standards set by the familiar rotation. There is a reflective cinematic streak running through everything, plus a suspicious talent for making an ordinary commute feel like the final scene of a film. You call it curation; the favourites call it permanent residency with excellent tenant protections and an unusually strict guest list.",
  "finalLine": "Keep the soundtrack dramatic and the evidence private."
}"""


def evidence() -> dict[str, object]:
    return {
        "personality": {"id": "main_character", "title": "The Main Character"},
        "strongestSignals": ["repeat listening", "album depth"],
        "knownArtists": ["Known Artist"],
        "knownGenres": ["Alternative"],
        "musicalAge": {"isResolved": True},
    }


def configured_settings() -> Settings:
    value = Settings()
    value.deployment_mode = "anonymous"
    value.hosted_llm_provider = "openai-compatible"
    value.hosted_llm_api_key = "server-only-test-key"
    value.hosted_llm_model = "test-writer"
    return value


def test_hosted_writer_uses_existing_strict_language_validation() -> None:
    service = HostedLanguageService(configured_settings())
    service._request_content = lambda _prompt: VALID_LANGUAGE  # type: ignore[method-assign]

    result = service.generate_persona_language(evidence(), "roast")

    assert result.generationSource == "hosted-llm"
    assert result.fallbackReason is None
    assert len(result.finalRoastBody.split()) >= 70


def test_hosted_writer_fails_closed_to_deterministic_language() -> None:
    service = HostedLanguageService(configured_settings())
    service._request_content = lambda _prompt: "not json"  # type: ignore[method-assign]

    result = service.generate_persona_language(evidence(), "roast")

    assert result.generationSource == "fallback"
    assert result.fallbackReason == "hosted_llm_invalid_response"
    assert result.finalRoastBody


def test_disabled_hosted_writer_never_attempts_a_remote_request() -> None:
    settings = configured_settings()
    settings.hosted_llm_provider = "disabled"
    service = HostedLanguageService(settings)

    def unexpected(_prompt: str) -> str:
        raise AssertionError("remote request must not run")

    service._request_content = unexpected  # type: ignore[method-assign]
    result = service.generate_persona_language(evidence())

    assert result.generationSource == "fallback"
    assert result.fallbackReason == "hosted_llm_not_configured"
