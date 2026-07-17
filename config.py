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


def env_float(name: str, default: float, min_value: float | None = None, max_value: float | None = None) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except Exception:
        value = float(default)
    if min_value is not None:
        value = max(float(min_value), value)
    if max_value is not None:
        value = min(float(max_value), value)
    return value

def env_command(name: str, default: str = "bika") -> str:
    """Read and validate a Telegram bot command name from environment variables.

    Accepted examples:
      bika
      /bika
      dao_1

    Invalid or empty values safely fall back to ``default``.
    """
    fallback = str(default or "bika").strip().lower().lstrip("/") or "bika"
    value = str(os.getenv(name, fallback) or "").strip().lower().lstrip("/")

    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_")
    if not (1 <= len(value) <= 32):
        return fallback
    if any(ch not in allowed for ch in value):
        return fallback

    return value


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MONGODB_URI = os.getenv("MONGODB_URI", os.getenv("MONGO_URI", "")).strip()
DB_NAME = os.getenv("DATABASE_NAME", os.getenv("DB_NAME", "bika_character_bot")).strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "@Official_Bika").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "").replace("@", "").strip()
CLAIM_COMMAND = env_command("CLAIM_COMMAND", "bika")


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
# In VPS/PM2 polling mode, 0 is safest: any update created before this process
# started is ignored, so offline queued messages cannot trigger mass drops.
DROP_IGNORE_OLD_MESSAGES_SECONDS = env_int("DROP_IGNORE_OLD_MESSAGES_SECONDS", 0, 0, 3600)

# Startup reset controls for VPS/PM2 polling.
# When the bot process starts, reset all group message counters to avoid an old
# near-complete counter (e.g. 98/100) immediately dropping after restart.
RESET_GROUP_MESSAGE_COUNT_ON_STARTUP = env_bool("RESET_GROUP_MESSAGE_COUNT_ON_STARTUP", "true")
# Keep active drops by default. Set true only if you want every restart to remove
# currently spawned / unclaimed drops and pending captchas.
CLEAR_ACTIVE_DROP_ON_STARTUP = env_bool("CLEAR_ACTIVE_DROP_ON_STARTUP", "false")

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
# /profile output mode: generated image is always used; true adds a second Rich Message table.
PROFILE_TABLE = env_bool("PROFILE_TABLE", "false")
PROFILE_TITLE = os.getenv("PROFILE_TITLE", "Bika Characters Profile").strip() or "Bika Characters Profile"
ANTI_SPAM_STREAK = int(os.getenv("ANTI_SPAM_STREAK", "6") or 6)
BOT_MUTE_SECONDS = int(os.getenv("BOT_MUTE_SECONDS", "600") or 600)
CLAIM_PREFIX_MIN_LENGTH = int(os.getenv("CLAIM_PREFIX_MIN_LENGTH", "3") or 3)
CLAIM_CAPTCHA_SECONDS = int(os.getenv("CLAIM_CAPTCHA_SECONDS", "120") or 120)
INLINE_PAGE_SIZE = env_int("INLINE_PAGE_SIZE", 50, 1, 50)
INLINE_CACHE_TIME = env_int("INLINE_CACHE_TIME", 60, 0, 300)

# ---------------------------------------------------------------------------
# BROADCAST SETTINGS
# ---------------------------------------------------------------------------
ENABLE_BROADCAST = env_bool("ENABLE_BROADCAST", "true")
BROADCAST_WORKERS = env_int("BROADCAST_WORKERS", 20, 1, 50)
BROADCAST_DELAY = env_float("BROADCAST_DELAY", 0.05, 0.0, 5.0)
BROADCAST_MAX_RETRY = env_int("BROADCAST_MAX_RETRY", 3, 1, 10)
ENABLE_BROADCAST_LOG = env_bool("ENABLE_BROADCAST_LOG", "true")

# Daily claim limit uses Myanmar/Yangon date.
CLAIM_DAILY_LIMIT = int(os.getenv("CLAIM_DAILY_LIMIT", "25") or 25)
CLAIM_TIMEZONE = os.getenv("CLAIM_TIMEZONE", "Asia/Yangon").strip() or "Asia/Yangon"

# Limited / owner-give-only card collection.
# Cards with rarity Limited or non-numeric IDs such as 1a, 1s, 1bc are stored here.
LIMITED_CARDS_COLLECTION = os.getenv("LIMITED_CARDS_COLLECTION", "limited_cards").strip() or "limited_cards"

