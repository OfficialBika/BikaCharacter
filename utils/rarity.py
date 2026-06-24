from __future__ import annotations

import random

from config import LIMITED_FALLBACK_EMOJI, LIMITED_RARITY_NAME, RARITY_EMOJI, RARITY_EXP, RARITY_ORDER


def get_rarity_emoji(rarity: str | None) -> str:
    return RARITY_EMOJI.get(str(rarity or ""), "🎴")


def get_rarity_exp(rarity: str | None) -> int:
    return int(RARITY_EXP.get(str(rarity or "Common"), 1))


def get_rarity_button_emoji(rarity: str | None) -> str:
    """Return a button-safe emoji.

    Button text is plain text, so HTML <tg-emoji> tags must not be used there.
    Limited buttons use icon_custom_emoji_id when available and this fallback emoji
    when custom emoji icons are disabled or unsupported.
    """
    if str(rarity or "") == str(LIMITED_RARITY_NAME):
        return LIMITED_FALLBACK_EMOJI
    emoji = RARITY_EMOJI.get(str(rarity or ""), "🎴")
    if isinstance(emoji, str) and emoji.startswith("<tg-emoji"):
        return LIMITED_FALLBACK_EMOJI
    return emoji


def normalize_rarity(raw: str | None) -> str | None:
    text = str(raw or "").strip().lower()
    for rarity in RARITY_ORDER:
        if rarity.lower() == text:
            return rarity
    return None


DROP_BASE_RARITIES = ("Common", "Uncommon", "Rare")


def get_scheduled_drop_rarity(drop_number: int) -> str:
    """Return the rarity that should spawn for a group drop number.

    Schedule:
    - Normal drops: random Common / Uncommon / Rare
    - Every 20 drops: Legendary
    - Every 100 drops: Mystical
    - Every 300 drops: Divine
    - Every 400 drops: CrossVerse
    - Every 500 drops: Cataphract 70% / Supreme 30%

    Higher milestones take priority when a drop number matches multiple rules.
    Example: 100 => Mystical, 300 => Divine, 500 => Cataphract/Supreme.
    """
    try:
        n = max(1, int(drop_number or 1))
    except Exception:
        n = 1

    if n % 500 == 0:
        return "Supreme" if random.random() < 0.30 else "Cataphract"
    if n % 400 == 0:
        return "CrossVerse"
    if n % 300 == 0:
        return "Divine"
    if n % 100 == 0:
        return "Mystical"
    if n % 20 == 0:
        return "Legendary"
    return random.choice(DROP_BASE_RARITIES)
