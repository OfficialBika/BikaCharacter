from __future__ import annotations

import asyncio
import random
import secrets
from datetime import timedelta

from pymongo import ReturnDocument
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from config import BOT_MUTE_SECONDS, CLAIM_CAPTCHA_SECONDS, DEFAULT_CHANGETIME
from database.mongodb import get_db
from utils.cooldown import is_bot_muted, record_message_and_maybe_mute
from utils.db_helpers import ensure_group, ensure_user, get_drop_photo_for_rarity
from utils.rarity import get_rarity_emoji, get_scheduled_drop_rarity
from utils.text import escape_html, safe_chat_title, utcnow

# For these rarity milestones, captcha appears BEFORE the card/photo spawns.
# If solved correctly within CLAIM_CAPTCHA_SECONDS, then the card spawns normally.
# If a wrong button is pressed or timeout happens, that scheduled drop is lost.
PRE_SPAWN_CAPTCHA_RARITIES = {"Divine", "CrossVerse", "Cataphract", "Supreme"}


def is_countable_message(update: Update) -> bool:
    msg = update.effective_message
    if not msg or not update.effective_user or update.effective_user.is_bot:
        return False
    if update.effective_chat.type not in ("group", "supergroup"):
        return False
    return bool(msg.text or msg.caption)


def needs_pre_spawn_captcha(rarity: str | None) -> bool:
    return str(rarity or "") in PRE_SPAWN_CAPTCHA_RARITIES


def make_pre_spawn_captcha() -> dict:
    """Create a captcha with one correct answer and four wrong answers."""
    a = random.randint(3, 19)
    b = random.randint(2, 17)
    correct = a + b
    wrongs: set[int] = set()
    while len(wrongs) < 4:
        value = correct + random.choice([-10, -8, -6, -4, -3, -2, 2, 3, 4, 5, 7, 9, 11])
        if value > 0 and value != correct:
            wrongs.add(value)
    options = list(wrongs) + [correct]
    random.shuffle(options)
    return {
        "question": f"{a} + {b} = ?",
        "answerIndex": options.index(correct),
        "options": options,
        "nonce": secrets.token_hex(4),
    }


def pre_spawn_keyboard(chat_id: int, nonce: str, options: list[int]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(str(value), callback_data=f"precap:{chat_id}:{nonce}:{idx}")
        for idx, value in enumerate(options)
    ]
    return InlineKeyboardMarkup([buttons[:3], buttons[3:]])


