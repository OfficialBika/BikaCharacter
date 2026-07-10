from __future__ import annotations

from io import BytesIO
from typing import Optional

import aiohttp
from pymongo import ReturnDocument
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from config import PROFILE_TABLE, PROFILE_TITLE
from database.mongodb import get_db
from utils.cooldown import should_ignore_update
from utils.db_helpers import ensure_user, get_photo_by_card_id
from utils.profile_renderer import render_profile_card
from utils.text import escape_html


PROFILE_COUNTER_ID = "profile_id"


RANKS = (
    (3000, "✨", "Legendary Monarch", 0, ""),
    (2001, "👑", "Grand Collector", 3000, "Legendary Monarch"),
    (1001, "💎", "Master Collector", 2001, "Grand Collector"),
    (501, "⚔️", "Elite Collector", 1001, "Master Collector"),
    (101, "🀄", "Card Hunter", 501, "Elite Collector"),
    (1, "🌱", "Novice Collector", 101, "Card Hunter"),
    (0, "🌑", "New Collector", 1, "Novice Collector"),
)


def collector_rank(unique_cards: int) -> dict:
    count = max(0, int(unique_cards or 0))
    for minimum, emoji, name, next_target, next_name in RANKS:
        if count >= minimum:
            return {
                "emoji": emoji,
                "name": name,
                "nextTarget": int(next_target),
                "nextName": next_name,
            }
    return {"emoji": "🌑", "name": "New Collector", "nextTarget": 1, "nextName": "Novice Collector"}


