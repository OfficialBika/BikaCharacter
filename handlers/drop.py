from __future__ import annotations

import asyncio
import io
import random
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pymongo import ReturnDocument
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from config import (
    BOT_MUTE_SECONDS,
    CLAIM_CAPTCHA_SECONDS,
    DEFAULT_CHANGETIME,
    DROP_IGNORE_OLD_MESSAGES_SECONDS,
    CAPTCHA_IMAGE_FORMAT,
    CAPTCHA_IMAGE_WIDTH,
    CAPTCHA_IMAGE_HEIGHT,
    CAPTCHA_JPEG_QUALITY,
)
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

# When the bot is offline, Telegram may keep pending updates and deliver them
# after PM2 restarts the process. Without this guard, old group messages can
# be counted at once and multiple cards can spawn immediately on startup.
BOT_STARTED_AT = datetime.now(timezone.utc)
STALE_UPDATE_GRACE_SECONDS = int(DROP_IGNORE_OLD_MESSAGES_SECONDS)


def _datetime_to_utc_ts(value) -> float:
    """Convert Telegram/PyMongo datetimes to UTC timestamp safely."""
    if not isinstance(value, datetime):
        return 0.0
    try:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return float(value.timestamp())
    except Exception:
        return 0.0


def is_stale_startup_update(update: Update) -> bool:
    """Ignore messages created before this process started.

    This prevents queued Telegram updates from increasing messageCount and
    spawning cards after the bot was stopped/restarted.
    """
    msg = update.effective_message
    if not msg or not getattr(msg, "date", None):
        return False

    msg_ts = _datetime_to_utc_ts(msg.date)
    started_ts = _datetime_to_utc_ts(BOT_STARTED_AT)
    if not msg_ts or not started_ts:
        return False

    return msg_ts < (started_ts - STALE_UPDATE_GRACE_SECONDS)