async def drop_listener(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_countable_message(update):
        return

    chat = update.effective_chat
    user = update.effective_user
    await ensure_user(user)
    group = await ensure_group(chat)
    if not group:
        return

    active = (group or {}).get("activeDrop") or {}
    pre_cap = active.get("preSpawnCaptcha") or {}
    if pre_cap.get("status") == "pending":
        # A high-rarity pre-spawn captcha is already active. Do not advance drop counter
        # until it is solved, failed, or timed out.
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
    db = get_db()

    group_after_inc = await db.groups.find_one_and_update(
        {"groupId": int(chat.id)},
        {"$inc": {"totalDrops": 1}, "$set": {"updatedAt": utcnow()}},
        return_document=ReturnDocument.AFTER,
    )
    drop_number = int((group_after_inc or {}).get("totalDrops", 1) or 1)
    scheduled_rarity = get_scheduled_drop_rarity(drop_number)

    if needs_pre_spawn_captcha(scheduled_rarity):
        await send_pre_spawn_captcha(update, context, scheduled_rarity, drop_number)
        return

    await send_spawn_card(context, int(chat.id), chat, scheduled_rarity, drop_number)


async def send_pre_spawn_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE, scheduled_rarity: str, drop_number: int) -> None:
    chat = update.effective_chat
    captcha = make_pre_spawn_captcha()
    expires_at = utcnow() + timedelta(seconds=CLAIM_CAPTCHA_SECONDS)

    text = (
        "🧩 <b>HIGH RARITY CAPTCHA</b>\n\n"
        f"{get_rarity_emoji(scheduled_rarity)} <b>{escape_html(scheduled_rarity)}</b> card is trying to spawn in "
        f"<b>{escape_html(safe_chat_title(chat))}</b>.\n\n"
        f"Solve this captcha within <b>{CLAIM_CAPTCHA_SECONDS}s</b>.\n"
        "✅ Correct answer = character will spawn.\n"
        "❌ Wrong answer or timeout = this drop will be lost.\n\n"
        f"Question: <b>{escape_html(captcha['question'])}</b>"
    )

    sent = await update.effective_message.reply_html(
        text,
        reply_markup=pre_spawn_keyboard(int(chat.id), captcha["nonce"], captcha["options"]),
    )

    await get_db().groups.update_one(
        {"groupId": int(chat.id)},
        {
            "$set": {
                "activeDrop": {
                    "cardId": "",
                    "name": "",
                    "normalizedName": "",
                    "rarity": scheduled_rarity,
                    "anime": "",
                    "fileId": "",
                    "messageId": 0,
                    "dropNumber": int(drop_number),
                    "scheduledRarity": scheduled_rarity,
                    "isClaimed": False,
                    "claimedByUserId": 0,
                    "claimedByName": "",
                    "droppedAt": None,
                    "preSpawnCaptcha": {
                        "status": "pending",
                        "nonce": captcha["nonce"],
                        "question": captcha["question"],
                        "answerIndex": int(captcha["answerIndex"]),
                        "options": captcha["options"],
                        "createdAt": utcnow(),
                        "expiresAt": expires_at,
                        "seconds": int(CLAIM_CAPTCHA_SECONDS),
                        "messageId": int(sent.message_id),
                    },
                },
                "updatedAt": utcnow(),
            }
        },
    )
    context.application.create_task(
        pre_spawn_timeout_task(context.bot, int(chat.id), captcha["nonce"], int(CLAIM_CAPTCHA_SECONDS))
    )


