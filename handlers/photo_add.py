from __future__ import annotations

import config
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from database.mongodb import get_db
from utils.parser import parse_add_caption, parse_forward_character
from utils.permissions import is_owner
from utils.text import escape_html, mention_user, utcnow

CARD_DATABASE_CHANNEL_ID = config.CARD_DATABASE_CHANNEL_ID
RARITY_ORDER = config.RARITY_ORDER

# Adder Group ထဲကနေ /add သုံးချင်တဲ့ group ID.
# config.py ထဲမှာ ADDER_GROUP_ID ရှိရင် အဲဒါကိုသုံးမယ်။
# မရှိရင် default group ID ကိုသုံးမယ်။
ADDER_GROUP_ID = int(getattr(config, "ADDER_GROUP_ID", -1003983636133) or -1003983636133)

SETTINGS_ID = "config"
CARD_COUNTER_ID = "photo_card_id"
CARD_RESERVATIONS_COLLECTION = "card_id_reservations"


def is_allowed_add_chat(update: Update) -> bool:
    chat = update.effective_chat
    if not chat:
        return False

    # Bot DM ထဲမှာ add ခွင့်ပြု
    if chat.type == "private":
        return True

    # သတ်မှတ်ထားတဲ့ Adder Group ထဲမှာ add ခွင့်ပြု
    return int(chat.id) == int(ADDER_GROUP_ID)


async def is_allowed_adder(user_id: int) -> bool:
    if is_owner(user_id):
        return True

    settings = await get_db().bot_settings.find_one(
        {"_id": SETTINGS_ID},
        {"adderIds": 1},
    )
    return int(user_id) in [int(x) for x in (settings or {}).get("adderIds", [])]


async def _max_numeric_card_id() -> int:
    docs = await get_db().photos.aggregate([
        {"$match": {"cardId": {"$regex": r"^[0-9]+$"}}},
        {"$project": {"cardIdNum": {"$toInt": "$cardId"}}},
        {"$sort": {"cardIdNum": -1}},
        {"$limit": 1},
    ]).to_list(1)
    return int(docs[0]["cardIdNum"]) if docs else 0


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
    docs = await get_db().photos.aggregate([
        {"$match": {"cardId": {"$regex": r"^[0-9]+$"}}},
        {"$project": {"_id": 0, "cardIdNum": {"$toInt": "$cardId"}}},
    ]).to_list(None)
    return {int(doc["cardIdNum"]) for doc in docs if int(doc.get("cardIdNum", 0) or 0) > 0}


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


async def _post_to_card_database_channel(
    context: ContextTypes.DEFAULT_TYPE,
    file_id: str,
    caption: str,
) -> dict:
    if not CARD_DATABASE_CHANNEL_ID:
        raise RuntimeError("CARD_DATABASE_CHANNEL_ID is missing in .env")

    sent = await context.bot.send_photo(
        chat_id=CARD_DATABASE_CHANNEL_ID,
        photo=file_id,
        caption=caption,
        parse_mode="HTML",
    )

    best = sent.photo[-1] if sent.photo else None
    return {
        "storageChatId": sent.chat_id,
        "storageMessageId": sent.message_id,
        "fileId": best.file_id if best else file_id,
        "fileUniqueId": best.file_unique_id if best else "",
    }


async def photo_add_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed_add_chat(update):
        return

    if not update.effective_user:
        return

    msg = update.effective_message
    if not msg or not msg.photo:
        return

    caption = (msg.caption or "").strip()
    looks_like_add = caption.lower().startswith("/add") or bool(parse_forward_character(caption))

    # /add မဟုတ်တဲ့ group photo တွေကို ignore
    if not looks_like_add:
        return

    if not await is_allowed_adder(update.effective_user.id):
        await msg.reply_text(
            "❌ You are not allowed to add/update cards. "
            "Ask the owner to use /addadder for your account."
        )
        return

    file_id = msg.photo[-1].file_id

    parsed = parse_add_caption(caption)
    mode = "Card"

    if not parsed:
        parsed = parse_forward_character(caption)
        mode = "Forward card"

    if not parsed:
        await msg.reply_text(
            "❌ Invalid add format.\n\n"
            "New card with auto ID:\n"
            "/add Yelan | Legendary | Genshin Impact\n\n"
            "Update/save a specific ID:\n"
            "/add 2 | Yelan | Legendary | Genshin Impact\n\n"
            f"Allowed rarities:\n{', '.join(RARITY_ORDER)}"
        )
        return

    reserved_auto_card_id = ""
    card_id_provided = bool(parsed.pop("_cardIdProvided", False))

    if not card_id_provided:
        parsed["cardId"] = await _reserve_next_card_id()
        reserved_auto_card_id = parsed["cardId"]
    else:
        parsed["cardId"] = str(parsed.get("cardId", "")).strip()
        if not parsed["cardId"].isdigit():
            await msg.reply_text("❌ Card ID must be a number.")
            return

    db = get_db()
    try:
        existing = await db.photos.find_one(
            {"cardId": parsed["cardId"]},
            {"_id": 1, "createdAt": 1},
        )

        action = "Update" if existing else "Saved"
        channel_caption = _database_caption(action, parsed, update.effective_user)

        try:
            storage = await _post_to_card_database_channel(context, file_id, channel_caption)
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
            "storageChatId": storage["storageChatId"],
            "storageMessageId": storage["storageMessageId"],
            "addedBy": update.effective_user.id,
            "updatedAt": now,
        }

        await db.photos.update_one(
            {"cardId": parsed["cardId"]},
            {"$set": doc, "$setOnInsert": {"createdAt": now}},
            upsert=True,
        )

        await _sync_card_counter_at_least(parsed["cardId"])

        icon = "✅" if action == "Saved" else "♻️"
        await msg.reply_text(
            f"{icon} {mode} {action}.\n"
            f"ID: {parsed['cardId']}\n"
            f"Name: {parsed['name']}\n"
            f"Rarity: {parsed['rarity']}\n"
            f"Anime: {parsed['anime']}\n"
            f"Bika Database Message ID: {storage['storageMessageId']}"
        )
    finally:
        if reserved_auto_card_id:
            await _release_reserved_card_id(reserved_auto_card_id)


def register_photo_add_handlers(app: Application) -> None:
    # DM + Adder Group နှစ်ခုလုံးက photo captions တွေဖမ်းရန်
    app.add_handler(MessageHandler(filters.PHOTO, photo_add_handler))
