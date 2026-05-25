from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from utils.cooldown import should_ignore_update
from utils.db_helpers import get_photo_by_card_id, global_card_stats
from utils.rarity import get_rarity_emoji
from utils.text import escape_html, mention_user_doc
from utils.i18n import t


async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await should_ignore_update(update):
        return
    if not context.args:
        await update.message.reply_text(t("check_usage"))
        return
    card_id = str(context.args[0]).strip()
    photo = await get_photo_by_card_id(card_id)
    if not photo:
        await update.message.reply_text(t("check_not_found", card_id=card_id))
        return

    stats = await global_card_stats(card_id)
    lines = [
        t("card_check_header"),
        "",
        f"<b>{escape_html(photo.get('anime'))}</b>",
        f"<b>{escape_html(photo.get('cardId'))}:</b> {escape_html(photo.get('name'))}",
        t("rarity_line", emoji=get_rarity_emoji(photo.get("rarity")), rarity=escape_html(photo.get("rarity"))),
        "",
        t("caught_globally", total=stats["totalOwned"]),
        "",
        t("top10_catchers"),
    ]
    if not stats["topCatchers"]:
        lines.append(t("no_catch_data"))
    else:
        for catcher in stats["topCatchers"]:
            lines.append(f"↪ {mention_user_doc(catcher)} x{catcher.get('count', 0)}")

    await update.message.reply_photo(photo["fileId"], caption="\n".join(lines), parse_mode="HTML")


def register_check_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("check", check_cmd))
