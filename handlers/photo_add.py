from __future__ import annotations

import config
from pymongo.errors import DuplicateKeyError
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from database.mongodb import get_db
from utils.parser import parse_add_caption
from utils.permissions import is_owner
from utils.text import escape_html, mention_user, utcnow

CARD_DATABASE_CHANNEL_ID = config.CARD_DATABASE_CHANNEL_ID
RARITY_ORDER = config.RARITY_ORDER
LIMITED_CARDS_COLLECTION = getattr(config, "LIMITED_CARDS_COLLECTION", "limited_cards")
LIMITED_RARITY_NAME = getattr(config, "LIMITED_RARITY_NAME", "Limited")

# Adder Group ထဲကနေ /add သုံးချင်တဲ့ group ID.
# config.py ထဲမှာ ADDER_GROUP_ID ရှိရင် အဲဒါကိုသုံးမယ်။
# မရှိရင် default group ID ကိုသုံးမယ်။
ADDER_GROUP_ID = int(getattr(config, "ADDER_GROUP_ID", -1003983636133) or -1003983636133)

SETTINGS_ID = "config"
CARD_COUNTER_ID = "photo_card_id"
CARD_RESERVATIONS_COLLECTION = "card_id_reservations"


SUPPORTED_DOCUMENT_MIME_PREFIXES = ("image/", "video/")


def is_forwarded_message(msg) -> bool:
    """Detect Telegram forwarded/copy-forwarded messages and reject them for /add."""
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
    card_id = str(parsed.get("cardId", "")).strip()
    rarity = str(parsed.get("rarity", "")).strip()
    return rarity.lower() == str(LIMITED_RARITY_NAME).lower() or (card_id_provided and bool(card_id) and not card_id.isdigit())


def is_allowed_add_chat(update: Update) -> bool:
    chat = update.effective_chat
    if not chat:
        return False

    # Bot DM ထဲမှာ add ခွင့်ပြု
    if chat.type == "private":
        return False

    # သတ်မှတ်ထားတဲ့ Adder Group ထဲမှာ add ခွင့်ပြု
    return int(chat.id) == int(ADDER_GROUP_ID)


async def is_allowed_adder(user) -> bool:
    # Owner can be matched by OWNER_ID or OWNER_USERNAME.
    if is_owner(user):
        return True

    user_id = getattr(user, "id", 0)
    if not user_id:
        return False

    settings = await get_db().bot_settings.find_one(
        {"_id": SETTINGS_ID},
        {"adderIds": 1},
    )
    return int(user_id) in [int(x) for x in (settings or {}).get("adderIds", [])]


async def _max_numeric_card_id() -> int:
    db = get_db()
    max_id = 0
    for collection_name in ("photos", LIMITED_CARDS_COLLECTION):
        docs = await db[collection_name].aggregate([
            {"$match": {"cardId": {"$regex": r"^[0-9]+$"}}},
            {"$project": {"cardIdNum": {"$toInt": "$cardId"}}},
            {"$sort": {"cardIdNum": -1}},
            {"$limit": 1},
        ]).to_list(1)
        if docs:
            max_id = max(max_id, int(docs[0]["cardIdNum"]))
    return max_id


async def _ensure_card_counter() -> None:
    """Legacy counter compatibility.

    New auto-ID assignment uses the smallest missing numeric ID from 1.
    This counter is still kept in sync for old code compatibility.
    """
    db = get_db()
    existing = await db.counters.find_one({"_id": CARD_COUNTER_ID})
    if existing:
        return

    max_id = await _max_numeric_card_id()
    try:
        await db.counters.insert_one({
            "_id": CARD_COUNTER_ID,
            "seq": max_id,
            "updatedAt": utcnow(),
        })
    except Exception:
        # Another concurrent /add may have created it first.
        pass


async def _sync_card_counter_at_least(card_id: str) -> None:
    if not str(card_id).isdigit():
        return

    await _ensure_card_counter()
    await get_db().counters.update_one(
        {"_id": CARD_COUNTER_ID, "seq": {"$lt": int(card_id)}},
        {"$set": {"seq": int(card_id), "updatedAt": utcnow()}},
    )


