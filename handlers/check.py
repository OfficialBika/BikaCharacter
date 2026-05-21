from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from utils.cooldown import should_ignore_update
from utils.db_helpers import get_photo_by_card_id, global_card_stats
from utils.rarity import get_rarity_emoji
from utils.text import escape_html, mention_user_doc


async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await should_ignore_update(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /check <card id>")
        return
    card_id = str(context.args[0]).strip()
    photo = await get_photo_by_card_id(card_id)
    if not photo:
        await update.message.reply_text(f"❌ Character ID {card_id} not found.")
        return

    stats = await global_card_stats(card_id)
    lines = [
        "<b>OwO! Check out this character!</b>",
        "",
        f"<b>{escape_html(photo.get('anime'))}</b>",
        f"<b>{escape_html(photo.get('cardId'))}:</b> {escape_html(photo.get('name'))}",
        f"({get_rarity_emoji(photo.get('rarity'))} <b>RARITY:</b> {escape_html(photo.get('rarity'))})",
        "",
        f"🌍 <b>CAUGHT GLOBALLY:</b> {stats['totalOwned']} TIMES",
        "",
        "🏅 <b>TOP 10 CATCHERS OF THIS CHARACTER!</b>",
    ]
    if not stats["topCatchers"]:
        lines.append("↪ No catch data yet")
    else:
        for catcher in stats["topCatchers"]:
            lines.append(f"↪ {mention_user_doc(catcher)} x{catcher.get('count', 0)}")

    await update.message.reply_photo(photo["fileId"], caption="\n".join(lines), parse_mode="HTML")


def register_check_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("check", check_cmd))
