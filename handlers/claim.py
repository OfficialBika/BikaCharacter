from __future__ import annotations

import asyncio
import random
import re
import secrets
from datetime import datetime, timezone, timedelta

from pymongo import ReturnDocument
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from config import BOT_USERNAME, CLAIM_CAPTCHA_SECONDS, CLAIM_COMMAND, CLAIM_DAILY_LIMIT, CLAIM_PREFIX_MIN_LENGTH
from database.mongodb import get_db
from utils.cooldown import should_ignore_update
from utils.claim_stats import get_daily_claim_count, log_claim_event, release_daily_claim, reserve_daily_claim, yangon_date_key
from utils.db_helpers import add_card_to_user, ensure_user, get_photo_by_card_id
from utils.parser import is_character_name_match, normalized_search_name
from utils.rarity import get_rarity_emoji
from utils.text import escape_html, mention_user, utcnow
from utils.i18n import t

# Divine and every rarity above it must be protected with captcha.
CAPTCHA_RARITIES = set()  # High-rarity captcha now happens before spawn in handlers/drop.py


# Claim command is controlled by .env. Empty/invalid values fall back to "bika"
# inside config.py.
CLAIM_COMMAND_REGEX = re.compile(
    rf"^/{re.escape(CLAIM_COMMAND)}(?:@([A-Za-z0-9_]{{5,32}}))?(?:\s+(.+))?$",
    flags=re.IGNORECASE,
)
CLAIM_COMMAND_TRIGGER_REGEX = re.compile(
    rf"^/{re.escape(CLAIM_COMMAND)}(?:@[A-Za-z0-9_]{{5,32}})?(?:\s|$)",
    flags=re.IGNORECASE,
)

# Extremely small in-process race coordinator.
#
# Why it exists:
# - after one correct guess is validated, the first local contender is reserved here;
# - that user gets ⚡ immediately;
# - later contenders get the already-caught response without waiting for the DB write
#   pipeline to finish;
# - MongoDB's atomic claimLock remains the source of truth, so database safety is
#   preserved and multi-process conflicts are corrected by the background worker.
_FAST_CLAIM_RESERVATIONS: dict[int, dict] = {}
_FAST_CLAIM_MUTEX = asyncio.Lock()
_FAST_CLAIM_TTL_SECONDS = 60.0


def detect_card_media_type(card_doc: dict) -> str:
    """Return Telegram media type for a stored card document.

    This keeps old cards working too: if mediaType is missing, infer from mimeType/fileName.
    """
    media_type = str(card_doc.get("mediaType") or "").strip().lower()
    if media_type in {"photo", "video", "animation", "document"}:
        return media_type
    if media_type == "gif":
        return "animation"

    mime_type = str(card_doc.get("mimeType") or "").strip().lower()
    file_name = str(card_doc.get("fileName") or "").strip().lower()

    if mime_type.startswith("video/") or file_name.endswith((".mp4", ".mov", ".mkv", ".webm")):
        return "video"
    if mime_type == "image/gif" or file_name.endswith(".gif"):
        return "animation"
    if mime_type and not mime_type.startswith("image/"):
        return "document"
    return "photo"


