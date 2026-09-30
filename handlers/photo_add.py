from __future__ import annotations

import asyncio
import math
import secrets
from datetime import timedelta

import config
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from telegram import InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database.mongodb import get_db
from utils.anime_catalog import add_anime, ensure_anime_catalog_seeded, list_animes
from utils.buttons import action_button, make_button
from utils.parser import parse_add_caption, parse_normal_add_caption
from utils.permissions import is_owner
from utils.text import escape_html, mention_user, utcnow

CARD_DATABASE_CHANNEL_ID = config.CARD_DATABASE_CHANNEL_ID
ADDING_LOG_CHANNEL_ID = getattr(
    config,
    "ADDING_LOG_CHANNEL_ID",
    getattr(config, "GROUP_LOG_CHANNEL_ID", ""),
)
RARITY_ORDER = config.RARITY_ORDER
LIMITED_CARDS_COLLECTION = getattr(config, "LIMITED_CARDS_COLLECTION", "limited_cards")
LIMITED_RARITY_NAME = getattr(config, "LIMITED_RARITY_NAME", "Limited")

ADDER_GROUP_IDS = getattr(
    config,
    "ADDER_GROUP_IDS",
    [-1003983636133],
)
SETTINGS_ID = "config"
CARD_COUNTER_ID = "photo_card_id"
ADD_SESSION_COLLECTION = "add_sessions"
ADD_OPERATION_COLLECTION = "add_operations"
ADD_SESSION_TIMEOUT_SECONDS = int(getattr(config, "ADD_SESSION_TIMEOUT_SECONDS", 180))
ADD_ANIME_PAGE_SIZE = int(getattr(config, "ADD_ANIME_PAGE_SIZE", 10))

SUPPORTED_DOCUMENT_MIME_PREFIXES = ("image/", "video/")

_ADD_SESSION_TASKS: dict[str, asyncio.Task] = {}
_CARD_COUNTER_READY = False
_CARD_COUNTER_LOCK = asyncio.Lock()


def is_forwarded_message(msg) -> bool:
    """Detect forwarded/copy-forwarded messages and reject them for /add."""
    return any(
        getattr(msg, attr, None)
        for attr in (
            "forward_origin",
            "forward_date",
            "forward_from",
            "forward_from_chat",
            "forward_sender_name",
        )
    )


def is_limited_card(parsed: dict, card_id_provided: bool) -> bool:
    """Limited cards keep the existing owner-only rules."""
    rarity = str(parsed.get("rarity", "")).strip()
    return rarity.casefold() == str(LIMITED_RARITY_NAME).casefold()


def is_allowed_add_chat(update: Update) -> bool:
    chat = update.effective_chat
    if not chat:
        return False
    return int(chat.id) in {int(x) for x in ADDER_GROUP_IDS}


async def is_allowed_adder(user) -> bool:
    if is_owner(user):
        return True

    user_id = getattr(user, "id", 0)
    if not user_id:
        return False

    settings = await get_db().bot_settings.find_one(
        {"_id": SETTINGS_ID},
        {"adderIds": 1},
    )
    return int(user_id) in {int(x) for x in (settings or {}).get("adderIds", [])}


async def _max_numeric_card_id() -> int:
    """Scan numeric IDs only once per process, during counter initialization."""
    db = get_db()
    max_id = 0
    for collection_name in ("photos", LIMITED_CARDS_COLLECTION):
        docs = await db[collection_name].aggregate(
            [
                {"$match": {"cardId": {"$regex": r"^[0-9]+$"}}},
                {"$project": {"cardIdNum": {"$toInt": "$cardId"}}},
                {"$sort": {"cardIdNum": -1}},
                {"$limit": 1},
            ]
        ).to_list(1)
        if docs:
            max_id = max(max_id, int(docs[0].get("cardIdNum", 0) or 0))
    return max_id


async def _ensure_card_counter() -> None:
    """Initialize the atomic numeric ID counter once per process."""
    global _CARD_COUNTER_READY

    if _CARD_COUNTER_READY:
        return

    async with _CARD_COUNTER_LOCK:
        if _CARD_COUNTER_READY:
            return

        db = get_db()
        max_id = await _max_numeric_card_id()
        now = utcnow()
        # Do not combine $max and $setOnInsert on the same "seq" path.
        # MongoDB rejects that combination as a path conflict (code 40).
        # $max also initializes the field on an upsert when it is missing.
        await db.counters.update_one(
            {"_id": CARD_COUNTER_ID},
            {
                "$max": {"seq": int(max_id)},
                "$set": {"updatedAt": now},
            },
            upsert=True,
        )
        _CARD_COUNTER_READY = True


async def _sync_card_counter_at_least(card_id: str) -> None:
    if not str(card_id).isdigit():
        return

    await _ensure_card_counter()
    await get_db().counters.update_one(
        {"_id": CARD_COUNTER_ID, "seq": {"$lt": int(card_id)}},
        {"$set": {"seq": int(card_id), "updatedAt": utcnow()}},
    )


async def _card_id_exists(card_id: str) -> bool:
    db = get_db()
    normal, limited = await asyncio.gather(
        db.photos.find_one({"cardId": str(card_id)}, {"_id": 1}),
        db[LIMITED_CARDS_COLLECTION].find_one({"cardId": str(card_id)}, {"_id": 1}),
    )
    return bool(normal or limited)


