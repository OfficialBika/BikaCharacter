from __future__ import annotations

import asyncio
import random
import secrets
from datetime import datetime, timezone, timedelta

from pymongo import ReturnDocument
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from config import CLAIM_CAPTCHA_SECONDS, CLAIM_DAILY_LIMIT, CLAIM_PREFIX_MIN_LENGTH
from database.mongodb import get_db
from utils.cooldown import should_ignore_update
from utils.claim_stats import get_daily_claim_count, log_claim_event, release_daily_claim, reserve_daily_claim, yangon_date_key
from utils.db_helpers import add_card_to_user, ensure_group, ensure_user, get_photo_by_card_id
from utils.parser import is_character_name_match, normalized_search_name
from utils.rarity import get_rarity_emoji
from utils.text import escape_html, mention_user, utcnow

# Divine and every rarity above it must be protected with captcha.
CAPTCHA_RARITIES = set()  # High-rarity captcha now happens before spawn in handlers/drop.py


def is_captcha_rarity(rarity: str | None) -> bool:
    return str(rarity or "") in CAPTCHA_RARITIES


def is_expired_datetime(value) -> bool:
    if not value:
        return False
    if isinstance(value, datetime):
        dt = value
    else:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt <= utcnow()


def make_captcha() -> dict:
    """Create a simple math captcha with one correct answer and four wrong answers."""
    a = random.randint(3, 19)
    b = random.randint(2, 17)
    correct = a + b
    wrongs: set[int] = set()
    while len(wrongs) < 4:
        value = correct + random.choice([-9, -7, -5, -3, -2, 2, 3, 4, 5, 6, 8, 10])
        if value > 0 and value != correct:
            wrongs.add(value)
    options = list(wrongs) + [correct]
    random.shuffle(options)
    return {
        "question": f"{a} + {b} = ?",
        "answer": correct,
        "options": options,
        "answerIndex": options.index(correct),
        "nonce": secrets.token_hex(4),
    }


def captcha_keyboard(chat_id: int, user_id: int, nonce: str, options: list[int]) -> InlineKeyboardMarkup:
    buttons = []
    for idx, value in enumerate(options):
        buttons.append(InlineKeyboardButton(str(value), callback_data=f"cap:{chat_id}:{user_id}:{nonce}:{idx}"))
    return InlineKeyboardMarkup([
        buttons[:3],
        buttons[3:],
    ])


