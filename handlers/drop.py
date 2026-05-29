from __future__ import annotations

import asyncio
import io
import random
import secrets
from datetime import timedelta
from pathlib import Path

from pymongo import ReturnDocument
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from config import BOT_MUTE_SECONDS, CLAIM_CAPTCHA_SECONDS, DEFAULT_CHANGETIME
from database.mongodb import get_db
from utils.cooldown import is_bot_muted, record_message_and_maybe_mute
from utils.db_helpers import ensure_group, ensure_user, get_drop_photo_for_rarity, get_photo_by_card_id
from utils.rarity import get_rarity_emoji, get_scheduled_drop_rarity
from utils.permissions import is_owner
from utils.text import escape_html, safe_chat_title, utcnow
from utils.i18n import t

BASE_DIR = Path(__file__).resolve().parent.parent
CAPTCHA_FONT_PATH = BASE_DIR / "assets" / "fonts" / "DejaVuSans.ttf"
# For these rarity milestones, captcha appears BEFORE the card/photo spawns.
# If solved correctly within CLAIM_CAPTCHA_SECONDS, then the card spawns normally.
# If a wrong button is pressed or timeout happens, that scheduled drop is lost.
PRE_SPAWN_CAPTCHA_RARITIES = {"Divine", "CrossVerse", "Cataphract", "Supreme"}


def is_countable_message(update: Update) -> bool:
    """Count every real group message as activity.

    Counted:
      - text / emoji
      - commands such as /bika, /harem, .gift
      - stickers
      - photo / video / GIF / voice / audio / document
      - media with or without captions
      - forwarded messages and forwarded media

    Not counted:
      - service/status updates like member joined, pinned message, etc.
      - messages sent by bots
      - non-group chats
    """
    msg = update.effective_message
    chat = update.effective_chat

    if not msg or not chat:
        return False

    if chat.type not in ("group", "supergroup"):
        return False

    # Avoid bot messages causing loops / fake activity.
    if update.effective_user and update.effective_user.is_bot:
        return False

    # MessageHandler already excludes service/status updates, so every remaining
    # group message is counted.
    return True


def needs_pre_spawn_captcha(rarity: str | None) -> bool:
    return str(rarity or "") in PRE_SPAWN_CAPTCHA_RARITIES


def _random_4digit_code() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(4))


def make_pre_spawn_captcha() -> dict:
    """Create a 4-digit image captcha with 1 correct button + 3 wrong buttons."""
    correct_code = _random_4digit_code()
    wrong_codes: set[str] = set()

    while len(wrong_codes) < 3:
        candidate = list(correct_code)
        # Make wrong options visually close but not identical.
        changes = random.randint(1, 2)
        positions = random.sample(range(4), changes)
        for pos in positions:
            new_digit = str(random.randint(0, 9))
            while new_digit == candidate[pos]:
                new_digit = str(random.randint(0, 9))
            candidate[pos] = new_digit
        wrong = "".join(candidate)
        if wrong != correct_code:
            wrong_codes.add(wrong)

    options = list(wrong_codes) + [correct_code]
    random.shuffle(options)

    return {
        "code": correct_code,
        "answerIndex": options.index(correct_code),
        "options": options,
        "nonce": secrets.token_hex(4),
    }


