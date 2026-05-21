from __future__ import annotations

import asyncio
import signal

from aiohttp import web
from telegram import BotCommand, Update
from telegram.ext import Application, ApplicationBuilder, ContextTypes

from config import (
    BOT_TOKEN,
    MONGODB_URI,
    PORT,
    RUN_MODE,
    WEBHOOK_DROP_PENDING_UPDATES,
    WEBHOOK_PATH,
    WEBHOOK_SECRET_TOKEN,
    WEBHOOK_URL,
)
from database.mongodb import close_db, init_db
from handlers import register_handlers
from web.app import create_health_app


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print("BOT ERROR:", repr(context.error))
    if isinstance(update, Update) and update.effective_chat and update.effective_chat.type == "private":
        try:
            await update.effective_message.reply_text("⚠️ Something went wrong. Please try again.")
        except Exception:
            pass


async def register_commands(app: Application) -> None:
    await app.bot.set_my_commands(
        [
            BotCommand("harem", "Display your harem"),
            BotCommand("profile", "See your profile"),
            BotCommand("fav", "Set or show favourite character"),
            BotCommand("check", "Check character by ID"),
            BotCommand("bika", "Claim spawned character"),
            BotCommand("hmode", "Change harem view"),
            BotCommand("topgroup", "Top groups by catches"),
            BotCommand("gtop", "Global top harem users"),
            BotCommand("todaygtop", "Today top catchers"),
            BotCommand("mylimit", "Check daily catch limit"),
        ]
    )


def _normalize_webhook_path(path: str) -> str:
    path = (path or "/webhook").strip()
    if not path.startswith("/"):
        path = "/" + path
    return path


async def start_web_server(app_bot: Application | None = None) -> web.AppRunner:
    """Start aiohttp server for Render health checks and optional Telegram webhook."""
    health_app = create_health_app()

    if app_bot is not None:
        webhook_path = _normalize_webhook_path(WEBHOOK_PATH)

        async def telegram_webhook(request: web.Request) -> web.Response:
            if WEBHOOK_SECRET_TOKEN:
                incoming_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
                if incoming_secret != WEBHOOK_SECRET_TOKEN:
                    return web.Response(status=403, text="forbidden")

            try:
                data = await request.json()
                update = Update.de_json(data=data, bot=app_bot.bot)
                await app_bot.process_update(update)
                return web.Response(text="ok")
            except Exception as exc:
                print("WEBHOOK ERROR:", repr(exc))
                return web.Response(status=500, text="error")

        health_app.router.add_post(webhook_path, telegram_webhook)
        print(f"Telegram webhook endpoint mounted at {webhook_path}")

    runner = web.AppRunner(health_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Web server running on :{PORT}")
    return runner


async def run_webhook(app: Application) -> tuple[web.AppRunner, str]:
    if not WEBHOOK_URL:
        raise RuntimeError("Missing WEBHOOK_URL. Example: https://your-service.onrender.com")

    webhook_path = _normalize_webhook_path(WEBHOOK_PATH)
    full_webhook_url = WEBHOOK_URL.rstrip("/") + webhook_path

    health_runner = await start_web_server(app)
    await app.bot.set_webhook(
        url=full_webhook_url,
        allowed_updates=Update.ALL_TYPES,
        secret_token=WEBHOOK_SECRET_TOKEN or None,
        drop_pending_updates=WEBHOOK_DROP_PENDING_UPDATES,
    )
    print(f"BIKA Character Bot launched in webhook mode: {full_webhook_url}")
    return health_runner, full_webhook_url


async def run_polling(app: Application) -> web.AppRunner:
    health_runner = await start_web_server(None)
    await app.bot.delete_webhook(drop_pending_updates=False)
    await app.updater.start_polling(drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)
    print("BIKA Character Bot launched in polling mode")
    return health_runner


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Missing BOT_TOKEN in .env")
    if not MONGODB_URI:
        raise RuntimeError("Missing MONGODB_URI/MONGO_URI in .env")

    await init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).concurrent_updates(True).build()
    register_handlers(app)
    app.add_error_handler(error_handler)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    health_runner: web.AppRunner | None = None
    using_polling = RUN_MODE.lower() == "polling"

    await app.initialize()
    await register_commands(app)
    await app.start()

    try:
        if using_polling:
            health_runner = await run_polling(app)
        else:
            health_runner, _ = await run_webhook(app)

        await stop_event.wait()
    finally:
        print("Shutting down...")
        if using_polling and app.updater:
            await app.updater.stop()
        await app.stop()
        await app.shutdown()
        if health_runner is not None:
            await health_runner.cleanup()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