async def ensure_claimed_card_media_fields(user_id: int, card_doc: dict) -> None:
    """Backfill media fields into user.cards after a claim.

    Older db_helpers.py versions only saved fileId. This makes newly claimed video/GIF/document
    cards usable in /harem, /fav, /profile and inline even if the helper was not updated yet.
    """
    card_id = str(card_doc.get("cardId", ""))
    if not card_id:
        return

    set_fields = {
        "cards.$.mediaType": detect_card_media_type(card_doc),
    }
    for src_key, dst_key in (
        ("mimeType", "cards.$.mimeType"),
        ("fileName", "cards.$.fileName"),
        ("fileUniqueId", "cards.$.fileUniqueId"),
        ("fileId", "cards.$.fileId"),
    ):
        value = str(card_doc.get(src_key) or "")
        if value:
            set_fields[dst_key] = value

    await get_db().users.update_one(
        {"userId": int(user_id), "cards.cardId": card_id},
        {"$set": set_fields},
    )


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
    text = t("claim_captcha_timeout")
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
    text = t(
        "claim_success",
        claimer=mention_user(tg_user),
        emoji=get_rarity_emoji(photo_doc.get("rarity")),
        name=escape_html(photo_doc.get("name")),
        card_id=escape_html(photo_doc.get("cardId")),
        rarity=escape_html(photo_doc.get("rarity")),
        anime=escape_html(photo_doc.get("anime")),
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
            t("daily_limit", date=date_key, used=used, limit=CLAIM_DAILY_LIMIT, remaining=0)
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
            await update.message.reply_text(t("claim_captcha_active"))
        else:
            await update.message.reply_text(t("character_unavailable"))
        return

    rarity = str(active.get("rarity", ""))
    text = t(
        "claim_captcha_required",
        emoji=get_rarity_emoji(rarity),
        rarity=escape_html(rarity),
        player=mention_user(user),
        card_name=escape_html(active.get("name")),
        card_id=escape_html(active.get("cardId")),
        seconds=CLAIM_CAPTCHA_SECONDS,
        question=escape_html(captcha["question"]),
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
    """Return clickable catcher mention from activeDrop data.

    During a fast race, the first correct user gets a temporary claimLock before
    the final result is written. Other users should still see that first user's
    name instead of getting their own progress emoji or daily-limit reservation.
    """
    user_id = int(active.get("claimedByUserId", 0) or 0)
    name = escape_html(active.get("claimedByName") or "")

    if not user_id:
        lock = active.get("claimLock") or {}
        user_id = int(lock.get("userId", 0) or 0)
        name = escape_html(lock.get("userName") or name or "Someone")

    if not name:
        name = "Someone"
    if user_id:
        return f'<a href="tg://user?id={user_id}">{name}</a>'
    return name


async def reply_already_caught(update: Update, active: dict) -> None:
    await update.message.reply_html(t("already_caught", caught_by=caught_by_html(active)))


def extract_bika_guess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Extract the guess for the configured claim command.

    Examples when CLAIM_COMMAND=dao:
      /dao name
      /dao@YourBotUsername name

    When CLAIM_COMMAND is absent from .env, the command is /bika.
    """
    msg = update.effective_message
    text = (msg.text or "").strip() if msg else ""

    match = CLAIM_COMMAND_REGEX.match(text)
    if not match:
        return None

    mentioned_bot = (match.group(1) or "").strip().lower()
    guess = (match.group(2) or "").strip()

    allowed_bot_names = {
        str(BOT_USERNAME or "").replace("@", "").strip().lower(),
    }
    allowed_bot_names.discard("")

    # If BOT_USERNAME is configured, ignore commands explicitly addressed to a
    # different bot. If BOT_USERNAME is blank, Telegram normally routes the
    # update correctly and we accept the command.
    if mentioned_bot and allowed_bot_names and mentioned_bot not in allowed_bot_names:
        return None

    return guess


def _claim_user_name(user) -> str:
    return (
        " ".join([user.first_name or "", user.last_name or ""]).strip()
        or user.username
        or str(user.id)
    )


async def get_fast_claim_reservation(chat_id: int) -> dict | None:
    """Return a live local reservation without touching MongoDB."""
    now = asyncio.get_running_loop().time()
    async with _FAST_CLAIM_MUTEX:
        state = _FAST_CLAIM_RESERVATIONS.get(int(chat_id))
        if not state:
            return None
        if float(state.get("expiresAtMonotonic", 0.0) or 0.0) <= now:
            _FAST_CLAIM_RESERVATIONS.pop(int(chat_id), None)
            return None
        return dict(state)


async def reserve_fast_claim(chat_id: int, card_id: str, user) -> tuple[dict | None, dict | None]:
    """Atomically reserve the first local correct contender.

    Returns:
      (new_reservation, None) for the first contender
      (None, existing_reservation) for later contenders
    """
    now = asyncio.get_running_loop().time()
    chat_id = int(chat_id)

    async with _FAST_CLAIM_MUTEX:
        existing = _FAST_CLAIM_RESERVATIONS.get(chat_id)
        if existing and float(existing.get("expiresAtMonotonic", 0.0) or 0.0) > now:
            return None, dict(existing)

        token = secrets.token_hex(8)
        state = {
            "chatId": chat_id,
            "cardId": str(card_id),
            "token": token,
            "userId": int(user.id),
            "userName": _claim_user_name(user),
            "expiresAtMonotonic": now + _FAST_CLAIM_TTL_SECONDS,
        }
        _FAST_CLAIM_RESERVATIONS[chat_id] = state
        return dict(state), None


async def clear_fast_claim_reservation(chat_id: int, token: str) -> None:
    """Clear only the reservation created by this background worker."""
    chat_id = int(chat_id)
    async with _FAST_CLAIM_MUTEX:
        current = _FAST_CLAIM_RESERVATIONS.get(chat_id)
        if current and str(current.get("token", "")) == str(token):
            _FAST_CLAIM_RESERVATIONS.pop(chat_id, None)


async def reply_fast_reservation_caught(update: Update, state: dict) -> None:
    """Fast loser path: no DB read/write is required."""
    pseudo_active = {
        "claimLock": {
            "userId": int(state.get("userId", 0) or 0),
            "userName": str(state.get("userName", "") or "Someone"),
        }
    }
    await reply_already_caught(update, pseudo_active)


async def reply_wrong_character_name(update: Update, active: dict, guess_raw: str = "") -> None:
    drop_message_id = int(active.get("messageId", 0) or 0)
    drop_link = build_drop_message_link(update.effective_chat, drop_message_id)

    arrow = (
        f'<a href="{drop_link}">🔺ᴄʜᴀʀᴀᴄᴛᴇʀ</a>'
        if drop_link
        else "🔺ᴄʜᴀʀᴀᴄᴛᴇʀ"
    )

    if str(guess_raw or "").strip():
        text = t("wrong_name", guess=escape_html(str(guess_raw).lower()), arrow=arrow)
    else:
        text = t("wrong_name_empty", arrow=arrow)

    # Backward compatibility:
    # Some lang.py versions use "{arrow} ᴄʜᴀʀᴀᴄᴛᴇʀ ...".
    # Since arrow now already includes "🔺ᴄʜᴀʀᴀᴄᴛᴇʀ", remove the duplicated word if present.
    text = text.replace(f"{arrow} ᴄʜᴀʀᴀᴄᴛᴇʀ", arrow, 1)

    await update.message.reply_html(
        text,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


async def edit_claim_progress_message(progress_message, text: str, parse_mode: str | None = None) -> bool:
    """Edit the single claim progress message instead of sending extra claim replies."""
    if not progress_message:
        return False
    try:
        await progress_message.edit_text(text, parse_mode=parse_mode)
        return True
    except Exception:
        return False


async def release_claim_lock(chat_id: int, card_id: str, claim_token: str) -> None:
    """Release only this user's temporary claim lock."""
    try:
        await get_db().groups.update_one(
            {
                "groupId": int(chat_id),
                "activeDrop.cardId": str(card_id),
                "activeDrop.isClaimed": False,
                "activeDrop.claimLock.token": str(claim_token),
            },
            {
                "$unset": {"activeDrop.claimLock": ""},
                "$set": {"updatedAt": utcnow()},
            },
        )
    except Exception:
        pass


async def lock_claim_for_first_user(update: Update, active: dict, claimer_name: str, claim_token: str) -> dict | None:
    """Atomically lock the drop for the first correct user only.

    This is the key race-control step:
    - The first correct user gets activeDrop.claimLock.
    - Only that locked user receives the ⚡ -> ⏳ progress message.
    - Other correct users immediately get the normal already-caught response.
    - Daily limit is reserved only after this lock is acquired.
    """
    now = utcnow()
    return await get_db().groups.find_one_and_update(
        {
            "groupId": int(update.effective_chat.id),
            "activeDrop.cardId": str(active.get("cardId")),
            "activeDrop.isClaimed": False,
            "$or": [
                {"activeDrop.claimLock": {"$exists": False}},
                {"activeDrop.claimLock": None},
                {"activeDrop.claimLock.expiresAt": {"$lte": now}},
            ],
        },
        {
            "$set": {
                "activeDrop.claimLock": {
                    "token": str(claim_token),
                    "userId": int(update.effective_user.id),
                    "userName": claimer_name or update.effective_user.username or str(update.effective_user.id),
                    "createdAt": now,
                    "expiresAt": now + timedelta(seconds=60),
                },
                "updatedAt": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )


async def rollback_finalized_claim(
    chat_id: int,
    card_id: str,
    claim_token: str,
    user_id: int,
) -> bool:
    """Undo only this exact winner's finalized DB claim after a confirmed grant failure."""
    result = await get_db().groups.update_one(
        {
            "groupId": int(chat_id),
            "activeDrop.cardId": str(card_id),
            "activeDrop.isClaimed": True,
            "activeDrop.claimedByUserId": int(user_id),
            "activeDrop.claimLock.token": str(claim_token),
        },
        {
            "$set": {
                "activeDrop.isClaimed": False,
                "activeDrop.claimedByUserId": 0,
                "activeDrop.claimedByName": "",
                "updatedAt": utcnow(),
            },
            "$unset": {"activeDrop.claimLock": ""},
        },
    )
    return bool(getattr(result, "modified_count", 0))


async def finish_claim_progress(
    update: Update,
    progress_message,
    text: str,
    parse_mode: str | None = None,
) -> None:
    if await edit_claim_progress_message(progress_message, text, parse_mode):
        return
    try:
        if parse_mode == ParseMode.HTML:
            await update.message.reply_html(text)
        else:
            await update.message.reply_text(text)
    except Exception:
        pass


async def process_claim_in_background(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    active: dict,
    fast_state: dict,
    progress_message,
) -> None:
    """Run the DB-heavy claim pipeline after the immediate ⚡ response.

    Order:
      1) Mongo atomic lock
      2) ⏳ progress edit
      3) ensure user + reserve daily limit
      4) load card + atomically finalize winner
      5) grant card
      6) best-effort media backfill + claim log
      7) final success edit

    The local fast reservation makes later contenders return immediately while this
    worker runs. MongoDB's claimLock still arbitrates the authoritative winner.
    """
    user = update.effective_user
    chat = update.effective_chat
    chat_id = int(chat.id)
    card_id = str(active.get("cardId", ""))
    claim_token = str(fast_state.get("token", ""))
    claimer_name = str(fast_state.get("userName") or _claim_user_name(user))

    daily_reserved = False
    reservation_date = None
    claim_finalized = False
    card_granted = False
    photo_doc = None
    before_card_count = 0

    try:
        # First authoritative DB operation happens only after ⚡ has already been sent.
        locked = await lock_claim_for_first_user(
            update,
            active,
            claimer_name,
            claim_token,
        )
        if not locked:
            latest = await get_db().groups.find_one(
                {"groupId": chat_id},
                {"activeDrop": 1},
            )
            latest_active = (latest or {}).get("activeDrop") or {}
            text = t("already_caught", caught_by=caught_by_html(latest_active))
            await finish_claim_progress(update, progress_message, text, ParseMode.HTML)
            return

        # Preserve the existing single-message flow: ⚡ -> ⏳ -> final result.
        await edit_claim_progress_message(progress_message, "⏳")

        # Everything below is intentionally after the immediate emoji response.
        user_doc = await ensure_user(user) or {}
        existing_card = next(
            (
                c
                for c in user_doc.get("cards", [])
                if str(c.get("cardId")) == card_id
            ),
            None,
        )
        before_card_count = int((existing_card or {}).get("count", 0) or 0)

        date_key = yangon_date_key()
        reservation = await reserve_daily_claim(user.id, date_key)
        if not reservation.get("ok"):
            await release_claim_lock(chat_id, card_id, claim_token)
            text = t(
                "daily_limit",
                date=reservation.get("date"),
                used=reservation.get("used"),
                limit=CLAIM_DAILY_LIMIT,
                remaining=0,
            )
            await finish_claim_progress(update, progress_message, text)
            return

        daily_reserved = True
        reservation_date = reservation.get("date")

        photo_doc = await get_photo_by_card_id(card_id)
        if not photo_doc:
            await release_daily_claim(user.id, reservation_date)
            daily_reserved = False
            await release_claim_lock(chat_id, card_id, claim_token)
            await finish_claim_progress(update, progress_message, t("drop_data_missing"))
            return

        # Finalize only the holder of this exact Mongo claim token.
        # Keep claimLock until the inventory grant succeeds, so a confirmed grant
        # failure can be rolled back safely.
        updated = await get_db().groups.find_one_and_update(
            {
                "groupId": chat_id,
                "activeDrop.cardId": card_id,
                "activeDrop.isClaimed": False,
                "activeDrop.claimLock.token": claim_token,
                "activeDrop.claimLock.userId": int(user.id),
            },
            {
                "$set": {
                    "activeDrop.isClaimed": True,
                    "activeDrop.claimedByUserId": int(user.id),
                    "activeDrop.claimedByName": claimer_name,
                    "updatedAt": utcnow(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )

        if not updated or int(
            (updated.get("activeDrop") or {}).get("claimedByUserId", 0) or 0
        ) != int(user.id):
            await release_daily_claim(user.id, reservation_date)
            daily_reserved = False
            latest = await get_db().groups.find_one(
                {"groupId": chat_id},
                {"activeDrop": 1},
            )
            latest_active = (latest or {}).get("activeDrop") or {}
            text = t("already_caught", caught_by=caught_by_html(latest_active))
            await finish_claim_progress(update, progress_message, text, ParseMode.HTML)
            return

        claim_finalized = True

        # Critical inventory grant.
        try:
            await add_card_to_user(user, photo_doc, 1)
            card_granted = True
        except Exception as grant_exc:
            # The helper updates the user and then reads it back. If an exception
            # happens after the DB update, verify quantity before deciding to roll
            # back; this avoids accidental duplicate grants on retry.
            print("CLAIM CARD GRANT ERROR:", repr(grant_exc), flush=True)
            latest_user = await get_db().users.find_one(
                {"userId": int(user.id)},
                {"cards": 1},
            )
            latest_card = next(
                (
                    c
                    for c in (latest_user or {}).get("cards", [])
                    if str(c.get("cardId")) == card_id
                ),
                None,
            )
            after_count = int((latest_card or {}).get("count", 0) or 0)
            card_granted = after_count > before_card_count

            if not card_granted:
                await rollback_finalized_claim(
                    chat_id,
                    card_id,
                    claim_token,
                    user.id,
                )
                claim_finalized = False
                if daily_reserved:
                    await release_daily_claim(user.id, reservation_date)
                    daily_reserved = False
                await finish_claim_progress(
                    update,
                    progress_message,
                    "⚠️ Claim processing failed. The card is available again.",
                )
                return

        # Once the card is safely in the harem, clear the temporary DB lock.
        await get_db().groups.update_one(
            {
                "groupId": chat_id,
                "activeDrop.cardId": card_id,
                "activeDrop.isClaimed": True,
                "activeDrop.claimedByUserId": int(user.id),
                "activeDrop.claimLock.token": claim_token,
            },
            {
                "$unset": {"activeDrop.claimLock": ""},
                "$set": {"updatedAt": utcnow()},
            },
        )

        # Non-critical follow-up writes must never undo an already granted card.
        try:
            await ensure_claimed_card_media_fields(user.id, photo_doc)
        except Exception as exc:
            print("CLAIM MEDIA BACKFILL ERROR:", repr(exc), flush=True)

        try:
            await log_claim_event(user, chat, photo_doc, reservation_date)
        except Exception as exc:
            print("CLAIM LOG ERROR:", repr(exc), flush=True)

        text = t(
            "claim_success",
            claimer=mention_user(user),
            emoji=get_rarity_emoji(photo_doc.get("rarity")),
            name=escape_html(photo_doc.get("name")),
            card_id=escape_html(photo_doc.get("cardId")),
            rarity=escape_html(photo_doc.get("rarity")),
            anime=escape_html(photo_doc.get("anime")),
        )
        await finish_claim_progress(
            update,
            progress_message,
            text,
            ParseMode.HTML,
        )

    except Exception as exc:
        print("CLAIM BACKGROUND ERROR:", repr(exc), flush=True)

        # Compensate only before a card has been confirmed in the user's harem.
        if claim_finalized and not card_granted:
            try:
                await rollback_finalized_claim(
                    chat_id,
                    card_id,
                    claim_token,
                    user.id,
                )
                claim_finalized = False
            except Exception as rollback_exc:
                print("CLAIM ROLLBACK ERROR:", repr(rollback_exc), flush=True)
        elif not claim_finalized:
            await release_claim_lock(chat_id, card_id, claim_token)

        if daily_reserved and not card_granted:
            try:
                await release_daily_claim(user.id, reservation_date)
                daily_reserved = False
            except Exception as release_exc:
                print("CLAIM DAILY RELEASE ERROR:", repr(release_exc), flush=True)

        if card_granted and photo_doc:
            # The card is already safe; present success even if a non-critical
            # follow-up failed unexpectedly.
            text = t(
                "claim_success",
                claimer=mention_user(user),
                emoji=get_rarity_emoji(photo_doc.get("rarity")),
                name=escape_html(photo_doc.get("name")),
                card_id=escape_html(photo_doc.get("cardId")),
                rarity=escape_html(photo_doc.get("rarity")),
                anime=escape_html(photo_doc.get("anime")),
            )
            await finish_claim_progress(update, progress_message, text, ParseMode.HTML)
        else:
            await finish_claim_progress(
                update,
                progress_message,
                "⚠️ Claim processing failed. Please try again.",
            )

    finally:
        await clear_fast_claim_reservation(chat_id, claim_token)


async def bika_claim_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or update.effective_chat.type not in ("group", "supergroup"):
        return

    guess_raw = extract_bika_guess(update, context)
    if guess_raw is None:
        return

    chat_id = int(update.effective_chat.id)

    # Fast loser path: once the first correct contender is locally reserved,
    # later contenders get the already-caught response immediately with no DB wait.
    fast_existing = await get_fast_claim_reservation(chat_id)
    if fast_existing:
        await reply_fast_reservation_caught(update, fast_existing)
        return

    # Before ⚡, correctness still requires the current activeDrop and the existing
    # mute rule still has to be preserved. Run both checks concurrently to minimize
    # pre-emoji latency. All claim writes and the heavy claim pipeline remain in
    # the background after ⚡.
    ignored, group = await asyncio.gather(
        should_ignore_update(update),
        get_db().groups.find_one(
            {"groupId": chat_id},
            {"activeDrop": 1},
        ),
    )
    if ignored:
        return
    active = (group or {}).get("activeDrop") or {}

    if not active or not active.get("cardId"):
        await update.message.reply_text(t("no_character_available"))
        return

    if active.get("isClaimed"):
        await reply_already_caught(update, active)
        return

    db_claim_lock = active.get("claimLock") or {}
    if db_claim_lock and not is_expired_datetime(db_claim_lock.get("expiresAt")):
        await reply_already_caught(update, active)
        return

    captcha = active.get("captcha") or {}
    if captcha.get("status") == "pending":
        await update.message.reply_text(t("claim_high_captcha_active"))
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

    # The first correct contender is selected in memory before any DB write.
    fast_state, existing = await reserve_fast_claim(
        chat_id,
        str(active.get("cardId")),
        update.effective_user,
    )
    if not fast_state:
        await reply_fast_reservation_caught(update, existing or {})
        return

    # Immediate first-user response.
    progress_message = None
    try:
        progress_message = await update.message.reply_text("⚡")
    except Exception as exc:
        print("CLAIM FAST EMOJI SEND ERROR:", repr(exc), flush=True)

    # DB-heavy work continues in the background. This handler returns immediately,
    # so other claim updates can be processed and answered fast.
    context.application.create_task(
        process_claim_in_background(
            update,
            context,
            active,
            fast_state,
            progress_message,
        )
    )


async def captcha_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    parts = query.data.split(":")
    if len(parts) != 5:
        await query.answer(t("captcha_invalid"), show_alert=True)
        return
    _, chat_id_raw, user_id_raw, nonce, choice_raw = parts
    chat_id = int(chat_id_raw)
    owner_user_id = int(user_id_raw)
    choice_index = int(choice_raw)

    if int(query.from_user.id) != owner_user_id:
        await query.answer(t("captcha_not_for_you"), show_alert=True)
        return

    latest = await get_db().groups.find_one({"groupId": int(chat_id), "activeDrop.captcha.nonce": str(nonce)})
    active = (latest or {}).get("activeDrop") or {}
    captcha = active.get("captcha") or {}
    if not active or captcha.get("status") != "pending":
        await query.answer(t("captcha_finished"), show_alert=True)
        return

    expires_at = captcha.get("expiresAt")
    if is_expired_datetime(expires_at):
        await mark_captcha_lost(chat_id, nonce, "Captcha Timeout")
        try:
            await query.edit_message_text(
                t("claim_captcha_timeout"),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        await query.answer(t("captcha_expired"), show_alert=True)
        return

    correct_index = int(captcha.get("answerIndex", -1))
    if choice_index != correct_index:
        await mark_captcha_lost(chat_id, nonce, "Captcha Failed")
        try:
            await query.edit_message_text(
                t("claim_wrong_captcha"),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        await query.answer(t("claim_wrong_card_lost"), show_alert=True)
        return

    reservation = await reserve_daily_claim(query.from_user.id)
    if not reservation.get("ok"):
        await mark_captcha_lost(chat_id, nonce, "Daily Limit Reached")
        try:
            await query.edit_message_text(
                t("daily_limit_card_lost", used=reservation.get("used"), limit=CLAIM_DAILY_LIMIT),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        await query.answer(t("daily_limit_reached_alert"), show_alert=True)
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
        await query.answer(t("card_no_longer_available"), show_alert=True)
        return

    photo_doc = await get_photo_by_card_id(active.get("cardId"))
    if not photo_doc:
        await release_daily_claim(query.from_user.id, reservation.get("date"))
        await query.answer(t("drop_data_missing"), show_alert=True)
        return

    await add_card_to_user(query.from_user, photo_doc, 1)
    await ensure_claimed_card_media_fields(query.from_user.id, photo_doc)
    await log_claim_event(query.from_user, query.message.chat, photo_doc, reservation.get("date"))

    try:
        await query.edit_message_text(
            t("claim_captcha_solved", user=mention_user(query.from_user)),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass
    await query.answer(t("captcha_solved_alert"))
    await query.message.reply_html(
        t(
            "claim_success",
            claimer=mention_user(query.from_user),
            emoji=get_rarity_emoji(photo_doc.get("rarity")),
            name=escape_html(photo_doc.get("name")),
            card_id=escape_html(photo_doc.get("cardId")),
            rarity=escape_html(photo_doc.get("rarity")),
            anime=escape_html(photo_doc.get("anime")),
        )
    )


def register_claim_handlers(app: Application) -> None:
    app.add_handler(
        MessageHandler(
            filters.Regex(CLAIM_COMMAND_TRIGGER_REGEX),
            bika_claim_cmd,
        )
    )
    app.add_handler(CallbackQueryHandler(captcha_callback, pattern=r"^cap:-?\d+:\d+:[0-9a-f]+:\d+$"))
