from __future__ import annotations

import time
from typing import Any

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.services.ollama_service import OllamaService, PersonaReportLanguage


class HostedLanguageService:
    """Bounded prose adapter; deterministic analytics remain authoritative."""

    def __init__(self, settings: Settings, validator: OllamaService | None = None) -> None:
        self.settings = settings
        self.validator = validator or OllamaService(settings)

    def status(self) -> dict[str, Any]:
        configured = self.settings.hosted_llm_enabled
        return {
            "configured": configured,
            "provider": self.settings.hosted_llm_provider,
            "model": self.settings.hosted_llm_model or None,
            "message": (
                "Hosted report writer is configured."
                if configured
                else "Hosted report writer is disabled; deterministic report language remains available."
            ),
        }

    def generate_persona_language(self, evidence: dict[str, Any], mode: str = "serious") -> PersonaReportLanguage:
        started = time.monotonic()
        if not self.settings.hosted_llm_enabled:
            return self.validator.fallback_persona_language(evidence, "hosted_llm_not_configured", started)
        prompt = self.validator._build_persona_language_prompt(evidence, mode)  # noqa: SLF001
        try:
            raw = self._request_content(prompt)
            parsed = self.validator.parse_persona_language(raw, evidence)
            parsed.generationSource = "hosted-llm"
            parsed.fallbackReason = None
            parsed.durationMs = max(0, int((time.monotonic() - started) * 1000))
            return parsed
        except httpx.TimeoutException:
            return self.validator.fallback_persona_language(evidence, "hosted_llm_timeout", started)
        except (ValueError, ValidationError):
            return self.validator.fallback_persona_language(evidence, "hosted_llm_invalid_response", started)
        except httpx.HTTPStatusError as exc:
            reason = "hosted_llm_rate_limited" if exc.response.status_code == 429 else "hosted_llm_provider_error"
            return self.validator.fallback_persona_language(evidence, reason, started)
        except Exception:  # noqa: BLE001 - never make a report depend on remote prose
            return self.validator.fallback_persona_language(evidence, "hosted_llm_error", started)

    def _request_content(self, prompt: str) -> str:
        url = f"{self.settings.hosted_llm_base_url}/chat/completions"
        payload = {
            "model": self.settings.hosted_llm_model,
            "messages": [
                {
                    "role": "system",
                    "content": "Return only the requested JSON. Never invent facts beyond the supplied deterministic evidence.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.38,
            "max_tokens": self.settings.hosted_llm_max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        with httpx.Client(timeout=self.settings.hosted_llm_timeout_seconds) as client:
            response = client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.settings.hosted_llm_api_key}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()
        choices = data.get("choices") if isinstance(data, dict) else None
        message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            content = "".join(
                str(item.get("text") or "") for item in content if isinstance(item, dict)
            )
        if not isinstance(content, str) or not content.strip():
            raise ValueError("hosted language response did not include text")
        return content
