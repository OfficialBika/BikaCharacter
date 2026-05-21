from __future__ import annotations

from pymongo import ReturnDocument
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from config import BOT_MUTE_SECONDS, DEFAULT_CHANGETIME
from database.mongodb import get_db
from utils.cooldown import is_bot_muted, record_message_and_maybe_mute
from utils.db_helpers import ensure_group, ensure_user, get_random_photo
from utils.rarity import get_rarity_emoji
from utils.text import safe_chat_title, utcnow


def is_countable_message(update: Update) -> bool:
    msg = update.effective_message
    if not msg or not update.effective_user or update.effective_user.is_bot:
        return False
    if update.effective_chat.type not in ("group", "supergroup"):
        return False
    return bool(msg.text or msg.caption)


async def drop_listener(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_countable_message(update):
        return

    chat = update.effective_chat
    user = update.effective_user
    await ensure_user(user)
    group = await ensure_group(chat)
    if not group:
        return

    if await is_bot_muted(chat.id, user.id):
        return

    just_muted = await record_message_and_maybe_mute(update)
    if just_muted:
        await update.effective_message.reply_text(
            f"🤐 {user.first_name}, you sent too many messages in a row. "
            f"Bot will ignore you for {BOT_MUTE_SECONDS // 60} minutes."
        )
        return

    db = get_db()
    updated = await db.groups.find_one_and_update(
        {"groupId": int(chat.id)},
        {"$inc": {"messageCount": 1}, "$set": {"updatedAt": utcnow()}},
        return_document=ReturnDocument.AFTER,
    )
    change_time = int((updated or {}).get("changeTime", DEFAULT_CHANGETIME) or DEFAULT_CHANGETIME)
    message_count = int((updated or {}).get("messageCount", 0) or 0)
    if message_count < change_time:
        return

    await db.groups.update_one({"groupId": int(chat.id)}, {"$set": {"messageCount": 0, "updatedAt": utcnow()}})
    await spawn_random_character(update, context)


async def spawn_random_character(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    photo = await get_random_photo()
    if not photo:
        return

    emoji = get_rarity_emoji(photo.get("rarity"))
    group_name = safe_chat_title(chat)
    caption = (
        f"{emoji} A new Character has spawned in {group_name} .\n\n"
        "To own this character, send the character name quickly using /bika name ."
    )
    try:
        sent = await context.bot.send_photo(chat_id=chat.id, photo=photo["fileId"], caption=caption)
    except Exception as exc:
        print("DROP SEND ERROR:", repr(exc))
        return

    active_drop = {
        "cardId": str(photo.get("cardId", "")),
        "name": str(photo.get("name", "")),
        "normalizedName": str(photo.get("normalizedName", "")),
        "rarity": str(photo.get("rarity", "Common")),
        "anime": str(photo.get("anime", "")),
        "fileId": str(photo.get("fileId", "")),
        "messageId": sent.message_id,
        "isClaimed": False,
        "claimedByUserId": 0,
        "claimedByName": "",
        "droppedAt": utcnow(),
    }
    await get_db().groups.update_one(
        {"groupId": int(chat.id)},
        {"$set": {"activeDrop": active_drop, "updatedAt": utcnow()}},
    )


def register_drop_handlers(app: Application) -> None:
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & (filters.TEXT | filters.CAPTION), drop_listener))
