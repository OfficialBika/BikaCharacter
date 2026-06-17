"""Configuration for BIKA Character Catcher Bot."""
from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv(override=True)


def env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def env_int(name: str, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except Exception:
        value = int(default)
    if min_value is not None:
        value = max(int(min_value), value)
    if max_value is not None:
        value = min(int(max_value), value)
    return value

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MONGODB_URI = os.getenv("MONGODB_URI", os.getenv("MONGO_URI", "")).strip()
DB_NAME = os.getenv("DATABASE_NAME", os.getenv("DB_NAME", "bika_character_bot")).strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "@Official_Bika").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "").replace("@", "").strip()


# /start page buttons.
# ADD_TO_GROUP_URL defaults to https://t.me/<BOT_USERNAME>?startgroup=true.
ADD_TO_GROUP_URL = os.getenv("ADD_TO_GROUP_URL", "").strip()
SUPPORT_GROUP_URL = os.getenv("SUPPORT_GROUP_URL", "https://t.me/Official_Bika").strip()
UPDATE_CHANNEL_URL = os.getenv("UPDATE_CHANNEL_URL", "https://t.me/Official_Bika").strip()

# Channel where bot-added-to-group logs will be posted.
# Use @channelusername or numeric channel ID like -1001234567890.
GROUP_LOG_CHANNEL_ID = os.getenv("GROUP_LOG_CHANNEL_ID", os.getenv("LOG_CHANNEL_ID", "")).strip()

# If True, bot will try to create an invite link for private groups so the
# group name in the log channel can be clickable. Bot must be admin in that group
# with invite-link permission. Public groups always use https://t.me/<username>.
CREATE_INVITE_LINK_FOR_PRIVATE_GROUPS = env_bool("CREATE_INVITE_LINK_FOR_PRIVATE_GROUPS", "false")

# Private channel used as the permanent card/media archive.
# Recommended: create a private channel named "Bika Database", add the bot as admin,
# then set the numeric channel ID like -1001234567890.
CARD_DATABASE_CHANNEL_ID = os.getenv("CARD_DATABASE_CHANNEL_ID", os.getenv("BIKA_DATABASE_CHANNEL_ID", "")).strip()
ADDER_GROUP_ID = int(os.getenv("ADDER_GROUP_ID", "-1003983636133") or -1003983636133)

PORT = int(os.getenv("PORT", "8080") or 8080)
NODE_ENV = os.getenv("NODE_ENV", "production").strip()

# Render / Telegram webhook settings.
# RUN_MODE=webhook is recommended for Render Web Service. Use RUN_MODE=polling only for local testing.
RUN_MODE = os.getenv("RUN_MODE", "webhook").strip().lower()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip().rstrip("/")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook").strip()
if not WEBHOOK_PATH.startswith("/"):
    WEBHOOK_PATH = "/" + WEBHOOK_PATH
WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", "").strip()
WEBHOOK_DROP_PENDING_UPDATES = env_bool("WEBHOOK_DROP_PENDING_UPDATES", "true")

# Bandwidth / traffic controls.
# In polling mode on VPS/PM2, a health HTTP server is usually unnecessary.
ENABLE_HEALTH_SERVER = env_bool("ENABLE_HEALTH_SERVER", "false")
# Only receive update types this bot actually uses. This reduces Telegram traffic.
BOT_ALLOWED_UPDATES = [
    item.strip()
    for item in os.getenv(
        "BOT_ALLOWED_UPDATES",
        "message,callback_query,inline_query,my_chat_member",
    ).split(",")
    if item.strip()
]
# Ignore Telegram message updates older than the process start by this many seconds.
DROP_IGNORE_OLD_MESSAGES_SECONDS = env_int("DROP_IGNORE_OLD_MESSAGES_SECONDS", 30, 0, 3600)

# Captcha image compression. JPEG is much smaller than PNG for Render bandwidth.
CAPTCHA_IMAGE_FORMAT = os.getenv("CAPTCHA_IMAGE_FORMAT", "jpeg").strip().lower()
CAPTCHA_IMAGE_WIDTH = env_int("CAPTCHA_IMAGE_WIDTH", 960, 640, 1600)
CAPTCHA_IMAGE_HEIGHT = env_int("CAPTCHA_IMAGE_HEIGHT", 480, 320, 900)
CAPTCHA_JPEG_QUALITY = env_int("CAPTCHA_JPEG_QUALITY", 74, 45, 95)

DEFAULT_CHANGETIME = int(os.getenv("DEFAULT_CHANGETIME", "100") or 100)
# Changetime permissions are fixed by bot rules:
# - Telegram group admins can set only 100-999.
# - Bot OWNER_ID can set 1-3000.
ADMIN_CHANGETIME_MIN = 100
ADMIN_CHANGETIME_MAX = 999
OWNER_CHANGETIME_MIN = 1
OWNER_CHANGETIME_MAX = 3000

HAREM_PAGE_SIZE = int(os.getenv("HAREM_PAGE_SIZE", "5") or 5)
ANTI_SPAM_STREAK = int(os.getenv("ANTI_SPAM_STREAK", "6") or 6)
BOT_MUTE_SECONDS = int(os.getenv("BOT_MUTE_SECONDS", "600") or 600)
CLAIM_PREFIX_MIN_LENGTH = int(os.getenv("CLAIM_PREFIX_MIN_LENGTH", "3") or 3)
CLAIM_CAPTCHA_SECONDS = int(os.getenv("CLAIM_CAPTCHA_SECONDS", "120") or 120)
INLINE_PAGE_SIZE = env_int("INLINE_PAGE_SIZE", 50, 1, 50)
INLINE_CACHE_TIME = env_int("INLINE_CACHE_TIME", 60, 0, 300)
# Daily claim limit uses Myanmar/Yangon date.
CLAIM_DAILY_LIMIT = int(os.getenv("CLAIM_DAILY_LIMIT", "25") or 25)
CLAIM_TIMEZONE = os.getenv("CLAIM_TIMEZONE", "Asia/Yangon").strip() or "Asia/Yangon"

RARITY_ORDER = [
    "Supreme",
    "Cataphract",
    "CrossVerse",
    "Divine",
    "Mystical",
    "Legendary",
    "Rare",
    "Uncommon",
    "Common",
]

# Telegram Bot API does not allow real colored button backgrounds. These emoji labels are
# used to make inline buttons visually colored/premium-style.
RARITY_EMOJI = {
    "Supreme": "🪞",
    "Cataphract": "✨",
    "CrossVerse": "⚡",
    "Divine": "⚜️",
    "Mystical": "💮",
    "Legendary": "🟡",
    "Rare": "🟠",
    "Uncommon": "🟣",
    "Common": "🔵",
}

RARITY_EXP = {
    "Supreme": 100,
    "Cataphract": 60,
    "CrossVerse": 35,
    "Divine": 20,
    "Mystical": 12,
    "Legendary": 7,
    "Rare": 4,
    "Uncommon": 2,
    "Common": 1,
}