async def mark_pre_spawn_lost(chat_id: int, nonce: str, status: str) -> dict | None:
    return await get_db().groups.find_one_and_update(
        {
            "groupId": int(chat_id),
            "activeDrop.preSpawnCaptcha.nonce": str(nonce),
            "activeDrop.preSpawnCaptcha.status": "pending",
        },
        {
            "$set": {
                "activeDrop.preSpawnCaptcha.status": status,
                "activeDrop.preSpawnCaptcha.finishedAt": utcnow(),
                "updatedAt": utcnow(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )


async def clear_lost_pre_spawn(chat_id: int, nonce: str) -> None:
    latest = await get_db().groups.find_one({"groupId": int(chat_id), "activeDrop.preSpawnCaptcha.nonce": str(nonce)})
    pre_cap = ((latest or {}).get("activeDrop") or {}).get("preSpawnCaptcha") or {}
    if pre_cap.get("status") in {"failed", "timeout", "missing_card"}:
        await get_db().groups.update_one(
            {"groupId": int(chat_id), "activeDrop.preSpawnCaptcha.nonce": str(nonce)},
            {"$set": {"activeDrop": None, "updatedAt": utcnow()}},
        )


async def pre_spawn_timeout_task(bot, chat_id: int, nonce: str, seconds: int) -> None:
    await asyncio.sleep(max(1, int(seconds)))
    latest = await get_db().groups.find_one({"groupId": int(chat_id), "activeDrop.preSpawnCaptcha.nonce": str(nonce)})
    active = (latest or {}).get("activeDrop") or {}
    pre_cap = active.get("preSpawnCaptcha") or {}
    if pre_cap.get("status") != "pending":
        return

    updated = await mark_pre_spawn_lost(chat_id, nonce, "timeout")
    if not updated:
        return

    message_id = int(pre_cap.get("messageId", 0) or 0)
    text = "⌛ <b>CAPTCHA TIMEOUT</b>\n\n120 seconds finished. This scheduled high-rarity spawn has been lost."
    try:
        if message_id:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, parse_mode=ParseMode.HTML)
        else:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
    except Exception:
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
        except Exception:
            pass
    await clear_lost_pre_spawn(chat_id, nonce)


async def pre_spawn_captcha_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    parts = query.data.split(":")
    if len(parts) != 4:
        await query.answer("Invalid captcha.", show_alert=True)
        return

    _, chat_id_raw, nonce, choice_raw = parts
    chat_id = int(chat_id_raw)
    choice_index = int(choice_raw)

    latest = await get_db().groups.find_one({"groupId": int(chat_id), "activeDrop.preSpawnCaptcha.nonce": str(nonce)})
    active = (latest or {}).get("activeDrop") or {}
    pre_cap = active.get("preSpawnCaptcha") or {}
    if not active or pre_cap.get("status") != "pending":
        await query.answer("Captcha already finished.", show_alert=True)
        return

    correct_index = int(pre_cap.get("answerIndex", -1))
    if choice_index != correct_index:
        await mark_pre_spawn_lost(chat_id, nonce, "failed")
        try:
            await query.edit_message_text(
                "❌ <b>WRONG CAPTCHA</b>\n\nThis scheduled high-rarity spawn has been lost.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        await query.answer("Wrong. Spawn lost.", show_alert=True)
        await clear_lost_pre_spawn(chat_id, nonce)
        return

    scheduled_rarity = str(active.get("scheduledRarity") or active.get("rarity") or "Divine")
    drop_number = int(active.get("dropNumber", 0) or 0)

    solved = await get_db().groups.find_one_and_update(
        {
            "groupId": int(chat_id),
            "activeDrop.preSpawnCaptcha.nonce": str(nonce),
            "activeDrop.preSpawnCaptcha.status": "pending",
        },
        {
            "$set": {
                "activeDrop.preSpawnCaptcha.status": "solved",
                "activeDrop.preSpawnCaptcha.solvedByUserId": int(query.from_user.id),
                "activeDrop.preSpawnCaptcha.solvedByName": " ".join([query.from_user.first_name or "", query.from_user.last_name or ""]).strip()
                    or query.from_user.username
                    or str(query.from_user.id),
                "activeDrop.preSpawnCaptcha.finishedAt": utcnow(),
                "updatedAt": utcnow(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if not solved:
        await query.answer("Captcha already finished.", show_alert=True)
        return

    try:
        await query.edit_message_text(
            "✅ <b>CAPTCHA SOLVED</b>\n\nHigh-rarity character is spawning now!",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass
    await query.answer("Solved.")

    # Use the callback chat when available; otherwise send by chat_id only.
    chat = query.message.chat if query.message else None
    await send_spawn_card(context, int(chat_id), chat, scheduled_rarity, drop_number)


async def send_spawn_card(context: ContextTypes.DEFAULT_TYPE, chat_id: int, chat, scheduled_rarity: str, drop_number: int) -> None:
    photo = await get_drop_photo_for_rarity(scheduled_rarity)
    if not photo:
        await get_db().groups.update_one(
            {"groupId": int(chat_id)},
            {"$set": {"activeDrop": None, "updatedAt": utcnow()}},
        )
        try:
            await context.bot.send_message(chat_id=chat_id, text="❌ No card found for this scheduled rarity. Spawn lost.")
        except Exception:
            pass
        return

    emoji = get_rarity_emoji(photo.get("rarity"))
    group_name = safe_chat_title(chat) if chat else str(chat_id)
    caption = (
        f"{emoji} A new Character has spawned in {group_name} .\n\n"
        "To own this character, send the character name quickly using /bika name ."
    )
    try:
        sent = await context.bot.send_photo(chat_id=chat_id, photo=photo["fileId"], caption=caption)
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
        "messageId": int(sent.message_id),
        "dropNumber": int(drop_number),
        "scheduledRarity": scheduled_rarity,
        "isClaimed": False,
        "claimedByUserId": 0,
        "claimedByName": "",
        "droppedAt": utcnow(),
    }
    await get_db().groups.update_one(
        {"groupId": int(chat_id)},
        {"$set": {"activeDrop": active_drop, "updatedAt": utcnow()}},
    )


def register_drop_handlers(app: Application) -> None:
    app.add_handler(CallbackQueryHandler(pre_spawn_captcha_callback, pattern=r"^precap:-?\d+:[0-9a-f]+:\d+$"))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & (filters.TEXT | filters.CAPTION), drop_listener))