async def _next_auto_card_id() -> str:
    """Allocate a numeric ID atomically without scanning all cards per add."""
    await _ensure_card_counter()
    db = get_db()

    while True:
        counter = await db.counters.find_one_and_update(
            {"_id": CARD_COUNTER_ID},
            {
                "$inc": {"seq": 1},
                "$set": {"updatedAt": utcnow()},
            },
            projection={"_id": 0, "seq": 1},
            return_document=ReturnDocument.AFTER,
        )
        if not counter:
            continue

        candidate = str(int(counter.get("seq", 0) or 0))
        if int(candidate) <= 0:
            continue

        if not await _card_id_exists(candidate):
            return candidate


def _database_caption(action: str, parsed: dict, adder) -> str:
    icon = "✅" if action == "Saved" else "♻️"
    return (
        f"{icon} <b>{escape_html(action)}</b>\n\n"
        f"👤 <b>Name:</b> {escape_html(parsed['name'])}\n"
        f"🆔 <b>ID:</b> {escape_html(parsed['cardId'])}\n"
        f"🏷 <b>Rarity:</b> {escape_html(parsed['rarity'])}\n"
        f"🌴 <b>Anime:</b> {escape_html(parsed['anime'])}\n\n"
        f"➕ <b>Added By:</b> {mention_user(adder)}\n"
        f"🆔 <b>Adder ID:</b> {adder.id}"
    )


def _extract_message_media(msg) -> dict | None:
    """Return Telegram media info for supported card media.

    Telegram can deliver visually identical files either as native media
    (photo/video/animation) or as a Document, and some clients omit/mislabel
    the document MIME type. Keep /add tolerant of those harmless transport
    differences instead of silently dropping the message.
    """
    if msg.photo:
        media = msg.photo[-1]
        return {
            "mediaType": "photo",
            "fileId": media.file_id,
            "fileUniqueId": media.file_unique_id,
            "mimeType": "",
            "fileName": "",
        }

    if msg.video:
        media = msg.video
        return {
            "mediaType": "video",
            "fileId": media.file_id,
            "fileUniqueId": media.file_unique_id,
            "mimeType": media.mime_type or "video/mp4",
            "fileName": media.file_name or "",
        }

    if msg.animation:
        media = msg.animation
        return {
            "mediaType": "animation",
            "fileId": media.file_id,
            "fileUniqueId": media.file_unique_id,
            "mimeType": media.mime_type or "image/gif",
            "fileName": media.file_name or "",
        }

    if msg.document:
        media = msg.document
        mime_type = (media.mime_type or "").strip().lower()
        file_name = (media.file_name or "").strip().lower()

        visual_extensions = (
            ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".avif",
            ".gif", ".mp4", ".m4v", ".mov", ".webm", ".mkv",
        )
        is_visual = (
            mime_type.startswith(SUPPORTED_DOCUMENT_MIME_PREFIXES)
            or file_name.endswith(visual_extensions)
        )
        if not is_visual:
            return None

        # Keep files uploaded as Telegram Documents as documents. Their
        # document file_id is guaranteed to be reusable with send_document().
        return {
            "mediaType": "document",
            "fileId": media.file_id,
            "fileUniqueId": media.file_unique_id,
            "mimeType": mime_type,
            "fileName": media.file_name or "",
        }

    return None


async def _post_to_card_database_channel(
    context: ContextTypes.DEFAULT_TYPE,
    file_id: str,
    caption: str,
    media_type: str,
) -> dict:
    if not CARD_DATABASE_CHANNEL_ID:
        raise RuntimeError("CARD_DATABASE_CHANNEL_ID is missing in .env")

    if media_type == "video":
        sent = await context.bot.send_video(
            chat_id=CARD_DATABASE_CHANNEL_ID,
            video=file_id,
            caption=caption,
            parse_mode="HTML",
        )
        media = sent.video
        stored_file_id = media.file_id if media else file_id
        file_unique_id = media.file_unique_id if media else ""

    elif media_type == "animation":
        sent = await context.bot.send_animation(
            chat_id=CARD_DATABASE_CHANNEL_ID,
            animation=file_id,
            caption=caption,
            parse_mode="HTML",
        )
        media = sent.animation
        stored_file_id = media.file_id if media else file_id
        file_unique_id = media.file_unique_id if media else ""

    elif media_type == "document":
        sent = await context.bot.send_document(
            chat_id=CARD_DATABASE_CHANNEL_ID,
            document=file_id,
            caption=caption,
            parse_mode="HTML",
        )
        media = sent.document
        stored_file_id = media.file_id if media else file_id
        file_unique_id = media.file_unique_id if media else ""

    else:
        sent = await context.bot.send_photo(
            chat_id=CARD_DATABASE_CHANNEL_ID,
            photo=file_id,
            caption=caption,
            parse_mode="HTML",
        )
        media = sent.photo[-1] if sent.photo else None
        stored_file_id = media.file_id if media else file_id
        file_unique_id = media.file_unique_id if media else ""
        media_type = "photo"

    return {
        "storageChatId": sent.chat_id,
        "storageMessageId": sent.message_id,
        "fileId": stored_file_id,
        "fileUniqueId": file_unique_id,
        "mediaType": media_type,
    }


