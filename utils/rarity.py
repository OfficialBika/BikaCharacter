from __future__ import annotations

import random

from config import (
    DROP_20_RARITY,
    DROP_100_RARITY,
    DROP_300_RARITY,
    DROP_400_RARITY,
    DROP_500_PRIMARY_RARITY,
    DROP_500_SECONDARY_CHANCE,
    DROP_500_SECONDARY_RARITY,
    DROP_BASE_RARITIES,
    LIMITED_FALLBACK_EMOJI,
    LIMITED_RARITY_NAME,
    RARITY_COMMON_NAME,
    RARITY_EMOJI,
    RARITY_EXP,
    RARITY_ORDER,
)


def get_rarity_emoji(rarity: str | None) -> str:
    return RARITY_EMOJI.get(str(rarity or ""), "🎴")


def get_rarity_exp(rarity: str | None) -> int:
    return int(RARITY_EXP.get(str(rarity or RARITY_COMMON_NAME), 1))


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


def get_scheduled_drop_rarity(drop_number: int) -> str:
    """Return the rarity that should spawn for a group drop number.

    Schedule is config-driven:
    - Normal drops: random from DROP_BASE_RARITIES
    - Every 20 drops : DROP_20_RARITY
    - Every 100 drops: DROP_100_RARITY
    - Every 300 drops: DROP_300_RARITY
    - Every 400 drops: DROP_400_RARITY
    - Every 500 drops: DROP_500_PRIMARY_RARITY / DROP_500_SECONDARY_RARITY

    Higher milestones take priority when a drop number matches multiple rules.
    """
    try:
        n = max(1, int(drop_number or 1))
    except Exception:
        n = 1

    if n % 500 == 0:
        return (
            DROP_500_SECONDARY_RARITY
            if random.random() < float(DROP_500_SECONDARY_CHANCE)
            else DROP_500_PRIMARY_RARITY
        )
    if n % 300 == 0:
        return DROP_400_RARITY
    if n % 200 == 0:
        return DROP_300_RARITY
    if n % 80 == 0:
        return DROP_100_RARITY
    if n % 15 == 0:
        return DROP_20_RARITY
    return random.choice(tuple(DROP_BASE_RARITIES))
