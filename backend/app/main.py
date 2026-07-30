from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import routes
from app.config import settings
from app.session import generate_session_id, reset_current_session, set_current_session, valid_session_id


app = FastAPI(
    title="Saville Music Persona API",
    version="0.1.0",
    description="Local-first YouTube Music taste analysis powered by ytmusicapi and Ollama.",
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
    if not settings.anonymous_mode:
        return await call_next(request)
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin not in settings.cors_origins:
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


@app.exception_handler(Exception)
async def unhandled_exception(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "error": "Unexpected local server error",
            "detail": str(exc),
            "code": "internal_error",
        },
    )