async def _existing_numeric_card_ids() -> set[int]:
    db = get_db()
    existing: set[int] = set()
    for collection_name in ("photos", LIMITED_CARDS_COLLECTION):
        docs = await db[collection_name].aggregate([
            {"$match": {"cardId": {"$regex": r"^[0-9]+$"}}},
            {"$project": {"_id": 0, "cardIdNum": {"$toInt": "$cardId"}}},
        ]).to_list(None)
        existing.update(int(doc["cardIdNum"]) for doc in docs if int(doc.get("cardIdNum", 0) or 0) > 0)
    return existing


async def _reserved_numeric_card_ids() -> set[int]:
    docs = await get_db()[CARD_RESERVATIONS_COLLECTION].find({}, {"_id": 1}).to_list(None)
    reserved: set[int] = set()
    for doc in docs:
        value = str(doc.get("_id", ""))
        if value.isdigit() and int(value) > 0:
            reserved.add(int(value))
    return reserved


async def _reserve_next_card_id() -> str:
    """Reserve the smallest missing numeric card ID starting from 1.

    Example:
      existing IDs: 1, 2, 3, 5, ..., 130
      next new ID: 4

    A short reservation document is used to reduce duplicate ID races when two
    adders upload at nearly the same time.
    """
    db = get_db()

    while True:
        existing_ids = await _existing_numeric_card_ids()
        reserved_ids = await _reserved_numeric_card_ids()
        unavailable = existing_ids | reserved_ids

        next_id = 1
        while next_id in unavailable:
            next_id += 1

        next_id_str = str(next_id)
        try:
            await db[CARD_RESERVATIONS_COLLECTION].insert_one({
                "_id": next_id_str,
                "reservedAt": utcnow(),
            })
            return next_id_str
        except DuplicateKeyError:
            # Another add operation reserved the same ID first. Try again.
            continue


async def _release_reserved_card_id(card_id: str) -> None:
    if not str(card_id).isdigit():
        return
    try:
        await get_db()[CARD_RESERVATIONS_COLLECTION].delete_one({"_id": str(card_id)})
    except Exception:
        pass


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

    Supported:
      - photo
      - video / mp4 sent as Telegram video
      - animation / gif
      - image/video sent as document
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
        mime_type = media.mime_type or ""
        if not mime_type.startswith(SUPPORTED_DOCUMENT_MIME_PREFIXES):
            return None
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