# ---------------------------------------------------------------------------
# RARITY SETTINGS
# ---------------------------------------------------------------------------
# New bot / new database အတွက် rarity name နဲ့ emoji တွေကို ဒီနေရာတစ်ခုတည်းကနေ
# စိတ်ကြိုက်ပြောင်းနိုင်အောင် centralize လုပ်ထားပါတယ်။
#
# ပြောင်းချင်ရင် .env ထဲမှာ ဥပမာ:
# RARITY_COMMON_NAME=Bronze
# RARITY_COMMON_EMOJI=🥉
#
# Database အသစ်မစခင်ပြောင်းထားရင် migration မလိုပါဘူး။
RARITY_COMMON_NAME = os.getenv("RARITY_COMMON_NAME", "Common").strip() or "Common"
RARITY_UNCOMMON_NAME = os.getenv("RARITY_UNCOMMON_NAME", "Uncommon").strip() or "Uncommon"
RARITY_RARE_NAME = os.getenv("RARITY_RARE_NAME", "Rare").strip() or "Rare"
RARITY_LEGENDARY_NAME = os.getenv("RARITY_LEGENDARY_NAME", "Legendary").strip() or "Legendary"
RARITY_MYSTICAL_NAME = os.getenv("RARITY_MYSTICAL_NAME", "Mystical").strip() or "Mystical"
RARITY_DIVINE_NAME = os.getenv("RARITY_DIVINE_NAME", "Divine").strip() or "Divine"
RARITY_CROSSVERSE_NAME = os.getenv("RARITY_CROSSVERSE_NAME", "CrossVerse").strip() or "CrossVerse"
RARITY_CATAPHRACT_NAME = os.getenv("RARITY_CATAPHRACT_NAME", "Cataphract").strip() or "Cataphract"
RARITY_SUPREME_NAME = os.getenv("RARITY_SUPREME_NAME", "Supreme").strip() or "Supreme"

# Limited rarity name ကို old config key နဲ့လည်း compatible ဖြစ်အောင်ထားထားပါတယ်။
LIMITED_RARITY_NAME = os.getenv(
    "LIMITED_RARITY_NAME",
    os.getenv("RARITY_LIMITED_NAME", "Limited"),
).strip() or "Limited"
RARITY_LIMITED_NAME = LIMITED_RARITY_NAME

LIMITED_CUSTOM_EMOJI_ID = os.getenv("LIMITED_CUSTOM_EMOJI_ID", "5361837567463399422").strip()
LIMITED_FALLBACK_EMOJI = os.getenv("LIMITED_FALLBACK_EMOJI", os.getenv("RARITY_LIMITED_EMOJI", "🔮")).strip() or "🔮"

# Bot API supports custom emoji icons and colored button styles on InlineKeyboardButton.
# These toggles make rollback easy if a self-hosted/old Bot API server is used.
ENABLE_BUTTON_CUSTOM_EMOJI = env_bool("ENABLE_BUTTON_CUSTOM_EMOJI", "true")
ENABLE_BUTTON_STYLE = env_bool("ENABLE_BUTTON_STYLE", "true")

LIMITED_EMOJI = (
    f'<tg-emoji emoji-id="{LIMITED_CUSTOM_EMOJI_ID}">{LIMITED_FALLBACK_EMOJI}</tg-emoji>'
    if LIMITED_CUSTOM_EMOJI_ID
    else LIMITED_FALLBACK_EMOJI
)

RARITY_COMMON_EMOJI = os.getenv("RARITY_COMMON_EMOJI", "🔵").strip() or "🔵"
RARITY_UNCOMMON_EMOJI = os.getenv("RARITY_UNCOMMON_EMOJI", "🟣").strip() or "🟣"
RARITY_RARE_EMOJI = os.getenv("RARITY_RARE_EMOJI", "🟠").strip() or "🟠"
RARITY_LEGENDARY_EMOJI = os.getenv("RARITY_LEGENDARY_EMOJI", "🟡").strip() or "🟡"
RARITY_MYSTICAL_EMOJI = os.getenv("RARITY_MYSTICAL_EMOJI", "💮").strip() or "💮"
RARITY_DIVINE_EMOJI = os.getenv("RARITY_DIVINE_EMOJI", "⚜️").strip() or "⚜️"
RARITY_CROSSVERSE_EMOJI = os.getenv("RARITY_CROSSVERSE_EMOJI", "⚡").strip() or "⚡"
RARITY_CATAPHRACT_EMOJI = os.getenv("RARITY_CATAPHRACT_EMOJI", "✨").strip() or "✨"
RARITY_SUPREME_EMOJI = os.getenv("RARITY_SUPREME_EMOJI", "🪞").strip() or "🪞"

