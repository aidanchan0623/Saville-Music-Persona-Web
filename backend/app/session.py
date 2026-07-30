from __future__ import annotations

import re
import secrets
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator


SESSION_ID_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_session_id: ContextVar[str | None] = ContextVar("smp_session_id", default=None)


def generate_session_id() -> str:
    return secrets.token_hex(32)


def valid_session_id(value: str | None) -> bool:
    return bool(value and SESSION_ID_PATTERN.fullmatch(value))


def current_session_id() -> str | None:
    return _session_id.get()


def current_session_namespace() -> str | None:
    session_id = current_session_id()
    return f"session:{session_id}" if session_id else None


def set_current_session(session_id: str | None) -> Token[str | None]:
    return _session_id.set(session_id)


def reset_current_session(token: Token[str | None]) -> None:
    _session_id.reset(token)


@contextmanager
def session_scope(session_id: str | None) -> Iterator[None]:
    token = set_current_session(session_id)
    try:
        yield
    finally:
        reset_current_session(token)


SHARED_CACHE_KEYS = {
    "album_image_cache",
    "album_image_cache_v1",
    "artist_image_cache",
    "artist_image_cache_v2",
    "duration_cache",
    "genre_metadata_cache",
    "release_year_cache_v1",
    "track_metadata_cache_v1",
    "usage:hosted_llm_global",
}


def is_shared_cache_key(key: str) -> bool:
    """Music metadata is reusable; listening profiles and jobs are not."""
    return key in SHARED_CACHE_KEYS
