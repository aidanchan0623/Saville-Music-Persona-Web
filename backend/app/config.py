from __future__ import annotations

import os
from pathlib import Path


def load_private_env(private_dir: Path) -> None:
    env_path = private_dir / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


class Settings:
    """Small settings object that keeps secrets out of source control."""

    def __init__(self) -> None:
        self.backend_dir = Path(__file__).resolve().parents[1]
        self.project_root = Path(__file__).resolve().parents[2]
        self.private_dir = Path(os.getenv("SMP_PRIVATE_DIR", self.backend_dir / "private"))
        # This repository is the hosted edition. The separate desktop repo
        # remains local-first, while an omitted production variable here must
        # fail closed into anonymous, account-disconnected behavior.
        self.deployment_mode = os.getenv("SMP_DEPLOYMENT_MODE", "anonymous").strip().casefold()
        if self.deployment_mode not in {"local", "anonymous"}:
            raise ValueError("SMP_DEPLOYMENT_MODE must be local or anonymous")
        # Hosted anonymous deployments must never inherit account credentials
        # from a bundled private directory. Deployment secrets come only from
        # the host environment, and account-connection routes remain disabled.
        if self.deployment_mode == "local":
            load_private_env(self.private_dir)
        self.session_cookie_name = os.getenv("SMP_SESSION_COOKIE_NAME", "smp_session")
        self.session_ttl_hours = max(1, int(os.getenv("SMP_SESSION_TTL_HOURS", "24")))
        self.session_cookie_secure = os.getenv(
            "SMP_SESSION_COOKIE_SECURE",
            "true" if self.deployment_mode == "anonymous" else "false",
        ).strip().casefold() in {"1", "true", "yes", "on"}
        self.session_cookie_samesite = os.getenv("SMP_SESSION_COOKIE_SAMESITE", "lax").strip().casefold()
        if self.session_cookie_samesite not in {"lax", "strict", "none"}:
            raise ValueError("SMP_SESSION_COOKIE_SAMESITE must be lax, strict, or none")
        if self.session_cookie_samesite == "none" and not self.session_cookie_secure:
            raise ValueError("SMP_SESSION_COOKIE_SECURE must be true when SMP_SESSION_COOKIE_SAMESITE=none")
        self.session_cleanup_interval_seconds = max(
            30,
            int(os.getenv("SMP_SESSION_CLEANUP_INTERVAL_SECONDS", "300")),
        )
        self.operations_token = os.getenv("SMP_OPERATIONS_TOKEN", "").strip()
        configured_hosts = [value.strip() for value in os.getenv("SMP_ALLOWED_HOSTS", "*").split(",") if value.strip()]
        self.allowed_hosts = configured_hosts or ["*"]
        self.anonymous_max_upload_bytes = max(
            1024 * 1024,
            int(os.getenv("SMP_ANONYMOUS_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024))),
        )
        self.anonymous_max_events = max(1_000, int(os.getenv("SMP_ANONYMOUS_MAX_EVENTS", "250000")))
        self.anonymous_uploads_per_hour = max(1, int(os.getenv("SMP_ANONYMOUS_UPLOADS_PER_HOUR", "4")))
        self.anonymous_max_concurrent_imports = max(
            1,
            int(os.getenv("SMP_ANONYMOUS_MAX_CONCURRENT_IMPORTS", "1")),
        )
        self.data_dir = Path(os.getenv("SMP_DATA_DIR", self.project_root / "data"))
        self.raw_dir = self.data_dir / "raw"
        self.db_path = Path(os.getenv("SMP_DB_PATH", self.data_dir / "saville_music_persona.db"))
        self.serve_frontend = os.getenv(
            "SMP_SERVE_FRONTEND",
            "true" if self.anonymous_mode else "false",
        ).strip().casefold() in {"1", "true", "yes", "on"}
        self.frontend_dist_dir = Path(
            os.getenv("SMP_FRONTEND_DIST_DIR", self.project_root / "frontend" / "dist")
        )
        if not self.frontend_dist_dir.is_absolute():
            self.frontend_dist_dir = self.project_root / self.frontend_dist_dir
        self.ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "gemma3:4b")
        self.ollama_generate_timeout_seconds = float(os.getenv("OLLAMA_GENERATE_TIMEOUT_SECONDS", "240"))
        self.hosted_llm_provider = os.getenv("SMP_HOSTED_LLM_PROVIDER", "disabled").strip().casefold()
        if self.hosted_llm_provider not in {"disabled", "openai-compatible"}:
            raise ValueError("SMP_HOSTED_LLM_PROVIDER must be disabled or openai-compatible")
        self.hosted_llm_base_url = os.getenv("SMP_HOSTED_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.hosted_llm_api_key = os.getenv("SMP_HOSTED_LLM_API_KEY", "").strip()
        self.hosted_llm_model = os.getenv("SMP_HOSTED_LLM_MODEL", "").strip()
        self.hosted_llm_timeout_seconds = min(45.0, max(3.0, float(os.getenv("SMP_HOSTED_LLM_TIMEOUT_SECONDS", "20"))))
        self.hosted_llm_max_output_tokens = min(1200, max(256, int(os.getenv("SMP_HOSTED_LLM_MAX_OUTPUT_TOKENS", "700"))))
        self.hosted_llm_requests_per_session_hour = max(1, int(os.getenv("SMP_HOSTED_LLM_REQUESTS_PER_SESSION_HOUR", "4")))
        self.hosted_llm_requests_global_day = max(1, int(os.getenv("SMP_HOSTED_LLM_REQUESTS_GLOBAL_DAY", "200")))
        self.report_generation_timeout_seconds = min(60, max(10, int(os.getenv("SMP_REPORT_GENERATION_TIMEOUT_SECONDS", "35"))))
        self.anonymous_max_concurrent_reports = max(1, int(os.getenv("SMP_ANONYMOUS_MAX_CONCURRENT_REPORTS", "2")))
        self.local_timezone = os.getenv("SMP_LOCAL_TIMEZONE", "Asia/Kuala_Lumpur")
        self.duration_enrichment_limit = int(os.getenv("SMP_DURATION_ENRICHMENT_LIMIT", "1000"))
        # Public InnerTube duration lookups are sequential. Keep anonymous
        # batches deliberately small so a free-tier host can checkpoint and
        # rebuild before its process is recycled. Local installs retain the
        # larger batch because they are not subject to hosted restarts.
        self.duration_public_batch_limit = max(
            1,
            int(
                os.getenv(
                    "SMP_DURATION_PUBLIC_BATCH_LIMIT",
                    "10" if self.anonymous_mode else "100",
                )
            ),
        )
        self.youtube_data_api_key = os.getenv("YOUTUBE_DATA_API_KEY", "").strip()
        self.duration_enrichment_timeout_seconds = int(os.getenv("SMP_DURATION_ENRICHMENT_TIMEOUT_SECONDS", "300"))
        # Exact MusicBrainz lookups are rate-limited and checkpointed after
        # every artist. A larger bounded batch materially improves a fresh
        # multi-user import while remaining below the five-minute job budget.
        self.genre_enrichment_limit = int(os.getenv("SMP_GENRE_ENRICHMENT_LIMIT", "100"))
        self.recording_genre_enrichment_limit = int(os.getenv("SMP_RECORDING_GENRE_ENRICHMENT_LIMIT", "60"))
        self.genre_enrichment_timeout_seconds = int(os.getenv("SMP_GENRE_ENRICHMENT_TIMEOUT_SECONDS", "300"))
        self.release_year_enrichment_limit = int(
            os.getenv("SMP_RELEASE_YEAR_ENRICHMENT_LIMIT", "12" if self.anonymous_mode else "50")
        )
        self.track_metadata_enrichment_limit = int(
            os.getenv("SMP_TRACK_METADATA_ENRICHMENT_LIMIT", "20" if self.anonymous_mode else "100")
        )
        self.takeout_max_upload_bytes = int(os.getenv("SMP_TAKEOUT_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024)))
        self.takeout_import_timeout_seconds = int(os.getenv("SMP_TAKEOUT_IMPORT_TIMEOUT_SECONDS", "1200"))
        self.refresh_timeout_seconds = int(os.getenv("SMP_REFRESH_TIMEOUT_SECONDS", "600"))
        auth_default = self.private_dir / "oauth.json"
        self.ytmusic_auth_file = Path(os.getenv("YTMUSIC_AUTH_FILE", auth_default))
        if not self.ytmusic_auth_file.is_absolute():
            self.ytmusic_auth_file = self.project_root / self.ytmusic_auth_file
        browser_default = self.private_dir / "browser.json"
        self.ytmusic_browser_auth_file = Path(os.getenv("YTMUSIC_BROWSER_AUTH_FILE", browser_default))
        if not self.ytmusic_browser_auth_file.is_absolute():
            self.ytmusic_browser_auth_file = self.project_root / self.ytmusic_browser_auth_file
        self.ytmusic_client_id = os.getenv("YTMUSIC_OAUTH_CLIENT_ID", "")
        self.ytmusic_client_secret = os.getenv("YTMUSIC_OAUTH_CLIENT_SECRET", "")
        self.spotify_client_id = os.getenv("SPOTIFY_CLIENT_ID", "")
        self.spotify_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "")
        self.spotify_redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8000/api/spotify/callback")
        self.frontend_url = os.getenv("SMP_FRONTEND_URL", "http://localhost:5173")
        configured_origins = [value.strip() for value in os.getenv("SMP_CORS_ORIGINS", "").split(",") if value.strip()]
        self.cors_origins = configured_origins or [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ]

    @property
    def anonymous_mode(self) -> bool:
        return self.deployment_mode == "anonymous"

    @property
    def effective_upload_limit_bytes(self) -> int:
        if self.anonymous_mode:
            return min(self.takeout_max_upload_bytes, self.anonymous_max_upload_bytes)
        return self.takeout_max_upload_bytes

    @property
    def hosted_llm_enabled(self) -> bool:
        return bool(
            self.anonymous_mode
            and self.hosted_llm_provider != "disabled"
            and self.hosted_llm_api_key
            and self.hosted_llm_model
        )

    def ensure_local_dirs(self) -> None:
        if not self.anonymous_mode:
            self.private_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
