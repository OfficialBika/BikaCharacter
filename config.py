"""Configuration for BIKA Character Catcher Bot."""
from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv(override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MONGODB_URI = os.getenv("MONGODB_URI", os.getenv("MONGO_URI", "")).strip()
DB_NAME = os.getenv("DB_NAME", "bika_character_bot").strip()

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
CREATE_INVITE_LINK_FOR_PRIVATE_GROUPS = os.getenv("CREATE_INVITE_LINK_FOR_PRIVATE_GROUPS", "false").strip().lower() in ("1", "true", "yes", "on")

# Private channel used as the permanent card/media archive.
# Recommended: create a private channel named "Bika Database", add the bot as admin,
# then set the numeric channel ID like -1001234567890.
CARD_DATABASE_CHANNEL_ID = os.getenv("CARD_DATABASE_CHANNEL_ID", os.getenv("BIKA_DATABASE_CHANNEL_ID", "")).strip()

PORT = int(os.getenv("PORT", "8080") or 8080)
NODE_ENV = os.getenv("NODE_ENV", "production").strip()

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