def render_pre_spawn_captcha_image(code: str) -> InputFile:
    """Render a simple 4-segment captcha image."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as exc:
        raise RuntimeError("Pillow is required for image captcha. Install with: pip install Pillow") from exc

    width, height = 980, 320
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    # Background noise dots.
    for _ in range(220):
        x = random.randint(0, width - 1)
        y = random.randint(0, height - 1)
        gray = random.randint(160, 225)
        draw.ellipse((x, y, x + 2, y + 2), fill=(gray, gray, gray))

    # Try repo font first, fallback to Render system fonts.
    font = None
    try:
        font = ImageFont.truetype(str(CAPTCHA_FONT_PATH), 36)
    except Exception:
        for font_path in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ):
            try:
                font = ImageFont.truetype(font_path, 36)
                break
            except Exception:
                pass

    if font is None:
        font = ImageFont.load_default()

    segment_colors = [
        (150, 20, 50),
        (180, 135, 40),
        (100, 155, 20),
        (40, 180, 175),
    ]

    xs = [120, 350, 590, 830]
    for idx, digit in enumerate(code):
        color = segment_colors[idx % len(segment_colors)]
        x = xs[idx]
        line_type = random.choice(["vertical", "slant_down", "slant_up", "flat"])

        if line_type == "vertical":
            x1, y1 = x, random.randint(35, 60)
            x2, y2 = x + random.randint(-18, 18), random.randint(220, 275)
        elif line_type == "slant_down":
            x1, y1 = x - 70, random.randint(55, 110)
            x2, y2 = x + 70, random.randint(200, 260)
        elif line_type == "slant_up":
            x1, y1 = x - 70, random.randint(180, 250)
            x2, y2 = x + 70, random.randint(55, 115)
        else:
            x1, y1 = x - 80, random.randint(200, 255)
            x2, y2 = x + 80, y1 + random.randint(-10, 10)

        points = []
        steps = 18
        for s in range(steps + 1):
            tval = s / steps
            px = int(x1 + (x2 - x1) * tval + random.randint(-2, 2))
            py = int(y1 + (y2 - y1) * tval + random.randint(-2, 2))
            points.append((px, py))

        draw.line(points, fill=color, width=5)

        label_x = min(max(int((x1 + x2) / 2) + random.randint(-25, 25), 25), width - 60)
        label_y = min(max(int((y1 + y2) / 2) + random.randint(-18, 18), 20), height - 55)
        draw.text((label_x, label_y), digit, fill=(90, 90, 90), font=font)

    bio = io.BytesIO()
    bio.name = "bika_captcha.png"
    image.save(bio, format="PNG")
    bio.seek(0)
    return InputFile(bio, filename="bika_captcha.png")


def pre_spawn_keyboard(chat_id: int, nonce: str, options: list[str]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(str(value), callback_data=f"precap:{chat_id}:{nonce}:{idx}")
        for idx, value in enumerate(options[:4])
    ]
    return InlineKeyboardMarkup([
        buttons[:2],
        buttons[2:],
    ])


def pre_spawn_caption(scheduled_rarity: str, group_name: str, seconds: int) -> str:
    emoji = get_rarity_emoji(scheduled_rarity)
    return (
        f"🧩 <b>𝐇𝐈𝐆𝐇 𝐑𝐀𝐑𝐈𝐓𝐘 𝐂𝐀𝐏𝐓𝐂𝐇𝐀</b>\n\n"
        f"🔢 ᴛᴀᴘ ᴛʜᴇ <b>4-ᴅɪɢɪᴛ ᴄᴏᴅᴇ</b> ꜱʜᴏᴡɴ ɪɴ ᴛʜᴇ ɪᴍᴀɢᴇ.\n"
        f"⏳ ᴛɪᴍᴇ: <b>{int(seconds)}𝐬</b>\n\n"
        f"✅ ᴄᴏʀʀᴇᴄᴛ = ᴄʜᴀʀᴀᴄᴛᴇʀ ᴡɪʟʟ ꜱᴘᴀᴡɴ.\n"
        f"❌ ᴡʀᴏɴɢ / ᴛɪᴍᴇᴏᴜᴛ = ᴛʜɪꜱ ᴅʀᴏᴘ ᴡɪʟʟ ʙᴇ ʟᴏꜱᴛ."
    )


async def drop_listener(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_countable_message(update):
        return

    chat = update.effective_chat
    user = update.effective_user

    group = await ensure_group(chat)
    if not group:
        return

    active = (group or {}).get("activeDrop") or {}
    pre_cap = active.get("preSpawnCaptcha") or {}
    if pre_cap.get("status") == "pending":
        # A high-rarity pre-spawn captcha is already active. Do not advance drop counter
        # until it is solved, failed, or timed out.
        return

    # If Telegram gives us a real user, apply bot-mute / 6-message streak logic.
    # Forwarded media still has the forwarding user as effective_user and will be counted.
    # Rare sender-chat/no-user messages are counted, but mute logic is skipped safely.
    if user:
        await ensure_user(user)

        if await is_bot_muted(chat.id, user.id):
            return

        just_muted = await record_message_and_maybe_mute(update)
        if just_muted:
            await update.effective_message.reply_text(
                t("bot_muted", name=user.first_name, minutes=BOT_MUTE_SECONDS // 60)
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

    try:
        captcha_photo = render_pre_spawn_captcha_image(captcha["code"])
    except Exception as exc:
        await update.effective_message.reply_text(f"❌ Failed to generate captcha image: {exc}")
        await get_db().groups.update_one(
            {"groupId": int(chat.id)},
            {"$set": {"activeDrop": None, "updatedAt": utcnow()}},
        )
        return

    sent = await update.effective_message.reply_photo(
        photo=captcha_photo,
        caption=pre_spawn_caption(scheduled_rarity, safe_chat_title(chat), CLAIM_CAPTCHA_SECONDS),
        parse_mode=ParseMode.HTML,
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
                        "code": captcha["code"],
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


async def send_manual_pre_spawn_captcha(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    chat,
    photo: dict,
    drop_number: int = 0,
) -> None:
    """Send the normal pre-spawn captcha for a specific owner-forced card."""
    scheduled_rarity = str(photo.get("rarity", "Common"))
    captcha = make_pre_spawn_captcha()
    expires_at = utcnow() + timedelta(seconds=CLAIM_CAPTCHA_SECONDS)

    try:
        captcha_photo = render_pre_spawn_captcha_image(captcha["code"])
    except Exception as exc:
        await context.bot.send_message(chat_id=int(chat_id), text=f"❌ Failed to generate captcha image: {exc}")
        await get_db().groups.update_one(
            {"groupId": int(chat_id)},
            {"$set": {"activeDrop": None, "updatedAt": utcnow()}},
            upsert=True,
        )
        return

    sent = await context.bot.send_photo(
        chat_id=int(chat_id),
        photo=captcha_photo,
        caption=pre_spawn_caption(scheduled_rarity, safe_chat_title(chat) if chat else str(chat_id), CLAIM_CAPTCHA_SECONDS),
        parse_mode=ParseMode.HTML,
        reply_markup=pre_spawn_keyboard(int(chat_id), captcha["nonce"], captcha["options"]),
    )

    await get_db().groups.update_one(
        {"groupId": int(chat_id)},
        {
            "$set": {
                "activeDrop": {
                    "cardId": str(photo.get("cardId", "")),
                    "name": str(photo.get("name", "")),
                    "normalizedName": str(photo.get("normalizedName", "")),
                    "rarity": scheduled_rarity,
                    "anime": str(photo.get("anime", "")),
                    "fileId": str(photo.get("fileId", "")),
                    "messageId": 0,
                    "dropNumber": int(drop_number),
                    "scheduledRarity": scheduled_rarity,
                    "manualDrop": True,
                    "isClaimed": False,
                    "claimedByUserId": 0,
                    "claimedByName": "",
                    "droppedAt": None,
                    "preSpawnCaptcha": {
                        "status": "pending",
                        "nonce": captcha["nonce"],
                        "code": captcha["code"],
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
        upsert=True,
    )
    context.application.create_task(
        pre_spawn_timeout_task(context.bot, int(chat_id), captcha["nonce"], int(CLAIM_CAPTCHA_SECONDS))
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


async def _edit_pre_spawn_result(bot, chat_id: int, message_id: int, text: str, parse_mode: str | None = ParseMode.HTML) -> None:
    if not message_id:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
        return
    try:
        await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=text,
            parse_mode=parse_mode,
            reply_markup=None,
        )
        return
    except Exception:
        pass
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=None,
        )
    except Exception:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)


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
    text = t("pre_spawn_timeout")
    try:
        await _edit_pre_spawn_result(bot, chat_id, message_id, text, ParseMode.HTML)
    except Exception:
        pass
    await clear_lost_pre_spawn(chat_id, nonce)


async def pre_spawn_captcha_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    parts = query.data.split(":")
    if len(parts) != 4:
        await query.answer(t("captcha_invalid"), show_alert=True)
        return

    _, chat_id_raw, nonce, choice_raw = parts
    chat_id = int(chat_id_raw)
    choice_index = int(choice_raw)

    latest = await get_db().groups.find_one({"groupId": int(chat_id), "activeDrop.preSpawnCaptcha.nonce": str(nonce)})
    active = (latest or {}).get("activeDrop") or {}
    pre_cap = active.get("preSpawnCaptcha") or {}
    if not active or pre_cap.get("status") != "pending":
        await query.answer(t("captcha_finished"), show_alert=True)
        return

    correct_index = int(pre_cap.get("answerIndex", -1))
    if choice_index != correct_index:
        await mark_pre_spawn_lost(chat_id, nonce, "failed")
        try:
            await query.edit_message_caption(
                caption=t("pre_spawn_wrong"),
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
        except Exception:
            try:
                await query.edit_message_text(
                    t("pre_spawn_wrong"),
                    parse_mode=ParseMode.HTML,
                    reply_markup=None,
                )
            except Exception:
                pass
        await query.answer(t("pre_spawn_wrong_alert"), show_alert=True)
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
        await query.answer(t("captcha_finished"), show_alert=True)
        return

    try:
        await query.edit_message_caption(
            caption=t("pre_spawn_solved"),
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )
    except Exception:
        try:
            await query.edit_message_text(
                t("pre_spawn_solved"),
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
        except Exception:
            pass
    await query.answer(t("solved"))

    # Use the callback chat when available; otherwise send by chat_id only.
    chat = query.message.chat if query.message else None
    forced_photo = None
    if active.get("manualDrop") and active.get("cardId"):
        forced_photo = {
            "cardId": str(active.get("cardId", "")),
            "name": str(active.get("name", "")),
            "normalizedName": str(active.get("normalizedName", "")),
            "rarity": str(active.get("rarity", scheduled_rarity)),
            "anime": str(active.get("anime", "")),
            "fileId": str(active.get("fileId", "")),
        }
    await send_spawn_card(context, int(chat_id), chat, scheduled_rarity, drop_number, forced_photo=forced_photo)


async def send_spawn_card(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    chat,
    scheduled_rarity: str,
    drop_number: int,
    forced_photo: dict | None = None,
) -> None:
    photo = forced_photo or await get_drop_photo_for_rarity(scheduled_rarity)
    if not photo:
        await get_db().groups.update_one(
            {"groupId": int(chat_id)},
            {"$set": {"activeDrop": None, "updatedAt": utcnow()}},
        )
        try:
            await context.bot.send_message(chat_id=chat_id, text=t("spawn_no_card"))
        except Exception:
            pass
        return

    emoji = get_rarity_emoji(photo.get("rarity"))
    group_name = safe_chat_title(chat) if chat else str(chat_id)
    caption = t("spawn_caption", emoji=emoji, group_name=group_name)
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


async def owner_drop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only manual drop: /drop <card_id> <chat_id>."""
    if not update.effective_user or not is_owner(update.effective_user.id):
        return

    msg = update.effective_message
    if len(context.args) < 2:
        await msg.reply_text("Usage: /drop <card_id> <chat_id>\nExample: /drop 131 -1001234567890")
        return

    card_id = str(context.args[0]).strip()
    chat_id_raw = str(context.args[1]).strip()

    try:
        target_chat_id = int(chat_id_raw)
    except ValueError:
        await msg.reply_text("❌ Invalid chat_id. Example: -1001234567890")
        return

    photo = await get_photo_by_card_id(card_id)
    if not photo:
        await msg.reply_text(f"❌ Card ID {card_id} not found.")
        return

    try:
        target_chat = await context.bot.get_chat(target_chat_id)
    except Exception as exc:
        await msg.reply_text(f"❌ Cannot access target chat: {exc}")
        return

    if target_chat.type not in ("group", "supergroup"):
        await msg.reply_text("❌ Target chat must be a group or supergroup.")
        return

    group = await ensure_group(target_chat)
    active = (group or {}).get("activeDrop") or {}
    pre_cap = active.get("preSpawnCaptcha") or {}
    if pre_cap.get("status") == "pending":
        await msg.reply_text("❌ Target group already has a pending captcha.")
        return
    if active and active.get("cardId") and not active.get("isClaimed"):
        await msg.reply_text("❌ Target group already has an active unclaimed drop.")
        return

    rarity = str(photo.get("rarity", "Common"))
    if needs_pre_spawn_captcha(rarity):
        await send_manual_pre_spawn_captcha(context, target_chat_id, target_chat, photo, drop_number=0)
        await msg.reply_text(
            f"✅ Manual drop captcha sent.\nCard ID: {card_id}\nRarity: {rarity}\nChat ID: {target_chat_id}"
        )
        return

    await send_spawn_card(context, target_chat_id, target_chat, rarity, drop_number=0, forced_photo=photo)
    await msg.reply_text(
        f"✅ Manual drop spawned.\nCard ID: {card_id}\nRarity: {rarity}\nChat ID: {target_chat_id}"
    )


def register_drop_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("drop", owner_drop_cmd))
    app.add_handler(CallbackQueryHandler(pre_spawn_captcha_callback, pattern=r"^precap:-?\d+:[0-9a-f]+:\d+$"))

    # Register in group=1 so this counter still runs after command handlers
    # such as /bika, /harem, /check, .gift handlers in group=0.
    # It catches every normal group message type; is_countable_message() filters only bots/non-groups.
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL, drop_listener),
        group=1,
    )
