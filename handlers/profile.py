from __future__ import annotations

import random

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from config import RARITY_ORDER
from database.mongodb import get_db
from utils.cooldown import should_ignore_update
from utils.db_helpers import ensure_user, rarity_counts
from utils.rarity import get_rarity_emoji
from utils.text import level_from_exp, progress_bar
from utils.i18n import t


def build_profile_text(user_doc: dict, total_photo_count: int) -> str:
    cards = list(user_doc.get("cards", []))
    total_owned = sum(int(c.get("count", 0)) for c in cards)
    unique_owned = len(cards)
    harem_percent = (unique_owned / total_photo_count * 100) if total_photo_count else 0
    level = level_from_exp(user_doc.get("exp", 0))
    counts = rarity_counts(cards)
    username = " ".join([user_doc.get("firstName", ""), user_doc.get("lastName", "")]).strip() or user_doc.get("username") or "Unknown"
    fav = next((c for c in cards if str(c.get("cardId")) == str(user_doc.get("favoriteCardId", ""))), None)

    lines = [
        t("profile_header"),
        "",
        t("profile_user", username=username),
        t("profile_user_id", user_id=user_doc.get("userId")),
        t("profile_total_character", total_owned=total_owned, unique_owned=unique_owned),
        t("profile_harem", unique_owned=unique_owned, total_photo_count=total_photo_count, percent=harem_percent),
        t("profile_level", level=level["level"]),
        t("profile_progress", bar=progress_bar(level["percent"])),
        t("profile_favourite", name=fav["name"], card_id=fav["cardId"]) if fav else t("profile_favourite_not_set"),
        "",
    ]
    for rarity in RARITY_ORDER:
        data = counts.get(rarity, {"unique": 0, "total": 0})
        lines.append(t("profile_rarity_line", emoji=get_rarity_emoji(rarity), rarity=rarity, unique=data["unique"], total=data["total"]))
    return "\n".join(lines)


async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await should_ignore_update(update):
        return
    user = await ensure_user(update.effective_user)
    total = await get_db().photos.count_documents({})
    text = build_profile_text(user, total)
    cards = list(user.get("cards", []))
    cover = None
    if user.get("favoriteCardId"):
        cover = next((c for c in cards if str(c.get("cardId")) == str(user.get("favoriteCardId"))), None)
    if not cover and cards:
        cover = random.choice(cards)
    if cover and cover.get("fileId"):
        await update.message.reply_photo(cover["fileId"], caption=text)
    else:
        await update.message.reply_text(text)


def register_profile_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("profile", profile_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^\.profile$"), profile_cmd))
