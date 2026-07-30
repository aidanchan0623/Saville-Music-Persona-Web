from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.api import routes
from app.config import settings
from app.session import generate_session_id, reset_current_session, set_current_session, valid_session_id


app = FastAPI(
    title="Saville Music Persona Web API",
    version="0.4.0",
    description="Anonymous hosted music-history analysis for Google Takeout and Spotify exports.",
)
logger = logging.getLogger("saville.session_cleanup")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def anonymous_session_boundary(request: Request, call_next):
    """Issue one opaque browser session before any user-owned cache is read."""
    is_api_request = request.url.path == "/api" or request.url.path.startswith("/api/")
    if not settings.anonymous_mode or not is_api_request:
        return await call_next(request)
    if request.url.path in {"/api/health", "/api/ready"}:
        return await call_next(request)
    origin = request.headers.get("origin")
    same_origin = bool(origin and origin.rstrip("/") == str(request.base_url).rstrip("/"))
    if (
        request.method not in {"GET", "HEAD", "OPTIONS"}
        and origin
        and origin not in settings.cors_origins
        and not (settings.serve_frontend and same_origin)
    ):
        return JSONResponse(
            status_code=403,
            content={
                "error": "Origin not allowed",
                "detail": "This anonymous session only accepts writes from the configured frontend.",
                "code": "origin_not_allowed",
            },
        )
    supplied_session = request.cookies.get(settings.session_cookie_name)
    session_id = supplied_session if valid_session_id(supplied_session) else generate_session_id()
    supplied_namespace = f"session:{session_id}"
    if supplied_session and routes.current_session_cleanup().session_expired(supplied_namespace):
        routes.current_session_cleanup().purge_namespace(supplied_namespace)
        session_id = generate_session_id()
    token = set_current_session(session_id)
    try:
        response = await call_next(request)
        try:
            routes.current_session_cleanup().cleanup_if_due(exclude={f"session:{session_id}"})
        except Exception:  # noqa: BLE001
            logger.exception("Anonymous session cleanup failed; the request itself remains valid.")
    finally:
        reset_current_session(token)
    if supplied_session != session_id:
        response.set_cookie(
            key=settings.session_cookie_name,
            value=session_id,
            max_age=settings.session_ttl_hours * 60 * 60,
            expires=datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours),
            httponly=True,
            secure=settings.session_cookie_secure,
            samesite=settings.session_cookie_samesite,
            path="/",
        )
    response.headers["Cache-Control"] = "no-store"
    return response

app.include_router(routes.router)


if settings.serve_frontend and (settings.frontend_dist_dir / "index.html").is_file():
    frontend_root = settings.frontend_dist_dir.resolve()

    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    def hosted_frontend(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        requested = (frontend_root / full_path).resolve()
        if requested.is_file() and (requested == frontend_root or frontend_root in requested.parents):
            cache_control = "public, max-age=31536000, immutable" if full_path.startswith("assets/") else "no-cache"
            return FileResponse(requested, headers={"Cache-Control": cache_control})
        return FileResponse(frontend_root / "index.html", headers={"Cache-Control": "no-cache"})


@app.exception_handler(Exception)
async def unhandled_exception(_: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled request failure", exc_info=(type(exc), exc, exc.__traceback__))
    return JSONResponse(
        status_code=500,
        content={
            "error": "Unexpected server error",
            "detail": "The request could not be completed." if settings.anonymous_mode else str(exc),
            "code": "internal_error",
        },
    )

