from __future__ import annotations

import os
import secrets
import time
from collections import OrderedDict
from typing import Any

from aiohttp import web


STARTED_AT = time.time()

# Temporary in-memory profile image cache.
# The bot and aiohttp server run in the same process in the current architecture,
# so generated profile images can be exposed through a short-lived public URL.
PROFILE_IMAGE_TTL_SECONDS = max(
    60,
    int(os.getenv("PROFILE_IMAGE_TTL_SECONDS", "900") or 900),
)
PROFILE_IMAGE_CACHE_MAX = max(
    16,
    int(os.getenv("PROFILE_IMAGE_CACHE_MAX", "128") or 128),
)

# token -> (image_bytes, expires_at_monotonic, content_type)
_PROFILE_IMAGE_CACHE: OrderedDict[str, tuple[bytes, float, str]] = OrderedDict()


def _purge_profile_image_cache() -> None:
    now = time.monotonic()

    expired = [
        token
        for token, (_, expires_at, _) in _PROFILE_IMAGE_CACHE.items()
        if expires_at <= now
    ]
    for token in expired:
        _PROFILE_IMAGE_CACHE.pop(token, None)

    while len(_PROFILE_IMAGE_CACHE) > PROFILE_IMAGE_CACHE_MAX:
        _PROFILE_IMAGE_CACHE.popitem(last=False)


def _to_bytes(data: Any) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, bytearray):
        return bytes(data)
    if isinstance(data, memoryview):
        return data.tobytes()
    if hasattr(data, "getvalue"):
        value = data.getvalue()
        if isinstance(value, bytes):
            return value
        return bytes(value)
    raise TypeError("profile image must be bytes-like or expose getvalue()")


def store_profile_image(
    image: Any,
    *,
    content_type: str = "image/png",
    ttl_seconds: int | None = None,
) -> str:
    """Store a generated profile image and return its public route path."""
    image_bytes = _to_bytes(image)
    if not image_bytes:
        raise ValueError("profile image is empty")

    _purge_profile_image_cache()

    ttl = max(
        60,
        int(ttl_seconds or PROFILE_IMAGE_TTL_SECONDS),
    )
    token = secrets.token_urlsafe(24)
    expires_at = time.monotonic() + ttl

    _PROFILE_IMAGE_CACHE[token] = (
        image_bytes,
        expires_at,
        str(content_type or "image/png"),
    )
    _PROFILE_IMAGE_CACHE.move_to_end(token)

    return f"/profile-image/{token}.png"


async def profile_image(request: web.Request) -> web.Response:
    _purge_profile_image_cache()

    token = str(request.match_info.get("token", "") or "")
    cached = _PROFILE_IMAGE_CACHE.get(token)
    if not cached:
        raise web.HTTPNotFound(text="profile image not found or expired")

    image_bytes, expires_at, content_type = cached
    if expires_at <= time.monotonic():
        _PROFILE_IMAGE_CACHE.pop(token, None)
        raise web.HTTPNotFound(text="profile image expired")

    # Mark recently requested images as most recently used.
    _PROFILE_IMAGE_CACHE.move_to_end(token)

    return web.Response(
        body=image_bytes,
        content_type=content_type,
        headers={
            "Cache-Control": f"public, max-age={PROFILE_IMAGE_TTL_SECONDS}, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def health(request: web.Request) -> web.Response:
    uptime = int(time.time() - STARTED_AT)
    _purge_profile_image_cache()

    return web.json_response(
        {
            "status": "ok",
            "service": "BIKA Character Bot",
            "uptime_seconds": uptime,
            "profile_image_cache_items": len(_PROFILE_IMAGE_CACHE),
        }
    )


def create_health_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/profile-image/{token}.png", profile_image)
    return app
