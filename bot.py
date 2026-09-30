from __future__ import annotations

import asyncio
import signal
import sys
import traceback

from aiohttp import web
from telegram import BotCommand, Update
from telegram.ext import Application, ApplicationBuilder, ContextTypes

from config import (
    BOT_TOKEN,
    MONGODB_URI,
    PORT,
    RUN_MODE,
    CLAIM_COMMAND,
    ENABLE_HEALTH_SERVER,
    BOT_ALLOWED_UPDATES,
    RESET_GROUP_MESSAGE_COUNT_ON_STARTUP,
    CLEAR_ACTIVE_DROP_ON_STARTUP,
    WEBHOOK_DROP_PENDING_UPDATES,
    WEBHOOK_PATH,
    WEBHOOK_SECRET_TOKEN,
    WEBHOOK_URL,
)
from database.mongodb import close_db, get_db, init_db
from database import sqlite_hot
from handlers import register_handlers
from handlers.photo_add import _ensure_card_counter
from web.app import create_health_app
from utils.text import utcnow
from utils.anime_catalog import canonicalize_anime_catalog, ensure_anime_catalog_seeded


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
            BotCommand("start", "Start the bot"),
            BotCommand("bharem", "Display your harem"),
            BotCommand("search", "Search characters"),
            BotCommand("bprofile", "See your profile"),
            BotCommand("fav", "Set or show favourite character"),
            BotCommand("check", "Check character by ID"),
            BotCommand(CLAIM_COMMAND, "Claim spawned character"),
            BotCommand("hmode", "Change harem view"),
            BotCommand("topgroup", "Top groups by catches"),
            BotCommand("gtop", "Global top harem users"),
            BotCommand("todaygtop", "Today top catchers"),
            BotCommand("mtop", "Monthly top 10 collectors"),
            BotCommand("wtop", "Weekly top 10 collectors"),
            BotCommand("mylimit", "Check daily catch limit"),
            BotCommand("gift", "Gift to other user")
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
                if update is None:
                    return web.Response(status=400, text="invalid update")
                await app_bot.update_queue.put(update)
                return web.Response(text="ok")
            except Exception as exc:
                print("WEBHOOK ERROR:", repr(exc), flush=True)
                return web.Response(status=400, text="bad request")

        health_app.router.add_post(webhook_path, telegram_webhook)

    runner = web.AppRunner(health_app)
    await runner.setup()

    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    print(f"Web server running on :{PORT}", flush=True)
    return runner


async def run_webhook(app: Application) -> tuple[web.AppRunner, str]:
    runner = await start_web_server(app)
    webhook_url = f"{WEBHOOK_URL.rstrip('/')}{_normalize_webhook_path(WEBHOOK_PATH)}"

    await app.bot.delete_webhook(drop_pending_updates=WEBHOOK_DROP_PENDING_UPDATES)
    await app.bot.set_webhook(
        url=webhook_url,
        secret_token=WEBHOOK_SECRET_TOKEN or None,
        allowed_updates=allowed_updates_for_bot(),
    )

    print(f"Telegram webhook endpoint mounted at {WEBHOOK_PATH}", flush=True)
    print(f"BIKA Character Bot launched in webhook mode: {webhook_url}", flush=True)
    return runner, webhook_url


async def run_polling(app: Application) -> web.AppRunner:
    health_runner = await start_web_server(app)

    await app.updater.start_polling(
        allowed_updates=allowed_updates_for_bot(),
        drop_pending_updates=True,
    )
    print("BIKA Character Bot launched in polling mode", flush=True)
    return health_runner


def allowed_updates_for_bot() -> list[str]:
    configured = [str(item).strip() for item in (BOT_ALLOWED_UPDATES or []) if str(item).strip()]
    if not configured:
        configured = ["message", "inline_query", "my_chat_member"]
    if "callback_query" not in configured:
        configured.append("callback_query")
    return configured


async def reset_group_state_on_startup() -> None:
    """Reset group counters on every process start.

    This is intentionally done after MongoDB init and before polling/webhook starts.
    It prevents the bot from using old messageCount values left in MongoDB before a
    VPS/PM2 restart, so changeTime counting always starts from 0 after startup.
    """
    set_data = {}
    if RESET_GROUP_MESSAGE_COUNT_ON_STARTUP:
        set_data["messageCount"] = 0
    if CLEAR_ACTIVE_DROP_ON_STARTUP:
        set_data["activeDrop"] = None

    if not set_data:
        print("STARTUP GROUP RESET: disabled", flush=True)
        return

    set_data["updatedAt"] = utcnow()
    result = await get_db().groups.update_many({}, {"$set": set_data})
    print(
        "STARTUP GROUP RESET: "
        f"matched={getattr(result, 'matched_count', 0)} "
        f"modified={getattr(result, 'modified_count', 0)} "
        f"messageCountReset={RESET_GROUP_MESSAGE_COUNT_ON_STARTUP} "
        f"activeDropCleared={CLEAR_ACTIVE_DROP_ON_STARTUP}",
        flush=True,
    )


async def main() -> None:
    print("Starting BIKA Character Bot...", flush=True)
    print(f"RUN_MODE={RUN_MODE} PORT={PORT} WEBHOOK_URL_SET={bool(WEBHOOK_URL)} WEBHOOK_PATH={WEBHOOK_PATH} HEALTH={ENABLE_HEALTH_SERVER}", flush=True)
    print(f"MONGODB_URI_SET={bool(MONGODB_URI)}", flush=True)

    if not BOT_TOKEN:
        raise RuntimeError("Missing BOT_TOKEN in Render Environment Variables")
    if not MONGODB_URI:
        raise RuntimeError("Missing MONGODB_URI/MONGO_URI in Render Environment Variables")
    if RUN_MODE.lower() != "polling" and not WEBHOOK_URL:
        raise RuntimeError("Missing WEBHOOK_URL in Render Environment Variables. Example: https://your-service.onrender.com")

    await init_db()

    # Prepare the numeric /add counter before Telegram starts receiving updates.
    # This keeps the first normal /add fast and avoids doing the initial full-ID
    # compatibility scan inside a live media handler.
    print("ADD COUNTER INIT START", flush=True)
    await _ensure_card_counter()
    print("ADD COUNTER INIT READY", flush=True)

    # Normalize legacy duplicate Anime aliases once before the bot starts serving /add.
    await ensure_anime_catalog_seeded()
    await canonicalize_anime_catalog()
    await sqlite_hot.init()
    sqlite_hot.start_flush_loop(get_db)
    await reset_group_state_on_startup()
    # Startup reset is authoritative in Mongo; never reuse stale local counters.
    await sqlite_hot.clear_groups()

    using_polling = RUN_MODE.lower() == "polling"
    app_builder = ApplicationBuilder().token(BOT_TOKEN).concurrent_updates(True)
    # Custom aiohttp webhook feeds Application.update_queue directly, matching
    # PTB's recommended custom-webhook architecture. Polling retains its updater.
    if not using_polling:
        app_builder.updater(None)
    app = app_builder.build()
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
        # Stop accepting new webhook requests before draining PTB's update queue.
        if not using_polling and health_runner is not None:
            await health_runner.cleanup()

        if using_polling and app.updater:
            await app.updater.stop()

        # PTB waits for pending update_queue work and tasks created through
        # Application.create_task() before returning from stop().
        await app.stop()
        await app.shutdown()

        if using_polling and health_runner is not None:
            await health_runner.cleanup()

        await sqlite_hot.close()
        await close_db()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        print("FATAL STARTUP ERROR:", flush=True)
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        raise