async def ensure_profile_id(user_id: int) -> int:
    db = get_db()
    existing = await db.users.find_one({"userId": int(user_id)}, {"profileId": 1})
    current = int((existing or {}).get("profileId", 0) or 0)
    if current > 0:
        return current

    counter = await db.counters.find_one_and_update(
        {"_id": PROFILE_COUNTER_ID},
        {
            "$inc": {"seq": 1},
            "$set": {"updatedAt": __import__("utils.text", fromlist=["utcnow"]).utcnow()},
            "$setOnInsert": {"createdAt": __import__("utils.text", fromlist=["utcnow"]).utcnow()},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    candidate = int((counter or {}).get("seq", 1) or 1)

    updated = await db.users.find_one_and_update(
        {
            "userId": int(user_id),
            "$or": [
                {"profileId": {"$exists": False}},
                {"profileId": None},
                {"profileId": 0},
            ],
        },
        {"$set": {"profileId": candidate}},
        return_document=ReturnDocument.AFTER,
    )
    if updated and int(updated.get("profileId", 0) or 0) > 0:
        return int(updated["profileId"])

    latest = await db.users.find_one({"userId": int(user_id)}, {"profileId": 1})
    return int((latest or {}).get("profileId", candidate) or candidate)


async def get_global_unique_rank(unique_cards: int) -> int:
    """Competition rank by unique-card count; duplicates do not count."""
    higher = await get_db().users.count_documents(
        {
            "$expr": {
                "$gt": [
                    {"$size": {"$ifNull": ["$cards", []]}},
                    int(unique_cards),
                ]
            }
        }
    )
    return int(higher) + 1


def _full_name(user_doc: dict) -> str:
    return (
        " ".join(
            [
                str(user_doc.get("firstName", "") or ""),
                str(user_doc.get("lastName", "") or ""),
            ]
        ).strip()
        or str(user_doc.get("username", "") or "")
        or f"User {user_doc.get('userId')}"
    )


async def _download_telegram_file_bytes(context: ContextTypes.DEFAULT_TYPE, file_id: str) -> bytes | None:
    if not file_id:
        return None
    try:
        tg_file = await context.bot.get_file(file_id)
        data = await tg_file.download_as_bytearray()
        return bytes(data)
    except Exception as exc:
        print("PROFILE AVATAR DOWNLOAD ERROR:", repr(exc), flush=True)
        return None


async def get_profile_avatar_bytes(
    context: ContextTypes.DEFAULT_TYPE,
    user_doc: dict,
    tg_user,
) -> bytes | None:
    """Favourite photo first; otherwise Telegram profile photo; otherwise renderer initials."""
    fav_id = str(user_doc.get("favoriteCardId", "") or "")
    if fav_id:
        fav = next(
            (c for c in user_doc.get("cards", []) if str(c.get("cardId")) == fav_id),
            None,
        )
        if fav:
            media_type = str(fav.get("mediaType") or "photo").lower()
            file_id = str(fav.get("fileId") or "")
            if media_type == "photo" and file_id:
                data = await _download_telegram_file_bytes(context, file_id)
                if data:
                    return data

            # Old user snapshot may not contain fresh media metadata.
            doc = await get_photo_by_card_id(fav_id)
            if doc and str(doc.get("mediaType") or "photo").lower() == "photo":
                data = await _download_telegram_file_bytes(context, str(doc.get("fileId") or ""))
                if data:
                    return data

    try:
        photos = await context.bot.get_user_profile_photos(int(tg_user.id), limit=1)
        if photos.total_count > 0 and photos.photos:
            largest = photos.photos[0][-1]
            return await _download_telegram_file_bytes(context, largest.file_id)
    except Exception as exc:
        print("PROFILE TG PHOTO ERROR:", repr(exc), flush=True)

    return None


def build_profile_caption(
    *,
    profile_id: int,
    unique_cards: int,
    global_rank: int,
    rank: dict,
) -> str:
    return (
        f"🎗 <b>{escape_html(PROFILE_TITLE)}</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"🪪 <b>Profile ID</b> - <code>{profile_id}</code>\n"
        f"🎴 <b>Total Cards</b> - <code>{unique_cards}</code> unique\n"
        f"🌍 <b>Global</b> - <code>#{global_rank}</code>\n"
        f"🏆 <b>Rank</b> - {rank['emoji']} <b>{escape_html(rank['name'])}</b>"
    )


def build_profile_rich_html(
    *,
    profile_id: int,
    unique_cards: int,
    global_rank: int,
    rank: dict,
) -> str:
    return (
        f"<h2>🎗 {escape_html(PROFILE_TITLE)}</h2>"
        "<table bordered striped>"
        "<tr><th align=\"left\">Field</th><th align=\"left\">Value</th></tr>"
        f"<tr><td>🪪 Profile ID</td><td>{profile_id}</td></tr>"
        f"<tr><td>🎴 Total Cards</td><td>{unique_cards} unique</td></tr>"
        f"<tr><td>🌍 Global</td><td>#{global_rank}</td></tr>"
        f"<tr><td>🏆 Rank</td><td>{rank['emoji']} {escape_html(rank['name'])}</td></tr>"
        "</table>"
        "<h3>Collector Rank System</h3>"
        "<table bordered striped>"
        "<tr><th align=\"left\">Card Count</th><th align=\"left\">Rank Name</th></tr>"
        "<tr><td>1 - 100</td><td>🌱 Novice Collector</td></tr>"
        "<tr><td>101 - 500</td><td>🀄 Card Hunter</td></tr>"
        "<tr><td>501 - 1,000</td><td>⚔️ Elite Collector</td></tr>"
        "<tr><td>1,001 - 2,000</td><td>💎 Master Collector</td></tr>"
        "<tr><td>2,001 - 3,000</td><td>👑 Grand Collector</td></tr>"
        "<tr><td>3,000+</td><td>✨ Legendary Monarch</td></tr>"
        "</table>"
    )


async def send_profile_rich_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    rich_html: str,
) -> bool:
    payload = {
        "chat_id": int(update.effective_chat.id),
        "rich_message": {
            "html": rich_html,
            "skip_entity_detection": True,
        },
    }
    if update.effective_message and getattr(update.effective_message, "message_thread_id", None):
        payload["message_thread_id"] = int(update.effective_message.message_thread_id)

    url = f"https://api.telegram.org/bot{context.bot.token}/sendRichMessage"
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as response:
                data = await response.json(content_type=None)
                if response.status != 200 or not data.get("ok"):
                    raise RuntimeError(
                        f"sendRichMessage HTTP={response.status}: {data.get('description')}"
                    )
        return True
    except Exception as exc:
        print("PROFILE RICH MESSAGE ERROR:", repr(exc), flush=True)
        return False


async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await should_ignore_update(update):
        return

    user_doc = await ensure_user(update.effective_user)
    if not user_doc:
        return

    unique_cards = len(list(user_doc.get("cards", [])))
    profile_id = await ensure_profile_id(int(update.effective_user.id))
    global_rank = await get_global_unique_rank(unique_cards)
    rank = collector_rank(unique_cards)
    avatar_bytes = await get_profile_avatar_bytes(context, user_doc, update.effective_user)
    full_name = _full_name(user_doc)

    image = render_profile_card(
        full_name=full_name,
        profile_id=profile_id,
        unique_cards=unique_cards,
        global_rank=global_rank,
        collector_rank=rank["name"],
        collector_emoji=rank["emoji"],
        avatar_bytes=avatar_bytes,
        next_rank_name=rank["nextName"],
        next_rank_target=rank["nextTarget"],
    )

    caption = build_profile_caption(
        profile_id=profile_id,
        unique_cards=unique_cards,
        global_rank=global_rank,
        rank=rank,
    )

    if PROFILE_TABLE:
        # Message 1: generated profile image only.
        await update.effective_message.reply_photo(photo=image)

        # Message 2: native Rich Message profile table.
        rich_ok = await send_profile_rich_message(
            update,
            context,
            build_profile_rich_html(
                profile_id=profile_id,
                unique_cards=unique_cards,
                global_rank=global_rank,
                rank=rank,
            ),
        )
        if not rich_ok:
            await update.effective_message.reply_html(caption)
        return

    # PROFILE_TABLE=false: generated image + normal HTML caption.
    await update.effective_message.reply_photo(photo=image, caption=caption, parse_mode="HTML")


def register_profile_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("profile", profile_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^\.profile$"), profile_cmd))