async def mark_captcha_lost(chat_id: int, nonce: str, reason: str) -> dict | None:
    return await get_db().groups.find_one_and_update(
        {
            "groupId": int(chat_id),
            "activeDrop.captcha.nonce": str(nonce),
            "activeDrop.captcha.status": "pending",
            "activeDrop.isClaimed": False,
        },
        {
            "$set": {
                "activeDrop.isClaimed": True,
                "activeDrop.claimedByUserId": 0,
                "activeDrop.claimedByName": reason,
                "activeDrop.captcha.status": reason,
                "activeDrop.captcha.finishedAt": utcnow(),
                "updatedAt": utcnow(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )


async def captcha_timeout_task(bot, chat_id: int, nonce: str, seconds: int) -> None:
    await asyncio.sleep(max(1, int(seconds)))
    latest = await get_db().groups.find_one({"groupId": int(chat_id), "activeDrop.captcha.nonce": str(nonce)})
    captcha = ((latest or {}).get("activeDrop") or {}).get("captcha") or {}
    if captcha.get("status") != "pending":
        return

    updated = await mark_captcha_lost(chat_id, nonce, "Captcha Timeout")
    if not updated:
        return

    message_id = int(captcha.get("messageId", 0) or 0)
    text = "⌛ <b>CAPTCHA TIMEOUT</b>\n\n120 seconds finished. This high-rarity card has been lost."
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


async def send_claim_success(update_or_query, tg_user, chat, photo_doc: dict) -> None:
    message_target = getattr(update_or_query, "message", None) or getattr(update_or_query, "effective_message", None)
    text = (
        "🎉 <b>YOU GOT A NEW CHARACTER!</b>\n\n"
        f"👤 Claimed by: {mention_user(tg_user)}\n"
        f"{get_rarity_emoji(photo_doc.get('rarity'))} Name: <b>{escape_html(photo_doc.get('name'))}</b>\n"
        f"🆔 ID: <b>{escape_html(photo_doc.get('cardId'))}</b>\n"
        f"🏷 RARITY: <b>{escape_html(photo_doc.get('rarity'))}</b>\n"
        f"🌴 ANIME: <b>{escape_html(photo_doc.get('anime'))}</b>\n\n"
        "❄️ CHECK YOUR /harem !"
    )
    if message_target:
        await message_target.reply_html(text)


async def start_high_rarity_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE, active: dict) -> None:
    user = update.effective_user
    chat = update.effective_chat
    date_key = yangon_date_key()
    used = await get_daily_claim_count(user.id, date_key)
    if used >= CLAIM_DAILY_LIMIT:
        await update.message.reply_text(
            f"❌ Daily catch limit reached.\n"
            f"Myanmar/Yangon date: {date_key}\n"
            f"Used: {used}/{CLAIM_DAILY_LIMIT}\n"
            f"Remaining: 0"
        )
        return

    captcha = make_captcha()
    expires_at = utcnow() + timedelta(seconds=CLAIM_CAPTCHA_SECONDS)
    claimer_name = " ".join([user.first_name or "", user.last_name or ""]).strip() or user.username or str(user.id)

    updated = await get_db().groups.find_one_and_update(
        {
            "groupId": int(chat.id),
            "activeDrop.cardId": str(active.get("cardId")),
            "activeDrop.isClaimed": False,
            "$or": [
                {"activeDrop.captcha.status": {"$exists": False}},
                {"activeDrop.captcha.status": {"$ne": "pending"}},
            ],
        },
        {
            "$set": {
                "activeDrop.captcha": {
                    "status": "pending",
                    "userId": int(user.id),
                    "userName": claimer_name,
                    "nonce": captcha["nonce"],
                    "question": captcha["question"],
                    "answerIndex": int(captcha["answerIndex"]),
                    "options": captcha["options"],
                    "createdAt": utcnow(),
                    "expiresAt": expires_at,
                    "seconds": int(CLAIM_CAPTCHA_SECONDS),
                    "messageId": 0,
                },
                "updatedAt": utcnow(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    if not updated:
        latest = await get_db().groups.find_one({"groupId": int(chat.id)})
        cap = ((latest or {}).get("activeDrop") or {}).get("captcha") or {}
        if cap.get("status") == "pending":
            await update.message.reply_text("⏳ Captcha is already active for this drop. Wait for the result.")
        else:
            await update.message.reply_text("❌ This character is no longer available.")
        return

    rarity = str(active.get("rarity", ""))
    text = (
        f"🧩 <b>CAPTCHA SOLVE REQUIRED</b>\n\n"
        f"{get_rarity_emoji(rarity)} <b>{escape_html(rarity)}</b> and above must pass captcha.\n"
        f"👤 Player: {mention_user(user)}\n"
        f"🎴 Card: <b>{escape_html(active.get('name'))}</b> [{escape_html(active.get('cardId'))}]\n\n"
        f"Solve within <b>{CLAIM_CAPTCHA_SECONDS}s</b> or this card will be lost.\n"
        f"Question: <b>{escape_html(captcha['question'])}</b>"
    )
    sent = await update.message.reply_html(
        text,
        reply_markup=captcha_keyboard(int(chat.id), int(user.id), captcha["nonce"], captcha["options"]),
    )
    await get_db().groups.update_one(
        {"groupId": int(chat.id), "activeDrop.captcha.nonce": captcha["nonce"]},
        {"$set": {"activeDrop.captcha.messageId": int(sent.message_id), "updatedAt": utcnow()}},
    )
    context.application.create_task(captcha_timeout_task(context.bot, int(chat.id), captcha["nonce"], CLAIM_CAPTCHA_SECONDS))


def build_drop_message_link(chat, message_id: int) -> str:
    """Build a Telegram link to the last spawned card message."""
    if not chat or not message_id:
        return ""

    username = getattr(chat, "username", "") or ""
    if username:
        return f"https://t.me/{username}/{message_id}"

    chat_id_str = str(getattr(chat, "id", ""))
    # Private supergroup/channel style: -1001234567890 -> https://t.me/c/1234567890/45
    if chat_id_str.startswith("-100"):
        return f"https://t.me/c/{chat_id_str[4:]}/{message_id}"

    return ""


def caught_by_html(active: dict) -> str:
    """Return clickable catcher mention from activeDrop data."""
    user_id = int(active.get("claimedByUserId", 0) or 0)
    name = escape_html(active.get("claimedByName") or "Someone")
    if user_id:
        return f'<a href="tg://user?id={user_id}">{name}</a>'
    return name


async def reply_already_caught(update: Update, active: dict) -> None:
    await update.message.reply_html(
        "❌ <b>CHARACTER ALREADY CAUGHT</b>\n\n"
        f"Caught by: {caught_by_html(active)}\n\n"
        "🥤 Wait for new character to spawn."
    )


async def reply_wrong_character_name(update: Update, active: dict, guess_raw: str = "") -> None:
    drop_message_id = int(active.get("messageId", 0) or 0)
    drop_link = build_drop_message_link(update.effective_chat, drop_message_id)

    if str(guess_raw or "").strip():
        first_line = f"❌ CHARACTER NAME {escape_html(str(guess_raw).lower())} IS INCORRECT"
    else:
        first_line = "❌ CHARACTER NAME IS INCORRECT"

    if drop_link:
        text = (
            f"{first_line}\n\n"
            f'<a href="{drop_link}">⬆️</a> CHARACTER is still available.'
        )
    else:
        text = (
            f"{first_line}\n\n"
            "⬆️ CHARACTER is still available."
        )

    await update.message.reply_html(text)


async def bika_claim_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    if await should_ignore_update(update):
        return

    guess_raw = " ".join(context.args).strip()

    await ensure_user(update.effective_user)
    group = await ensure_group(update.effective_chat)
    active = (group or {}).get("activeDrop")
    if not active or not active.get("cardId"):
        await update.message.reply_text("❌ No character is available right now.")
        return

    # /bika without a name should not show usage text.
    # If the card is already caught, show catcher. If not caught, show wrong-name + last drop link.
    if active.get("isClaimed"):
        await reply_already_caught(update, active)
        return

    captcha = active.get("captcha") or {}
    if captcha.get("status") == "pending":
        await update.message.reply_text("⏳ Captcha is already active for this high-rarity drop.")
        return

    if not guess_raw:
        await reply_wrong_character_name(update, active, guess_raw)
        return

    target = str(active.get("normalizedName") or active.get("name") or "")
    is_match = is_character_name_match(
        guess_text=guess_raw,
        target_name=target,
        min_length=CLAIM_PREFIX_MIN_LENGTH,
    )
    if not is_match:
        await reply_wrong_character_name(update, active, guess_raw)
        return

    # High-rarity captcha is solved before the card spawns, so /bika claims no longer trigger captcha here.

    reservation = await reserve_daily_claim(update.effective_user.id)
    if not reservation.get("ok"):
        await update.message.reply_text(
            f"❌ Daily catch limit reached.\n"
            f"Myanmar/Yangon date: {reservation.get('date')}\n"
            f"Used: {reservation.get('used')}/{CLAIM_DAILY_LIMIT}\n"
            f"Remaining: 0"
        )
        return

    claimer_name = " ".join([update.effective_user.first_name or "", update.effective_user.last_name or ""]).strip()
    updated = await get_db().groups.find_one_and_update(
        {
            "groupId": int(update.effective_chat.id),
            "activeDrop.cardId": str(active.get("cardId")),
            "activeDrop.isClaimed": False,
        },
        {
            "$set": {
                "activeDrop.isClaimed": True,
                "activeDrop.claimedByUserId": int(update.effective_user.id),
                "activeDrop.claimedByName": claimer_name or update.effective_user.username or str(update.effective_user.id),
                "updatedAt": utcnow(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    if not updated or int(updated.get("activeDrop", {}).get("claimedByUserId", 0)) != int(update.effective_user.id):
        await release_daily_claim(update.effective_user.id, reservation.get("date"))
        latest = await get_db().groups.find_one({"groupId": int(update.effective_chat.id)})
        latest_active = (latest or {}).get("activeDrop") or {}
        await reply_already_caught(update, latest_active)
        return

    photo_doc = await get_photo_by_card_id(active.get("cardId"))
    if not photo_doc:
        await release_daily_claim(update.effective_user.id, reservation.get("date"))
        await update.message.reply_text("❌ Drop data missing.")
        return

    await add_card_to_user(update.effective_user, photo_doc, 1)
    await log_claim_event(update.effective_user, update.effective_chat, photo_doc, reservation.get("date"))
    await update.message.reply_html(
        "🎉 <b>YOU GOT A NEW CHARACTER!</b>\n\n"
        f"👤 Claimed by: {mention_user(update.effective_user)}\n"
        f"{get_rarity_emoji(photo_doc.get('rarity'))} Name: <b>{escape_html(photo_doc.get('name'))}</b>\n"
        f"🆔 ID: <b>{escape_html(photo_doc.get('cardId'))}</b>\n"
        f"🏷 RARITY: <b>{escape_html(photo_doc.get('rarity'))}</b>\n"
        f"🌴 ANIME: <b>{escape_html(photo_doc.get('anime'))}</b>\n\n"
        "❄️ CHECK YOUR /harem !"
    )


async def captcha_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    parts = query.data.split(":")
    if len(parts) != 5:
        await query.answer("Invalid captcha.", show_alert=True)
        return
    _, chat_id_raw, user_id_raw, nonce, choice_raw = parts
    chat_id = int(chat_id_raw)
    owner_user_id = int(user_id_raw)
    choice_index = int(choice_raw)

    if int(query.from_user.id) != owner_user_id:
        await query.answer("This captcha is not for you.", show_alert=True)
        return

    latest = await get_db().groups.find_one({"groupId": int(chat_id), "activeDrop.captcha.nonce": str(nonce)})
    active = (latest or {}).get("activeDrop") or {}
    captcha = active.get("captcha") or {}
    if not active or captcha.get("status") != "pending":
        await query.answer("Captcha already finished.", show_alert=True)
        return

    expires_at = captcha.get("expiresAt")
    if is_expired_datetime(expires_at):
        await mark_captcha_lost(chat_id, nonce, "Captcha Timeout")
        try:
            await query.edit_message_text(
                "⌛ <b>CAPTCHA TIMEOUT</b>\n\n120 seconds finished. This high-rarity card has been lost.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        await query.answer("Expired.", show_alert=True)
        return

    correct_index = int(captcha.get("answerIndex", -1))
    if choice_index != correct_index:
        await mark_captcha_lost(chat_id, nonce, "Captcha Failed")
        try:
            await query.edit_message_text(
                "❌ <b>WRONG CAPTCHA</b>\n\nThis high-rarity card has been lost.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        await query.answer("Wrong. Card lost.", show_alert=True)
        return

    reservation = await reserve_daily_claim(query.from_user.id)
    if not reservation.get("ok"):
        await mark_captcha_lost(chat_id, nonce, "Daily Limit Reached")
        try:
            await query.edit_message_text(
                f"❌ <b>DAILY LIMIT REACHED</b>\n\nUsed: {reservation.get('used')}/{CLAIM_DAILY_LIMIT}. This card has been lost.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        await query.answer("Daily limit reached.", show_alert=True)
        return

    claimer_name = " ".join([query.from_user.first_name or "", query.from_user.last_name or ""]).strip()
    updated = await get_db().groups.find_one_and_update(
        {
            "groupId": int(chat_id),
            "activeDrop.cardId": str(active.get("cardId")),
            "activeDrop.captcha.nonce": str(nonce),
            "activeDrop.captcha.status": "pending",
            "activeDrop.captcha.userId": int(query.from_user.id),
            "activeDrop.isClaimed": False,
        },
        {
            "$set": {
                "activeDrop.isClaimed": True,
                "activeDrop.claimedByUserId": int(query.from_user.id),
                "activeDrop.claimedByName": claimer_name or query.from_user.username or str(query.from_user.id),
                "activeDrop.captcha.status": "solved",
                "activeDrop.captcha.finishedAt": utcnow(),
                "updatedAt": utcnow(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        await release_daily_claim(query.from_user.id, reservation.get("date"))
        await query.answer("This card is no longer available.", show_alert=True)
        return

    photo_doc = await get_photo_by_card_id(active.get("cardId"))
    if not photo_doc:
        await release_daily_claim(query.from_user.id, reservation.get("date"))
        await query.answer("Drop data missing.", show_alert=True)
        return

    await add_card_to_user(query.from_user, photo_doc, 1)
    await log_claim_event(query.from_user, query.message.chat, photo_doc, reservation.get("date"))

    try:
        await query.edit_message_text(
            f"✅ <b>CAPTCHA SOLVED</b>\n\n{mention_user(query.from_user)} got the card.",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass
    await query.answer("Captcha solved.")
    await query.message.reply_html(
        "🎉 <b>YOU GOT A NEW CHARACTER!</b>\n\n"
        f"👤 Claimed by: {mention_user(query.from_user)}\n"
        f"{get_rarity_emoji(photo_doc.get('rarity'))} Name: <b>{escape_html(photo_doc.get('name'))}</b>\n"
        f"🆔 ID: <b>{escape_html(photo_doc.get('cardId'))}</b>\n"
        f"🏷 RARITY: <b>{escape_html(photo_doc.get('rarity'))}</b>\n"
        f"🌴 ANIME: <b>{escape_html(photo_doc.get('anime'))}</b>\n\n"
        "❄️ CHECK YOUR /harem !"
    )


def register_claim_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("bika", bika_claim_cmd))
    app.add_handler(CallbackQueryHandler(captcha_callback, pattern=r"^cap:-?\d+:\d+:[0-9a-f]+:\d+$"))
