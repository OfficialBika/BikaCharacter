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


def detect_card_media_type(card: dict) -> str:
    media_type = str(card.get("mediaType") or "").strip().lower()
    if media_type in {"photo", "video", "animation", "document"}:
        return media_type
    if media_type == "gif":
        return "animation"

    mime_type = str(card.get("mimeType") or "").strip().lower()
    file_name = str(card.get("fileName") or "").strip().lower()
    if mime_type.startswith("video/") or file_name.endswith((".mp4", ".mov", ".mkv", ".webm")):
        return "video"
    if mime_type == "image/gif" or file_name.endswith(".gif"):
        return "animation"
    if mime_type and not mime_type.startswith("image/"):
        return "document"
    return "photo"


async def hydrate_card_media(card: dict | None) -> dict | None:
    """Merge media metadata from photos collection for old user.cards snapshots."""
    if not card:
        return None

    merged = dict(card)
    card_id = str(merged.get("cardId", ""))
    needs_lookup = not merged.get("mediaType") or detect_card_media_type(merged) == "photo"

    if card_id and needs_lookup:
        photo_doc = await get_db().photos.find_one(
            {"cardId": card_id},
            {"fileId": 1, "fileUniqueId": 1, "mediaType": 1, "mimeType": 1, "fileName": 1},
        )
        if photo_doc:
            for key in ("fileId", "fileUniqueId", "mediaType", "mimeType", "fileName"):
                value = photo_doc.get(key)
                if value:
                    merged[key] = value

    return merged


async def reply_profile_media(message, cover: dict, text: str) -> None:
    media_type = detect_card_media_type(cover)
    file_id = str(cover.get("fileId") or "")
    if not file_id:
        await message.reply_text(text)
        return

    try:
        if media_type == "video":
            await message.reply_video(file_id, caption=text)
        elif media_type == "animation":
            await message.reply_animation(file_id, caption=text)
        elif media_type == "document":
            await message.reply_document(file_id, caption=text)
        else:
            await message.reply_photo(file_id, caption=text)
    except Exception as exc:
        print("PROFILE SEND MEDIA ERROR:", repr(exc))
        await message.reply_text(text)


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
    cover = await hydrate_card_media(cover)
    if cover and cover.get("fileId"):
        await reply_profile_media(update.message, cover, text)
    else:
        await update.message.reply_text(text)


def register_profile_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("profile", profile_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^\.profile$"), profile_cmd))