RARITY_ORDER = [
    RARITY_LIMITED_NAME,
    RARITY_SUPREME_NAME,
    RARITY_CATAPHRACT_NAME,
    RARITY_CROSSVERSE_NAME,
    RARITY_DIVINE_NAME,
    RARITY_MYSTICAL_NAME,
    RARITY_LEGENDARY_NAME,
    RARITY_RARE_NAME,
    RARITY_UNCOMMON_NAME,
    RARITY_COMMON_NAME,
]

# Rarity emoji used inside HTML messages/captions. Buttons use utils.buttons
# to send Bot API icon_custom_emoji_id/style with safe fallback text.
RARITY_EMOJI = {
    RARITY_LIMITED_NAME: LIMITED_EMOJI,
    RARITY_SUPREME_NAME: RARITY_SUPREME_EMOJI,
    RARITY_CATAPHRACT_NAME: RARITY_CATAPHRACT_EMOJI,
    RARITY_CROSSVERSE_NAME: RARITY_CROSSVERSE_EMOJI,
    RARITY_DIVINE_NAME: RARITY_DIVINE_EMOJI,
    RARITY_MYSTICAL_NAME: RARITY_MYSTICAL_EMOJI,
    RARITY_LEGENDARY_NAME: RARITY_LEGENDARY_EMOJI,
    RARITY_RARE_NAME: RARITY_RARE_EMOJI,
    RARITY_UNCOMMON_NAME: RARITY_UNCOMMON_EMOJI,
    RARITY_COMMON_NAME: RARITY_COMMON_EMOJI,
}

RARITY_EXP = {
    RARITY_LIMITED_NAME: env_int("RARITY_LIMITED_EXP", 0, 0),
    RARITY_SUPREME_NAME: env_int("RARITY_SUPREME_EXP", 100, 0),
    RARITY_CATAPHRACT_NAME: env_int("RARITY_CATAPHRACT_EXP", 60, 0),
    RARITY_CROSSVERSE_NAME: env_int("RARITY_CROSSVERSE_EXP", 35, 0),
    RARITY_DIVINE_NAME: env_int("RARITY_DIVINE_EXP", 20, 0),
    RARITY_MYSTICAL_NAME: env_int("RARITY_MYSTICAL_EXP", 12, 0),
    RARITY_LEGENDARY_NAME: env_int("RARITY_LEGENDARY_EXP", 7, 0),
    RARITY_RARE_NAME: env_int("RARITY_RARE_EXP", 4, 0),
    RARITY_UNCOMMON_NAME: env_int("RARITY_UNCOMMON_EXP", 2, 0),
    RARITY_COMMON_NAME: env_int("RARITY_COMMON_EXP", 1, 0),
}

# Scheduled drop config.
# Normal drops use these base rarities. Milestones use the specific names below.
DROP_BASE_RARITIES = tuple(
    item.strip()
    for item in os.getenv(
        "DROP_BASE_RARITIES",
        f"{RARITY_COMMON_NAME},{RARITY_UNCOMMON_NAME},{RARITY_RARE_NAME}",
    ).split(",")
    if item.strip()
) or (RARITY_COMMON_NAME, RARITY_UNCOMMON_NAME, RARITY_RARE_NAME)

DROP_20_RARITY = os.getenv("DROP_20_RARITY", RARITY_LEGENDARY_NAME).strip() or RARITY_LEGENDARY_NAME
DROP_100_RARITY = os.getenv("DROP_100_RARITY", RARITY_MYSTICAL_NAME).strip() or RARITY_MYSTICAL_NAME
DROP_300_RARITY = os.getenv("DROP_300_RARITY", RARITY_DIVINE_NAME).strip() or RARITY_DIVINE_NAME
DROP_400_RARITY = os.getenv("DROP_400_RARITY", RARITY_CROSSVERSE_NAME).strip() or RARITY_CROSSVERSE_NAME
DROP_500_PRIMARY_RARITY = os.getenv("DROP_500_PRIMARY_RARITY", RARITY_CATAPHRACT_NAME).strip() or RARITY_CATAPHRACT_NAME
DROP_500_SECONDARY_RARITY = os.getenv("DROP_500_SECONDARY_RARITY", RARITY_SUPREME_NAME).strip() or RARITY_SUPREME_NAME
DROP_500_SECONDARY_CHANCE = env_float("DROP_500_SECONDARY_CHANCE", 0.30, 0.0, 1.0)
