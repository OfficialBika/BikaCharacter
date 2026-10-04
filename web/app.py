from __future__ import annotations

import hashlib
import os
import time
from collections import OrderedDict
from typing import Any

from aiohttp import web

STARTED_AT = time.time()
PROFILE_IMAGE_TTL_SECONDS = max(60, int(os.getenv("PROFILE_IMAGE_TTL_SECONDS", "900") or 900))
PROFILE_IMAGE_CACHE_MAX = max(16, int(os.getenv("PROFILE_IMAGE_CACHE_MAX", "128") or 128))
PROFILE_IMAGE_CACHE_MAX_BYTES = max(
    4 * 1024 * 1024,
    int(os.getenv("PROFILE_IMAGE_CACHE_MAX_BYTES", str(64 * 1024 * 1024)) or 64 * 1024 * 1024),
)

_PROFILE_IMAGE_CACHE: OrderedDict[str, tuple[bytes, float, str]] = OrderedDict()
_PROFILE_IMAGE_CACHE_BYTES = 0


def _purge_profile_image_cache() -> None:
    global _PROFILE_IMAGE_CACHE_BYTES
    now = time.monotonic()
    expired = [
        token for token, (_, expires_at, _) in _PROFILE_IMAGE_CACHE.items()
        if expires_at <= now
    ]
    for token in expired:
        item = _PROFILE_IMAGE_CACHE.pop(token, None)
        if item:
            _PROFILE_IMAGE_CACHE_BYTES -= len(item[0])

    while _PROFILE_IMAGE_CACHE and (
        len(_PROFILE_IMAGE_CACHE) > PROFILE_IMAGE_CACHE_MAX
        or _PROFILE_IMAGE_CACHE_BYTES > PROFILE_IMAGE_CACHE_MAX_BYTES
    ):
        _, item = _PROFILE_IMAGE_CACHE.popitem(last=False)
        _PROFILE_IMAGE_CACHE_BYTES -= len(item[0])
    _PROFILE_IMAGE_CACHE_BYTES = max(0, _PROFILE_IMAGE_CACHE_BYTES)


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
    content_type: str = "image/jpeg",
    ttl_seconds: int | None = None,
) -> str:
    """Content-addressed, bounded profile image cache."""
    global _PROFILE_IMAGE_CACHE_BYTES
    image_bytes = _to_bytes(image)
    if not image_bytes:
        raise ValueError("profile image is empty")
    if len(image_bytes) > PROFILE_IMAGE_CACHE_MAX_BYTES:
        raise ValueError("profile image exceeds cache memory ceiling")

    _purge_profile_image_cache()
    ttl = max(60, int(ttl_seconds or PROFILE_IMAGE_TTL_SECONDS))
    token = hashlib.sha256(image_bytes).hexdigest()

    old = _PROFILE_IMAGE_CACHE.pop(token, None)
    if old:
        _PROFILE_IMAGE_CACHE_BYTES -= len(old[0])

    _PROFILE_IMAGE_CACHE[token] = (
        image_bytes,
        time.monotonic() + ttl,
        str(content_type or "image/jpeg"),
    )
    _PROFILE_IMAGE_CACHE_BYTES += len(image_bytes)
    _PROFILE_IMAGE_CACHE.move_to_end(token)
    _purge_profile_image_cache()
    return f"/profile-image/{token}.jpg"


async def profile_image(request: web.Request) -> web.Response:
    global _PROFILE_IMAGE_CACHE_BYTES
    _purge_profile_image_cache()
    token = str(request.match_info.get("token", "") or "")
    cached = _PROFILE_IMAGE_CACHE.get(token)
    if not cached:
        raise web.HTTPNotFound(text="profile image not found or expired")

    image_bytes, expires_at, content_type = cached
    if expires_at <= time.monotonic():
        item = _PROFILE_IMAGE_CACHE.pop(token, None)
        if item:
            _PROFILE_IMAGE_CACHE_BYTES = max(0, _PROFILE_IMAGE_CACHE_BYTES - len(item[0]))
        raise web.HTTPNotFound(text="profile image expired")

    _PROFILE_IMAGE_CACHE.move_to_end(token)
    return web.Response(
        body=image_bytes,
        content_type=content_type,
        headers={
            "Cache-Control": f"public, max-age={PROFILE_IMAGE_TTL_SECONDS}, immutable",
            "Content-Disposition": 'inline; filename="profile.jpg"',
            "X-Content-Type-Options": "nosniff",
        },
    )


async def health(request: web.Request) -> web.Response:
    _purge_profile_image_cache()
    return web.json_response(
        {
            "status": "ok",
            "service": "BIKA Character Bot",
            "uptime_seconds": int(time.time() - STARTED_AT),
            "profile_image_cache_items": len(_PROFILE_IMAGE_CACHE),
            "profile_image_cache_bytes": _PROFILE_IMAGE_CACHE_BYTES,
            "profile_image_cache_max_bytes": PROFILE_IMAGE_CACHE_MAX_BYTES,
        }
    )


def create_health_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/profile-image/{token}.jpg", profile_image)
    return app
