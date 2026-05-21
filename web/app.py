from __future__ import annotations

import time
from aiohttp import web

STARTED_AT = time.time()


async def health(request: web.Request) -> web.Response:
    uptime = int(time.time() - STARTED_AT)
    return web.json_response({"status": "ok", "service": "BIKA Character Bot", "uptime_seconds": uptime})


def create_health_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    return app