async def _edit_database_caption(
    context: ContextTypes.DEFAULT_TYPE,
    storage: dict,
    caption: str,
) -> bool:
    chat_id = storage.get("storageChatId")
    message_id = storage.get("storageMessageId")
    if not chat_id or not message_id:
        return False

    try:
        await context.bot.edit_message_caption(
            chat_id=chat_id,
            message_id=int(message_id),
            caption=caption,
            parse_mode="HTML",
        )
        return True
    except Exception as exc:
        print("EDIT CARD DATABASE CAPTION FAILED:", repr(exc), flush=True)
        return False


async def _delete_database_message(
    context: ContextTypes.DEFAULT_TYPE,
    storage: dict | None,
) -> bool:
    if not storage:
        return True

    chat_id = storage.get("storageChatId")
    message_id = storage.get("storageMessageId")
    if not chat_id or not message_id:
        return True

    try:
        await context.bot.delete_message(
            chat_id=chat_id,
            message_id=int(message_id),
        )
        return True
    except Exception as exc:
        print("DELETE OLD CARD DATABASE MESSAGE FAILED:", repr(exc), flush=True)
        return False


def _cancel_add_session_task(token: str) -> None:
    task = _ADD_SESSION_TASKS.pop(str(token), None)
    if task and not task.done():
        task.cancel()