async def photo_add_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed_add_chat(update):
        return

    if not update.effective_user:
        return

    msg = update.effective_message
    if not msg:
        return

    media_info = _extract_message_media(msg)
    if not media_info:
        return

    caption = (msg.caption or "").strip()
    looks_like_add = caption.lower().startswith("/add")

    # Forward add is disabled. Direct uploads with /add only.
    if is_forwarded_message(msg):
        if looks_like_add:
            await msg.reply_text("❌ Forward add is disabled. Please upload the media directly with /add.")
        return

    # /add မဟုတ်တဲ့ group/private media တွေကို ignore
    if not looks_like_add:
        return

    if not await is_allowed_adder(update.effective_user):
        await msg.reply_text(
            "❌ You are not allowed to add/update cards. "
            "Ask the owner to use /addadder for your account."
        )
        return

    file_id = media_info["fileId"]
    media_type = media_info["mediaType"]

    parsed = parse_add_caption(caption)
    if not parsed:
        await msg.reply_text(
            "❌ Invalid add format.\n\n"
            "Normal card with auto ID:\n"
            "/add Yelan | Legendary | Genshin Impact\n\n"
            "Normal card with specific numeric ID:\n"
            "/add 2 | Yelan | Legendary | Genshin Impact\n\n"
            "Limited owner-give-only card:\n"
            "/add 1a | Special Name | Limited | Bika Limited\n\n"
            f"Allowed rarities:\n{', '.join(RARITY_ORDER)}"
        )
        return

    reserved_auto_card_id = ""
    card_id_provided = bool(parsed.pop("_cardIdProvided", False))
    parsed["cardId"] = str(parsed.get("cardId", "")).strip()

    limited_card = is_limited_card(parsed, card_id_provided)
    if limited_card and not is_owner(update.effective_user):
        await msg.reply_text(
            "❌ Limited cards can only be added/updated by the owner.\n"
            "Normal adders can add/update normal numeric ID cards only."
        )
        return

    if limited_card:
        parsed["rarity"] = str(LIMITED_RARITY_NAME)
        if not card_id_provided or not parsed["cardId"]:
            await msg.reply_text("❌ Limited cards require a custom ID. Example: /add 1a | Name | Limited | Anime")
            return
    else:
        if not card_id_provided:
            parsed["cardId"] = await _reserve_next_card_id()
            reserved_auto_card_id = parsed["cardId"]
        elif not parsed["cardId"].isdigit():
            await msg.reply_text("❌ Non-numeric IDs are only allowed for Rarity Limited cards.")
            return

    collection_name = LIMITED_CARDS_COLLECTION if limited_card else "photos"
    other_collection_name = "photos" if limited_card else LIMITED_CARDS_COLLECTION
    mode = "Limited card" if limited_card else "Card"

    db = get_db()
    try:
        existing = await db[collection_name].find_one(
            {"cardId": parsed["cardId"]},
            {"_id": 1, "createdAt": 1},
        )
        duplicate_other = await db[other_collection_name].find_one({"cardId": parsed["cardId"]}, {"_id": 1})
        if duplicate_other and not existing:
            await msg.reply_text(f"❌ Card ID {parsed['cardId']} already exists in {other_collection_name}.")
            return

        action = "Update" if existing else "Saved"
        channel_caption = _database_caption(action, parsed, update.effective_user)

        try:
            storage = await _post_to_card_database_channel(context, file_id, channel_caption, media_type)
        except TelegramError as exc:
            await msg.reply_text(
                "❌ Failed to post card media to Bika Database channel.\n\n"
                "Check these:\n"
                "1) CARD_DATABASE_CHANNEL_ID is correct\n"
                "2) Bot is admin in that private channel\n"
                f"3) Telegram error: {exc}"
            )
            return
        except Exception as exc:
            await msg.reply_text(f"❌ Bika Database channel setup error: {exc}")
            return

        now = utcnow()
        doc = {
            **parsed,
            "fileId": storage["fileId"],
            "fileUniqueId": storage.get("fileUniqueId", ""),
            "mediaType": storage.get("mediaType", media_type),
            "mimeType": media_info.get("mimeType", ""),
            "fileName": media_info.get("fileName", ""),
            "storageChatId": storage["storageChatId"],
            "storageMessageId": storage["storageMessageId"],
            "addedBy": update.effective_user.id,
            "updatedAt": now,
        }

        await db[collection_name].update_one(
            {"cardId": parsed["cardId"]},
            {"$set": doc, "$setOnInsert": {"createdAt": now}},
            upsert=True,
        )

        if not limited_card:
            await _sync_card_counter_at_least(parsed["cardId"])

        icon = "✅" if action == "Saved" else "♻️"
        await msg.reply_text(
            f"{icon} {mode} {action}.\n"
            f"ID: {parsed['cardId']}\n"
            f"Name: {parsed['name']}\n"
            f"Rarity: {parsed['rarity']}\n"
            f"Anime: {parsed['anime']}\n"
            f"Media: {storage.get('mediaType', media_type)}\n"
            f"Collection: {collection_name}\n"
            f"Bika Database Message ID: {storage['storageMessageId']}"
        )
    finally:
        if reserved_auto_card_id:
            await _release_reserved_card_id(reserved_auto_card_id)


def register_photo_add_handlers(app: Application) -> None:
    # DM + Adder Group နှစ်ခုလုံးက photo/video/gif/document captions တွေဖမ်းရန်
    app.add_handler(MessageHandler(filters.ATTACHMENT, photo_add_handler))