def is_pre_spawn_expired(pre_cap: dict) -> bool:
    """Return True when a DB-stored captcha expired while the bot was offline."""
    expires_ts = _datetime_to_utc_ts((pre_cap or {}).get("expiresAt"))
    if not expires_ts:
        return False
    return expires_ts <= datetime.now(timezone.utc).timestamp()


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
    """Render a smaller compressed 4-digit noisy captcha image.

    JPEG output saves much more bandwidth than PNG while still being readable.
    Size/format can be controlled from .env:
      CAPTCHA_IMAGE_FORMAT=jpeg
      CAPTCHA_IMAGE_WIDTH=960
      CAPTCHA_IMAGE_HEIGHT=480
      CAPTCHA_JPEG_QUALITY=74
    """
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
    except Exception as exc:
        raise RuntimeError("Pillow is required for image captcha. Install with: pip install Pillow") from exc

    def load_captcha_font(size: int):
        font = None

        try:
            font = ImageFont.truetype(str(CAPTCHA_FONT_PATH), size)
        except Exception:
            for font_path in (
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
                "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            ):
                try:
                    font = ImageFont.truetype(font_path, size)
                    break
                except Exception:
                    pass

        if font is None:
            font = ImageFont.load_default()

        return font

    width = max(640, int(CAPTCHA_IMAGE_WIDTH))
    height = max(320, int(CAPTCHA_IMAGE_HEIGHT))
    output_format = str(CAPTCHA_IMAGE_FORMAT or "jpeg").strip().lower()
    if output_format == "jpg":
        output_format = "jpeg"

    image = Image.new("RGB", (width, height), (248, 248, 248))
    draw = ImageDraw.Draw(image)

    font = load_captcha_font(max(40, int(height * 0.12)))
    layer_size = max(120, int(height * 0.34))

    def draw_rotated_digit(base_img, digit: str, center_pos: tuple[float, float], font, angle: int) -> None:
        digit_layer = Image.new("RGBA", (layer_size, layer_size), (255, 255, 255, 0))
        digit_draw = ImageDraw.Draw(digit_layer)

        bbox = digit_draw.textbbox((0, 0), digit, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        x = (layer_size - text_w) // 2
        y = (layer_size - text_h) // 2

        digit_draw.text((x + 2, y + 2), digit, font=font, fill=(180, 180, 180, 130))
        digit_draw.text((x, y), digit, font=font, fill=(70, 70, 70, 235))

        digit_layer = digit_layer.rotate(
            angle,
            expand=True,
            resample=Image.Resampling.BICUBIC,
        )

        px = int(center_pos[0] - digit_layer.width / 2)
        py = int(center_pos[1] - digit_layer.height / 2)
        base_img.paste(digit_layer, (px, py), digit_layer)

    # Background noise. Scaled down with image size to reduce generated media size.
    dot_count = max(260, int((width * height) / 1250))
    for _ in range(dot_count):
        x = random.randint(0, width - 1)
        y = random.randint(0, height - 1)
        shade = random.randint(145, 225)
        if random.random() < 0.80:
            draw.point((x, y), fill=(shade, shade, shade))
        else:
            draw.ellipse((x, y, x + 1, y + 1), fill=(shade, shade, shade))

    line_colors = [
        (145, 20, 60),
        (175, 130, 25),
        (90, 150, 35),
        (35, 175, 170),
        (80, 80, 80),
        (170, 60, 135),
    ]

    xs = [int(width * 0.14), int(width * 0.38), int(width * 0.64), int(width * 0.86)]
    min_cy = int(height * 0.36)
    max_cy = int(height * 0.62)

    for idx, digit in enumerate(str(code)[:4]):
        cx = xs[idx]
        cy = random.randint(min_cy, max_cy)
        line_type = random.choice(["vertical", "horizontal", "diag_up", "diag_down"])

        if line_type == "vertical":
            x1 = cx + random.randint(-int(width * 0.025), int(width * 0.025))
            y1 = cy - random.randint(int(height * 0.15), int(height * 0.22))
            x2 = x1 + random.randint(-int(width * 0.015), int(width * 0.015))
            y2 = cy + random.randint(int(height * 0.15), int(height * 0.22))
            text_angle = random.randint(-14, 14)

        elif line_type == "horizontal":
            x1 = cx - random.randint(int(width * 0.08), int(width * 0.12))
            y1 = cy + random.randint(-int(height * 0.04), int(height * 0.04))
            x2 = cx + random.randint(int(width * 0.08), int(width * 0.12))
            y2 = y1 + random.randint(-int(height * 0.025), int(height * 0.025))
            text_angle = random.randint(-10, 10)

        elif line_type == "diag_up":
            x1 = cx - random.randint(int(width * 0.06), int(width * 0.10))
            y1 = cy + random.randint(int(height * 0.10), int(height * 0.18))
            x2 = cx + random.randint(int(width * 0.06), int(width * 0.10))
            y2 = cy - random.randint(int(height * 0.10), int(height * 0.18))
            text_angle = random.randint(-38, -15)

        else:
            x1 = cx - random.randint(int(width * 0.06), int(width * 0.10))
            y1 = cy - random.randint(int(height * 0.10), int(height * 0.18))
            x2 = cx + random.randint(int(width * 0.06), int(width * 0.10))
            y2 = cy + random.randint(int(height * 0.10), int(height * 0.18))
            text_angle = random.randint(15, 38)

        color = random.choice(line_colors)
        points = []
        steps = 24
        jitter = max(2, int(min(width, height) * 0.005))
        for s in range(steps + 1):
            ratio = s / steps
            px = int(x1 + (x2 - x1) * ratio + random.randint(-jitter, jitter))
            py = int(y1 + (y2 - y1) * ratio + random.randint(-jitter, jitter))
            points.append((px, py))

        draw.line(points, fill=color, width=max(3, int(height * 0.009)))

        mx = (x1 + x2) / 2
        my = (y1 + y2) / 2
        digit_x = mx + random.randint(-int(width * 0.012), int(width * 0.012))
        digit_y = my + random.randint(-int(height * 0.018), int(height * 0.018))

        draw_rotated_digit(
            image,
            digit,
            (digit_x, digit_y),
            font,
            text_angle + random.randint(-8, 8),
        )

    for _ in range(max(12, int((width * height) / 28000))):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = x1 + random.randint(-int(width * 0.10), int(width * 0.10))
        y2 = y1 + random.randint(-int(height * 0.10), int(height * 0.10))
        shade = random.randint(165, 225)
        draw.line((x1, y1, x2, y2), fill=(shade, shade, shade), width=1)

    extra_dots = max(160, int((width * height) / 1800))
    for _ in range(extra_dots):
        x = random.randint(0, width - 1)
        y = random.randint(0, height - 1)
        shade = random.randint(135, 220)
        draw.point((x, y), fill=(shade, shade, shade))

    image = image.filter(ImageFilter.GaussianBlur(radius=0.28))

    bio = io.BytesIO()
    if output_format in {"jpeg", "jpg"}:
        bio.name = "bika_captcha.jpg"
        image.save(
            bio,
            format="JPEG",
            quality=max(45, min(95, int(CAPTCHA_JPEG_QUALITY))),
            optimize=True,
            progressive=True,
        )
    else:
        bio.name = "bika_captcha.png"
        image.save(bio, format="PNG", optimize=True)

    bio.seek(0)
    return InputFile(bio, filename=bio.name)

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


def user_mention_html(user) -> str:
    """Build a safe clickable mention for the user who clicked captcha."""
    if not user:
        return "Unknown"

    name = " ".join([user.first_name or "", user.last_name or ""]).strip()
    if not name:
        name = user.username or str(user.id)

    return f'<a href="tg://user?id={int(user.id)}">{escape_html(name)}</a>'


def build_pre_spawn_wrong_text(query, pre_cap: dict, choice_index: int) -> str:
    """Wrong captcha result text with clicked answer and clickable user mention."""
    options = list(pre_cap.get("options") or [])

    if 0 <= int(choice_index) < len(options):
        clicked_answer = str(options[int(choice_index)])
    else:
        clicked_answer = "Unknown"

    return (
        "❌ <b>𝐖𝐑𝐎𝐍𝐆 𝐂𝐀𝐏𝐓𝐂𝐇𝐀</b>\n\n"
        f"👤 ᴄʟɪᴄᴋᴇᴅ ʙʏ: {user_mention_html(query.from_user)}\n"
        f"🔢 ᴄʟɪᴄᴋᴇᴅ ᴀɴꜱᴡᴇʀ: <b>{escape_html(clicked_answer)}</b>\n\n"
        "ᴛʜɪꜱ ꜱᴄʜᴇᴅᴜʟᴇᴅ ʜɪɢʜ-ʀᴀʀɪᴛʏ ꜱᴘᴀᴡɴ ʜᴀꜱ ʙᴇᴇɴ ʟᴏꜱᴛ."
    )


async def drop_listener(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_countable_message(update):
        return

    # PM2/VPS restart protection:
    # Do not count old Telegram updates that were created while the bot was offline.
    # This prevents queued messages from spawning many cards immediately on restart.
    if is_stale_startup_update(update):
        return

    chat = update.effective_chat
    user = update.effective_user

    group = await ensure_group(chat)
    if not group:
        return

    active = (group or {}).get("activeDrop") or {}
    pre_cap = active.get("preSpawnCaptcha") or {}
    if pre_cap.get("status") == "pending":
        # If a captcha expired while the bot process was offline, clear it now so the
        # group does not stay blocked forever after PM2 restarts the bot.
        if is_pre_spawn_expired(pre_cap):
            await get_db().groups.update_one(
                {"groupId": int(chat.id), "activeDrop.preSpawnCaptcha.status": "pending"},
                {
                    "$set": {
                        "activeDrop": None,
                        "updatedAt": utcnow(),
                    }
                },
            )
        else:
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
                    "mediaType": str(photo.get("_sentMediaType") or detect_card_media_type(photo)),
                    "mimeType": str(photo.get("mimeType", "") or ""),
                    "fileName": str(photo.get("fileName", "") or ""),
                    "fileUniqueId": str(photo.get("fileUniqueId", "") or ""),
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
        # Stale button cleanup: if the captcha is already solved/failed/timeout,
        # remove old inline buttons so users cannot keep clicking a dead captcha.
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.answer(t("captcha_finished"), show_alert=True)
        return

    correct_index = int(pre_cap.get("answerIndex", -1))
    if choice_index != correct_index:
        await mark_pre_spawn_lost(chat_id, nonce, "failed")

        wrong_text = build_pre_spawn_wrong_text(query, pre_cap, choice_index)

        try:
            await query.edit_message_caption(
                caption=wrong_text,
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
        except Exception:
            try:
                await query.edit_message_text(
                    wrong_text,
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

    # Lock this captcha first. Do not leave it as "pending" while the card is spawning,
    # otherwise double taps can race each other. "solving" also tells stale clicks to stop.
    solving = await get_db().groups.find_one_and_update(
        {
            "groupId": int(chat_id),
            "activeDrop.preSpawnCaptcha.nonce": str(nonce),
            "activeDrop.preSpawnCaptcha.status": "pending",
        },
        {
            "$set": {
                "activeDrop.preSpawnCaptcha.status": "solving",
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
    if not solving:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
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
            try:
                await query.edit_message_reply_markup(reply_markup=None)
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
            "mediaType": str(active.get("mediaType", "photo") or "photo"),
            "mimeType": str(active.get("mimeType", "") or ""),
            "fileName": str(active.get("fileName", "") or ""),
            "fileUniqueId": str(active.get("fileUniqueId", "") or ""),
        }

    spawned = await send_spawn_card(context, int(chat_id), chat, scheduled_rarity, drop_number, forced_photo=forced_photo)
    if not spawned:
        # If the card cannot be sent, clear the pre-spawn state so the group does not remain stuck.
        try:
            await get_db().groups.update_one(
                {"groupId": int(chat_id), "activeDrop.preSpawnCaptcha.nonce": str(nonce)},
                {"$set": {"activeDrop": None, "updatedAt": utcnow()}},
            )
        except Exception:
            pass


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


def _actual_media_type_from_error(exc: Exception) -> str | None:
    """Read Telegram type-mismatch BadRequest text and return the real file type."""
    text = repr(exc).lower()
    for actual in ("photo", "video", "animation", "document"):
        if f"file of type {actual}" in text:
            return actual
    return None


async def _send_media_as(context: ContextTypes.DEFAULT_TYPE, chat_id: int, method: str, file_id: str, caption: str):
    if method == "video":
        return await context.bot.send_video(chat_id=chat_id, video=file_id, caption=caption)
    if method == "animation":
        return await context.bot.send_animation(chat_id=chat_id, animation=file_id, caption=caption)
    if method == "document":
        return await context.bot.send_document(chat_id=chat_id, document=file_id, caption=caption)
    return await context.bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption)


async def send_card_media(context: ContextTypes.DEFAULT_TYPE, chat_id: int, card: dict, caption: str):
    """Send card media without fallback spam.

    The old version tried photo/video/animation/document one after another.
    That wasted Telegram API traffic and quickly hit flood limits. This version:
      1) tries the detected type once;
      2) if Telegram says the file is actually another type, retries once with that type;
      3) stops.
    """
    media_type = detect_card_media_type(card)
    file_id = str(card.get("fileId") or "")
    if not file_id:
        raise RuntimeError("Missing card fileId")

    try:
        sent = await _send_media_as(context, chat_id, media_type, file_id, caption)
        card["_sentMediaType"] = media_type
        return sent
    except Exception as exc:
        actual_type = _actual_media_type_from_error(exc)
        if actual_type and actual_type != media_type:
            sent = await _send_media_as(context, chat_id, actual_type, file_id, caption)
            card["_sentMediaType"] = actual_type
            return sent
        raise

def card_media_snapshot(card: dict) -> dict:
    return {
        "mediaType": detect_card_media_type(card),
        "mimeType": str(card.get("mimeType", "") or ""),
        "fileName": str(card.get("fileName", "") or ""),
        "fileUniqueId": str(card.get("fileUniqueId", "") or ""),
    }


async def send_spawn_card(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    chat,
    scheduled_rarity: str,
    drop_number: int,
    forced_photo: dict | None = None,
) -> bool:
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
        return False

    emoji = get_rarity_emoji(photo.get("rarity"))
    group_name = safe_chat_title(chat) if chat else str(chat_id)
    caption = t("spawn_caption", emoji=emoji, group_name=group_name)
    try:
        sent = await send_card_media(context, chat_id, photo, caption)
    except Exception as exc:
        print("DROP SEND ERROR:", repr(exc))
        await get_db().groups.update_one(
            {"groupId": int(chat_id)},
            {"$set": {"activeDrop": None, "updatedAt": utcnow()}},
        )
        try:
            await context.bot.send_message(chat_id=chat_id, text=t("spawn_no_card"))
        except Exception:
            pass
        return False

    active_drop = {
        "cardId": str(photo.get("cardId", "")),
        "name": str(photo.get("name", "")),
        "normalizedName": str(photo.get("normalizedName", "")),
        "rarity": str(photo.get("rarity", "Common")),
        "anime": str(photo.get("anime", "")),
        "fileId": str(photo.get("fileId", "")),
        "mediaType": str(photo.get("_sentMediaType") or detect_card_media_type(photo)),
        "mimeType": str(photo.get("mimeType", "") or ""),
        "fileName": str(photo.get("fileName", "") or ""),
        "fileUniqueId": str(photo.get("fileUniqueId", "") or ""),
        "messageId": int(sent.message_id),
        "dropNumber": int(drop_number),
        "scheduledRarity": scheduled_rarity,
        "isClaimed": False,
        "claimedByUserId": 0,
        "claimedByName": "",
        "droppedAt": utcnow(),
    }
    sent_media_type = str(active_drop.get("mediaType") or "")
    stored_media_type = str(photo.get("mediaType") or "").strip().lower()
    if sent_media_type and sent_media_type != stored_media_type and photo.get("cardId"):
        try:
            await get_db().photos.update_one(
                {"cardId": str(photo.get("cardId"))},
                {"$set": {"mediaType": sent_media_type, "updatedAt": utcnow()}},
            )
        except Exception:
            pass

    await get_db().groups.update_one(
        {"groupId": int(chat_id)},
        {"$set": {"activeDrop": active_drop, "updatedAt": utcnow()}},
    )
    return True


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
