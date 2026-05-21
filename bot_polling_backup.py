from __future__ import annotations

import asyncio
import signal

from aiohttp import web
from telegram import BotCommand, Update
from telegram.ext import Application, ApplicationBuilder, ContextTypes

from config import BOT_TOKEN, MONGODB_URI, PORT
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


async def start_health_server() -> web.AppRunner:
    health_app = create_health_app()
    runner = web.AppRunner(health_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Health server running on :{PORT}")
    return runner


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Missing BOT_TOKEN in .env")
    if not MONGODB_URI:
        raise RuntimeError("Missing MONGODB_URI in .env")

    await init_db()
    health_runner = await start_health_server()

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

    await app.initialize()
    await register_commands(app)
    await app.start()
    await app.updater.start_polling(drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)
    print("BIKA Character Bot launched in polling mode")

    try:
        await stop_event.wait()
    finally:
        print("Shutting down...")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await health_runner.cleanup()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
