from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    backend_dir = project_root / "backend"
    sys.path.insert(0, str(backend_dir))

    concurrency = int(os.getenv("WEB_CONCURRENCY", os.getenv("SMP_WEB_CONCURRENCY", "1")))
    if concurrency != 1:
        raise SystemExit(
            "Saville Web currently requires exactly one application process. "
            "Background imports are thread-based and SQLite is mounted to one writer."
        )

    os.environ.setdefault("SMP_DEPLOYMENT_MODE", "anonymous")
    os.environ.setdefault("SMP_SERVE_FRONTEND", "true")
    os.environ.setdefault("SMP_DATA_DIR", "/var/lib/saville")
    os.environ.setdefault("SMP_SESSION_COOKIE_SECURE", "true")
    os.environ.setdefault("SMP_SESSION_COOKIE_SAMESITE", "lax")

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(
        "app.main:app",
        app_dir=str(backend_dir),
        host="0.0.0.0",
        port=port,
        workers=1,
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "*"),
        access_log=os.getenv("SMP_ACCESS_LOG", "false").strip().casefold() in {"1", "true", "yes", "on"},
    )


if __name__ == "__main__":
    main()