async def _expire_add_session(token: str, bot) -> None:
    try:
        await asyncio.sleep(ADD_SESSION_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        return

    try:
        now = utcnow()
        session = await get_db()[ADD_SESSION_COLLECTION].find_one_and_update(
            {
                "_id": str(token),
                "status": "active",
                "expiresAt": {"$lte": now},
            },
            {
                "$set": {
                    "status": "expired",
                    "closedAt": now,
                    "updatedAt": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if not session:
            return

        try:
            await bot.edit_message_text(
                chat_id=int(session["chatId"]),
                message_id=int(session["promptMessageId"]),
                text=(
                    "⏰ <b>Add session closed.</b>\n\n"
                    "No Add information was received for 3 minutes.\n"
                    "Send /add to start again."
                ),
                parse_mode="HTML",
            )
        except Exception as exc:
            print("ADD SESSION EXPIRY MESSAGE EDIT FAILED:", repr(exc), flush=True)
    finally:
        _ADD_SESSION_TASKS.pop(str(token), None)


def _schedule_add_session_expiry(token: str, bot) -> None:
    _cancel_add_session_task(token)
    _ADD_SESSION_TASKS[str(token)] = asyncio.create_task(
        _expire_add_session(str(token), bot)
    )


async def _touch_add_session(
    token: str,
    bot,
    *,
    anime: str | None = None,
) -> dict | None:
    now = utcnow()
    expires_at = now + timedelta(seconds=ADD_SESSION_TIMEOUT_SECONDS)
    update_data = {
        "expiresAt": expires_at,
        "updatedAt": now,
    }
    if anime is not None:
        update_data["selectedAnime"] = str(anime)

    session = await get_db()[ADD_SESSION_COLLECTION].find_one_and_update(
        {
            "_id": str(token),
            "status": "active",
            "expiresAt": {"$gt": now},
        },
        {"$set": update_data},
        return_document=ReturnDocument.AFTER,
    )
    if session:
        _schedule_add_session_expiry(str(token), bot)
    return session


async def _get_active_add_session(user_id: int, chat_id: int) -> dict | None:
    now = utcnow()
    return await get_db()[ADD_SESSION_COLLECTION].find_one(
        {
            "userId": int(user_id),
            "chatId": int(chat_id),
            "status": "active",
            "expiresAt": {"$gt": now},
        },
        sort=[("createdAt", -1)],
    )


def _add_anime_keyboard(
    *,
    token: str,
    page: int,
    names: list[str],
    total: int,
) -> InlineKeyboardMarkup:
    rows: list[list] = []
    for index in range(0, len(names), 2):
        row = []
        for local_index, name in enumerate(names[index:index + 2]):
            absolute_index = (int(page) * ADD_ANIME_PAGE_SIZE) + index + local_index
            row.append(
                make_button(
                    name,
                    style="primary",
                    callback_data=f"addsel:{token}:{absolute_index}",
                    strip_existing_emoji=False,
                )
            )
        rows.append(row)

    total_pages = max(1, math.ceil(total / ADD_ANIME_PAGE_SIZE))
    if total_pages > 1:
        nav: list = []
        if page > 0:
            nav.append(
                action_button(
                    "◀️",
                    "primary",
                    callback_data=f"addpage:{token}:{page - 1}",
                )
            )
        nav.append(
            action_button(
                f"{page + 1}/{total_pages}",
                "primary",
                callback_data=f"addpage:{token}:{page}",
            )
        )
        if page < total_pages - 1:
            nav.append(
                action_button(
                    "▶️",
                    "primary",
                    callback_data=f"addpage:{token}:{page + 1}",
                )
            )
        rows.append(nav)

    rows.append(
        [
            action_button(
                "✖ Cancel",
                "danger",
                callback_data=f"addcancel:{token}",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


async def _render_add_anime_page(
    *,
    bot,
    chat_id: int,
    message_id: int,
    token: str,
    page: int,
) -> None:
    names, total = await list_animes(page, ADD_ANIME_PAGE_SIZE)
    if total <= 0:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=(
                "🎴 <b>Please choose Anime</b>\n\n"
                "No Anime found in the database yet.\n"
                "Use /addanime Anime Name first."
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[
                    action_button(
                        "✖ Cancel",
                        "danger",
                        callback_data=f"addcancel:{token}",
                    )
                ]]
            ),
        )
        return

    total_pages = max(1, math.ceil(total / ADD_ANIME_PAGE_SIZE))
    safe_page = max(0, min(int(page), total_pages - 1))
    if safe_page != int(page):
        names, total = await list_animes(safe_page, ADD_ANIME_PAGE_SIZE)

    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=(
            "🎴 <b>Please choose Anime</b>\n\n"
            "Select the Anime for the cards you are going to add."
        ),
        parse_mode="HTML",
        reply_markup=_add_anime_keyboard(
            token=token,
            page=safe_page,
            names=names,
            total=total,
        ),
    )


async def start_add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed_add_chat(update):
        return

    user = update.effective_user
    msg = update.effective_message
    chat = update.effective_chat
    if not user or not msg or not chat:
        return

    if not await is_allowed_adder(user):
        await msg.reply_text(
            "❌ You are not allowed to add/update cards. "
            "Ask the owner to use /addadder for your account."
        )
        return

    if context.args:
        await msg.reply_text(
            "❌ New /add format does not use command arguments.\n\n"
            "Use /add, choose Anime, then send media with:\n"
            "Yelan | Lg\n"
            "or\n"
            "240 | Yelan | Dv"
        )
        return

    await ensure_anime_catalog_seeded()

    db = get_db()
    now = utcnow()
    previous = await db[ADD_SESSION_COLLECTION].find_one_and_update(
        {
            "userId": int(user.id),
            "chatId": int(chat.id),
            "status": "active",
        },
        {
            "$set": {
                "status": "cancelled",
                "closedAt": now,
                "updatedAt": now,
            }
        },
        sort=[("createdAt", -1)],
        return_document=ReturnDocument.BEFORE,
    )
    if previous:
        _cancel_add_session_task(str(previous["_id"]))

    token = secrets.token_hex(8)
    expires_at = now + timedelta(seconds=ADD_SESSION_TIMEOUT_SECONDS)

    sent = await msg.reply_text(
        "🎴 <b>Please choose Anime</b>\n\n"
        "Select the Anime for the cards you are going to add.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [[
                action_button(
                    "Loading Anime…",
                    "primary",
                    callback_data=f"addpage:{token}:0",
                )
            ]]
        ),
    )

    await db[ADD_SESSION_COLLECTION].delete_many(
        {
            "userId": int(user.id),
            "chatId": int(chat.id),
            "status": "active",
        }
    )
    await db[ADD_SESSION_COLLECTION].insert_one(
        {
            "_id": token,
            "userId": int(user.id),
            "chatId": int(chat.id),
            "status": "active",
            "selectedAnime": "",
            "promptMessageId": int(sent.message_id),
            "createdAt": now,
            "updatedAt": now,
            "expiresAt": expires_at,
        }
    )

    await _render_add_anime_page(
        bot=context.bot,
        chat_id=int(chat.id),
        message_id=int(sent.message_id),
        token=token,
        page=0,
    )
    _schedule_add_session_expiry(token, context.bot)


async def addanime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed_add_chat(update):
        return

    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return

    if not await is_allowed_adder(user):
        await msg.reply_text(
            "❌ You are not allowed to add Anime. "
            "Ask the owner to use /addadder for your account."
        )
        return

    anime_name = " ".join(context.args or []).strip()
    if not anime_name:
        await msg.reply_text("Usage: /addanime Naruto")
        return

    try:
        await ensure_anime_catalog_seeded()
        created, clean_name = await add_anime(
            anime_name,
            created_by=int(user.id),
        )
    except Exception as exc:
        print("ADD ANIME FAILED:", repr(exc), flush=True)
        await msg.reply_text("❌ Failed to add Anime. Please try again.")
        return

    if not created:
        await msg.reply_text(
            f"⚠️ Anime already exists: {escape_html(clean_name)}",
            parse_mode="HTML",
        )
        return

    await msg.reply_text(
        f"✅ New added Anime {escape_html(clean_name)}",
        parse_mode="HTML",
    )

    if ADDING_LOG_CHANNEL_ID:
        try:
            await context.bot.send_message(
                chat_id=ADDING_LOG_CHANNEL_ID,
                text=(
                    f"New added Anime {escape_html(clean_name)}\n\n"
                    f"By {mention_user(user)}."
                ),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as exc:
            print("SEND ADDING ANIME LOG FAILED:", repr(exc), flush=True)


async def _handle_limited_legacy_add(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    media_info: dict,
    caption: str,
) -> bool:
    parsed = parse_add_caption(caption)
    if not parsed:
        return False

    card_id_provided = bool(parsed.pop("_cardIdProvided", False))
    parsed["cardId"] = str(parsed.get("cardId", "")).strip()

    if not is_limited_card(parsed, card_id_provided):
        return False

    if not is_owner(update.effective_user):
        await update.effective_message.reply_text(
            "❌ Limited cards can only be added/updated by the owner."
        )
        return True

    if not card_id_provided or not parsed["cardId"] or parsed["cardId"].isdigit():
        await update.effective_message.reply_text(
            "❌ Limited cards require a custom non-numeric ID. "
            "Example: /add 1a | Special Name | Limited | Bika Limited"
        )
        return True

    parsed["rarity"] = str(LIMITED_RARITY_NAME)
    collection_name = LIMITED_CARDS_COLLECTION
    db = get_db()

    existing, duplicate_other = await asyncio.gather(
        db[collection_name].find_one({"cardId": parsed["cardId"]}),
        db.photos.find_one({"cardId": parsed["cardId"]}, {"_id": 1}),
    )
    if duplicate_other and not existing:
        await update.effective_message.reply_text(
            f"❌ Card ID {parsed['cardId']} already exists in photos."
        )
        return True

    unique_id = str(media_info.get("fileUniqueId", "")).strip()
    duplicate_matches: list[dict] = []
    if unique_id:
        normal_dupes, limited_dupes = await asyncio.gather(
            db.photos.find(
                {
                    "fileUniqueId": unique_id,
                    "cardId": {"$ne": str(parsed["cardId"])},
                },
                {"_id": 0, "cardId": 1, "name": 1},
            ).limit(5).to_list(5),
            db[collection_name].find(
                {
                    "fileUniqueId": unique_id,
                    "cardId": {"$ne": str(parsed["cardId"])},
                },
                {"_id": 0, "cardId": 1, "name": 1},
            ).limit(5).to_list(5),
        )
        duplicate_matches = normal_dupes + limited_dupes

    action = "Update" if existing else "Saved"
    channel_caption = _database_caption(action, parsed, update.effective_user)
    old_storage = (
        {
            "storageChatId": existing.get("storageChatId"),
            "storageMessageId": existing.get("storageMessageId"),
        }
        if existing
        else None
    )
    same_media = bool(
        existing
        and unique_id
        and str(existing.get("fileUniqueId", "")) == unique_id
        and existing.get("storageChatId")
        and existing.get("storageMessageId")
    )

    op_id = secrets.token_hex(12)
    now = utcnow()
    base_doc = {
        **parsed,
        "mimeType": media_info.get("mimeType", ""),
        "fileName": media_info.get("fileName", ""),
        "addedBy": update.effective_user.id,
        "updatedAt": now,
    }
    if not existing:
        base_doc["createdAt"] = now

    storage = None
    try:
        await db[ADD_OPERATION_COLLECTION].insert_one(
            {
                "_id": op_id,
                "status": "prepared",
                "collectionName": collection_name,
                "cardId": parsed["cardId"],
                "document": base_doc,
                "createdAt": now,
            }
        )
        if same_media:
            storage = {
                "storageChatId": existing["storageChatId"],
                "storageMessageId": existing["storageMessageId"],
                "fileId": existing.get("fileId") or media_info["fileId"],
                "fileUniqueId": existing.get("fileUniqueId") or unique_id,
                "mediaType": existing.get("mediaType") or media_info["mediaType"],
            }
            if not await _edit_database_caption(context, storage, channel_caption):
                storage = await _post_to_card_database_channel(
                    context,
                    media_info["fileId"],
                    channel_caption,
                    media_info["mediaType"],
                )
        else:
            storage = await _post_to_card_database_channel(
                context,
                media_info["fileId"],
                channel_caption,
                media_info["mediaType"],
            )

        doc = {**base_doc, **storage}
        await db[ADD_OPERATION_COLLECTION].update_one(
            {"_id": op_id},
            {
                "$set": {
                    "status": "archived",
                    "document": doc,
                    "storage": storage,
                    "oldStorage": old_storage,
                    "archivedAt": utcnow(),
                    "updatedAt": utcnow(),
                }
            },
        )

        try:
            # "createdAt" is already inside doc for a new card.
            # Do not also target the same field with $setOnInsert; MongoDB
            # rejects that as a path conflict (code 40).
            await db[collection_name].update_one(
                {"cardId": parsed["cardId"]},
                {"$set": doc},
                upsert=True,
            )
        except DuplicateKeyError:
            # A concurrent Add can win the unique cardId insert race.
            await db[collection_name].update_one(
                {"cardId": parsed["cardId"]},
                {"$set": doc},
            )
        await db[ADD_OPERATION_COLLECTION].update_one(
            {"_id": op_id},
            {
                "$set": {
                    "status": "completed",
                    "completedAt": utcnow(),
                    "updatedAt": utcnow(),
                }
            },
        )

        if not same_media and old_storage:
            await _delete_database_message(context, old_storage)

    except TelegramError as exc:
        await db[ADD_OPERATION_COLLECTION].update_one(
            {"_id": op_id},
            {
                "$set": {
                    "status": "failed",
                    "lastError": str(exc),
                    "updatedAt": utcnow(),
                }
            },
        )
        await update.effective_message.reply_text(
            f"❌ Failed to archive Limited card: {escape_html(str(exc))}"
        )
        return True
    except Exception as exc:
        await db[ADD_OPERATION_COLLECTION].update_one(
            {"_id": op_id},
            {
                "$set": {
                    "status": "archived" if storage else "failed",
                    "lastError": repr(exc),
                    "updatedAt": utcnow(),
                }
            },
        )
        await update.effective_message.reply_text(
            "❌ Limited card save encountered an error. "
            "The archived operation will be recovered safely."
        )
        return True

    warning = ""
    if duplicate_matches:
        first = duplicate_matches[0]
        warning = (
            "\n\n⚠️ Possible duplicate media already used by "
            f"ID {escape_html(first.get('cardId'))}"
            f" — {escape_html(first.get('name'))}."
        )

    await update.effective_message.reply_text(
        f"{'✅' if action == 'Saved' else '♻️'} Limited card {action}.\n"
        f"ID: {parsed['cardId']}\n"
        f"Name: {parsed['name']}\n"
        f"Rarity: {parsed['rarity']}\n"
        f"Anime: {parsed['anime']}"
        f"{warning}"
    )
    return True


async def _save_normal_card(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session: dict,
    parsed: dict,
    media_info: dict,
) -> None:
    msg = update.effective_message
    user = update.effective_user
    db = get_db()

    card_id_provided = bool(parsed.pop("_cardIdProvided", False))
    parsed["cardId"] = str(parsed.get("cardId", "")).strip()

    if card_id_provided and not parsed["cardId"].isdigit():
        await msg.reply_text(
            "❌ Update ID must be numeric. Example: 240 | Yelan | Dv"
        )
        return

    if not card_id_provided:
        parsed["cardId"] = await _next_auto_card_id()

    parsed["anime"] = str(session.get("selectedAnime", "")).strip()
    if not parsed["anime"]:
        await msg.reply_text("❌ Add session has no selected Anime. Please send /add again.")
        return

    collection_name = "photos"
    other_collection_name = LIMITED_CARDS_COLLECTION

    existing, duplicate_other = await asyncio.gather(
        db[collection_name].find_one({"cardId": parsed["cardId"]}),
        db[other_collection_name].find_one({"cardId": parsed["cardId"]}, {"_id": 1}),
    )

    if duplicate_other and not existing:
        await msg.reply_text(
            f"❌ Card ID {parsed['cardId']} belongs to a Limited card."
        )
        return

    action = "Update" if existing else "Saved"

    unique_id = str(media_info.get("fileUniqueId", "")).strip()
    duplicate_matches: list[dict] = []
    if unique_id:
        normal_dupes, limited_dupes = await asyncio.gather(
            db.photos.find(
                {
                    "fileUniqueId": unique_id,
                    "cardId": {"$ne": str(parsed["cardId"])},
                },
                {"_id": 0, "cardId": 1, "name": 1},
            ).limit(5).to_list(5),
            db[LIMITED_CARDS_COLLECTION].find(
                {
                    "fileUniqueId": unique_id,
                    "cardId": {"$ne": str(parsed["cardId"])},
                },
                {"_id": 0, "cardId": 1, "name": 1},
            ).limit(5).to_list(5),
        )
        duplicate_matches = normal_dupes + limited_dupes

    channel_caption = _database_caption(action, parsed, user)
    old_storage = (
        {
            "storageChatId": existing.get("storageChatId"),
            "storageMessageId": existing.get("storageMessageId"),
        }
        if existing
        else None
    )
    same_media = bool(
        existing
        and unique_id
        and str(existing.get("fileUniqueId", "")) == unique_id
        and existing.get("storageChatId")
        and existing.get("storageMessageId")
    )

    op_id = secrets.token_hex(12)
    now = utcnow()
    base_doc = {
        **parsed,
        "mimeType": media_info.get("mimeType", ""),
        "fileName": media_info.get("fileName", ""),
        "addedBy": user.id,
        "updatedAt": now,
    }
    if not existing:
        base_doc["createdAt"] = now

    storage = None
    try:
        await db[ADD_OPERATION_COLLECTION].insert_one(
            {
                "_id": op_id,
                "status": "prepared",
                "collectionName": collection_name,
                "cardId": parsed["cardId"],
                "document": base_doc,
                "createdAt": now,
            }
        )

        if same_media:
            storage = {
                "storageChatId": existing["storageChatId"],
                "storageMessageId": existing["storageMessageId"],
                "fileId": existing.get("fileId") or media_info["fileId"],
                "fileUniqueId": existing.get("fileUniqueId") or unique_id,
                "mediaType": existing.get("mediaType") or media_info["mediaType"],
            }
            if not await _edit_database_caption(context, storage, channel_caption):
                storage = await _post_to_card_database_channel(
                    context,
                    media_info["fileId"],
                    channel_caption,
                    media_info["mediaType"],
                )
        else:
            storage = await _post_to_card_database_channel(
                context,
                media_info["fileId"],
                channel_caption,
                media_info["mediaType"],
            )

        doc = {**base_doc, **storage}

        await db[ADD_OPERATION_COLLECTION].update_one(
            {"_id": op_id},
            {
                "$set": {
                    "status": "archived",
                    "document": doc,
                    "storage": storage,
                    "oldStorage": old_storage,
                    "archivedAt": utcnow(),
                    "updatedAt": utcnow(),
                }
            },
        )

        try:
            # "createdAt" is already inside doc for a new card.
            # Do not also target the same field with $setOnInsert; MongoDB
            # rejects that as a path conflict (code 40).
            await db[collection_name].update_one(
                {"cardId": parsed["cardId"]},
                {"$set": doc},
                upsert=True,
            )
        except DuplicateKeyError:
            # A concurrent Add can win the unique cardId insert race.
            await db[collection_name].update_one(
                {"cardId": parsed["cardId"]},
                {"$set": doc},
            )

        await _sync_card_counter_at_least(parsed["cardId"])

        await db[ADD_OPERATION_COLLECTION].update_one(
            {"_id": op_id},
            {
                "$set": {
                    "status": "completed",
                    "completedAt": utcnow(),
                    "updatedAt": utcnow(),
                }
            },
        )

        if not same_media and old_storage:
            await _delete_database_message(context, old_storage)

    except TelegramError as exc:
        await db[ADD_OPERATION_COLLECTION].update_one(
            {"_id": op_id},
            {
                "$set": {
                    "status": "failed",
                    "lastError": str(exc),
                    "updatedAt": utcnow(),
                }
            },
        )
        await msg.reply_text(
            "❌ Failed to post card media to Bika Database channel.\n\n"
            "Check CARD_DATABASE_CHANNEL_ID and the bot's channel permissions.\n"
            f"Telegram error: {escape_html(str(exc))}"
        )
        return
    except Exception as exc:
        await db[ADD_OPERATION_COLLECTION].update_one(
            {"_id": op_id},
            {
                "$set": {
                    "status": "archived" if storage else "failed",
                    "lastError": repr(exc),
                    "updatedAt": utcnow(),
                }
            },
        )
        await msg.reply_text(
            "❌ Card save encountered an error. "
            "The archived operation will be recovered safely."
        )
        return

    await _touch_add_session(
        str(session["_id"]),
        context.bot,
        anime=str(parsed["anime"]),
    )

    warning = ""
    if duplicate_matches:
        first = duplicate_matches[0]
        warning = (
            "\n\n⚠️ Possible duplicate media already used by "
            f"ID {escape_html(first.get('cardId'))}"
            f" — {escape_html(first.get('name'))}."
        )

    await msg.reply_text(
        f"{'✅' if action == 'Saved' else '♻️'} Card {action}.\n"
        f"ID: {parsed['cardId']}\n"
        f"Name: {parsed['name']}\n"
        f"Rarity: {parsed['rarity']}\n"
        f"Anime: {parsed['anime']}\n"
        f"Media: {storage.get('mediaType', media_info['mediaType'])}\n"
        f"Collection: {collection_name}"
        f"{warning}"
    )


async def photo_add_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed_add_chat(update):
        return

    user = update.effective_user
    msg = update.effective_message
    chat = update.effective_chat
    if not user or not msg or not chat:
        return

    caption = (msg.caption or "").strip()
    media_info = _extract_message_media(msg)
    if media_info:
        print(
            "ADD MEDIA RECEIVED:"
            f" chat={int(chat.id)} user={int(user.id)}"
            f" type={media_info.get('mediaType')}"
            f" caption={caption[:200]!r}",
            flush=True,
        )
    if not media_info:
        # Do not silently ignore a captioned unsupported attachment.
        # This makes malformed client/media delivery visible to the adder.
        if caption and ("|" in caption or "｜" in caption):
            await msg.reply_text(
                "❌ Unsupported media type for /add.\n"
                "Send a photo, video, GIF, or an image/video file."
            )
        return
    looks_like_add_command = caption.casefold().startswith("/add")

    if is_forwarded_message(msg):
        if looks_like_add_command:
            await msg.reply_text(
                "❌ Forward add is disabled. Please upload the media directly with /add."
            )
        return

    if not await is_allowed_adder(user):
        if looks_like_add_command or "|" in caption:
            await msg.reply_text(
                "❌ You are not allowed to add/update cards. "
                "Ask the owner to use /addadder for your account."
            )
        return

    # Preserve the old Limited owner-only path.
    if looks_like_add_command:
        handled = await _handle_limited_legacy_add(
            update,
            context,
            media_info,
            caption,
        )
        if handled:
            return

        await msg.reply_text(
            "❌ Normal cards now use the new Add flow.\n\n"
            "1) Send /add\n"
            "2) Choose Anime\n"
            "3) Send media with:\n"
            "Yelan | Lg"
        )
        return

    try:
        session = await _get_active_add_session(int(user.id), int(chat.id))
        if not session:
            if "|" in caption or "｜" in caption:
                await msg.reply_text("❌ Please send /add first, then choose Anime.")
            return

        await _touch_add_session(str(session["_id"]), context.bot)
    except Exception as exc:
        print("ADD SESSION LOOKUP/TOUCH FAILED:", repr(exc), flush=True)
        await msg.reply_text(
            "❌ Add session could not be read. Please send /add again."
        )
        return

    # Accept full-width pipes. The parser also accepts an optional
    # "Media +" input marker without storing it in the character name.
    caption = caption.replace("｜", "|").strip()
    parsed = parse_normal_add_caption(caption)
    if not parsed:
        await msg.reply_text(
            "❌ Invalid Add format.\n\n"
            "New card:\n"
            "Yelan | Lg\n"
            "or\n"
            "Media + Yelan | Lg\n\n"
            "Update:\n"
            "240 | Yelan | Dv\n"
            "or\n"
            "Media + 240 | Yelan | Dv\n\n"
            "Rarity codes: Su, Ca, Cv, Dv, My, Lg, Ra, Un, Co"
        )
        return

    print(
        "ADD PARSED:"
        f" user={int(user.id)} chat={int(chat.id)}"
        f" cardId={parsed.get('cardId')!r}"
        f" name={parsed.get('name')!r}"
        f" rarity={parsed.get('rarity')!r}"
        f" anime={session.get('selectedAnime')!r}",
        flush=True,
    )

    try:
        await _save_normal_card(
            update,
            context,
            session,
            parsed,
            media_info,
        )
    except Exception as exc:
        print("ADD SAVE UNHANDLED ERROR:", repr(exc), flush=True)
        try:
            await msg.reply_text(
                "❌ Failed to process this card. "
                "Please send the same media + caption again."
            )
        except Exception:
            pass


async def add_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user or not query.message:
        return

    data = str(query.data or "")
    parts = data.split(":")
    if len(parts) < 2:
        await query.answer("Invalid Add action.", show_alert=True)
        return

    token = parts[1]
    # Check expiry inside MongoDB instead of comparing a MongoDB-decoded
    # naive datetime with utcnow()'s timezone-aware datetime. Motor/PyMongo
    # decodes BSON datetimes as naive UTC by default, so a direct Python
    # comparison can raise TypeError and make the button appear unresponsive.
    session = await get_db()[ADD_SESSION_COLLECTION].find_one(
        {
            "_id": token,
            "status": "active",
            "expiresAt": {"$gt": utcnow()},
        }
    )
    if not session:
        await query.answer("Add session expired. Send /add again.", show_alert=True)
        return

    if int(session.get("userId", 0) or 0) != int(query.from_user.id):
        await query.answer("This Add session belongs to another user.", show_alert=True)
        return

    if int(session.get("chatId", 0) or 0) != int(query.message.chat_id):
        await query.answer("This Add session belongs to another chat.", show_alert=True)
        return

    action = parts[0]

    try:
        if action == "addsel" and len(parts) == 3:
            absolute_index = int(parts[2])
            page = max(0, absolute_index // ADD_ANIME_PAGE_SIZE)
            names, _total = await list_animes(page, ADD_ANIME_PAGE_SIZE)
            local_index = absolute_index % ADD_ANIME_PAGE_SIZE
            if local_index >= len(names):
                await query.answer("Anime is no longer available.", show_alert=True)
                return

            selected = names[local_index]
            touched = await _touch_add_session(
                token,
                context.bot,
                anime=selected,
            )
            if not touched:
                await query.answer("Add session expired.", show_alert=True)
                return

            await query.edit_message_text(
                f"✅ <b>Selected Anime:</b> {escape_html(selected)}\n\n"
                "Now send the character media with:\n\n"
                "<code>Yelan | Lg</code>\n"
                "or\n"
                "<code>240 | Yelan | Dv</code>\n\n"
                "The selected Anime will be used for every card until you "
                "change it or the 3-minute session expires.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    [[
                        action_button(
                            "🔄 Change Anime",
                            "primary",
                            callback_data=f"addchange:{token}",
                        ),
                        action_button(
                            "✖ Cancel",
                            "danger",
                            callback_data=f"addcancel:{token}",
                        ),
                    ]]
                ),
            )
            await query.answer("Anime selected.")
            return

        if action == "addpage" and len(parts) == 3:
            page = max(0, int(parts[2]))
            await _touch_add_session(token, context.bot)
            await _render_add_anime_page(
                bot=context.bot,
                chat_id=int(query.message.chat_id),
                message_id=int(query.message.message_id),
                token=token,
                page=page,
            )
            await query.answer()
            return

        if action == "addchange" and len(parts) == 2:
            await _touch_add_session(token, context.bot, anime="")
            await _render_add_anime_page(
                bot=context.bot,
                chat_id=int(query.message.chat_id),
                message_id=int(query.message.message_id),
                token=token,
                page=0,
            )
            await query.answer("Choose Anime.")
            return

        if action == "addcancel" and len(parts) == 2:
            now = utcnow()
            cancelled = await get_db()[ADD_SESSION_COLLECTION].find_one_and_update(
                {"_id": token, "status": "active"},
                {
                    "$set": {
                        "status": "cancelled",
                        "closedAt": now,
                        "updatedAt": now,
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
            _cancel_add_session_task(token)
            if cancelled:
                await query.edit_message_text(
                    "✖ <b>Add session cancelled.</b>\n\n"
                    "Send /add to start again.",
                    parse_mode="HTML",
                )
            await query.answer("Cancelled.")
            return

    except Exception as exc:
        # Never leave Telegram's callback spinner hanging when an unexpected
        # runtime/DB/Telegram error occurs inside the Add wizard.
        print("ADD CALLBACK ERROR:", repr(exc), flush=True)
        try:
            await query.answer("Unable to process this Add action.", show_alert=True)
        except TelegramError:
            pass
        return

    await query.answer("Unknown Add action.", show_alert=True)


def register_photo_add_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("add", start_add_cmd))
    app.add_handler(CommandHandler("addanime", addanime_cmd))
    app.add_handler(
        CallbackQueryHandler(
            add_callback,
            pattern=r"^add(?:sel|page|change|cancel):",
        )
    )
    add_media_filter = (
        filters.PHOTO
        | filters.VIDEO
        | filters.ANIMATION
        | filters.Document.ALL
    )
    app.add_handler(MessageHandler(add_media_filter, photo_add_handler))
