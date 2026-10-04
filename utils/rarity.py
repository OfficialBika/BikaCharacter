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
    LIMITED_CUSTOM_EMOJI_ID,
    LIMITED_FALLBACK_EMOJI,
    LIMITED_RARITY_NAME,
    RARITY_CATAPHRACT_CUSTOM_EMOJI_ID,
    RARITY_CATAPHRACT_NAME,
    RARITY_CATAPHRACT_FALLBACK_EMOJI,
    RARITY_COMMON_CUSTOM_EMOJI_ID,
    RARITY_COMMON_NAME,
    RARITY_COMMON_FALLBACK_EMOJI,
    RARITY_CROSSVERSE_CUSTOM_EMOJI_ID,
    RARITY_CROSSVERSE_NAME,
    RARITY_CROSSVERSE_FALLBACK_EMOJI,
    RARITY_DIVINE_CUSTOM_EMOJI_ID,
    RARITY_DIVINE_NAME,
    RARITY_DIVINE_FALLBACK_EMOJI,
    RARITY_EMOJI,
    RARITY_EXP,
    RARITY_LEGENDARY_CUSTOM_EMOJI_ID,
    RARITY_LEGENDARY_NAME,
    RARITY_LEGENDARY_FALLBACK_EMOJI,
    RARITY_MYSTICAL_CUSTOM_EMOJI_ID,
    RARITY_MYSTICAL_NAME,
    RARITY_MYSTICAL_FALLBACK_EMOJI,
    RARITY_ORDER,
    RARITY_RARE_CUSTOM_EMOJI_ID,
    RARITY_RARE_NAME,
    RARITY_RARE_FALLBACK_EMOJI,
    RARITY_SUPREME_CUSTOM_EMOJI_ID,
    RARITY_SUPREME_NAME,
    RARITY_SUPREME_FALLBACK_EMOJI,
    RARITY_UNCOMMON_CUSTOM_EMOJI_ID,
    RARITY_UNCOMMON_NAME,
    RARITY_UNCOMMON_FALLBACK_EMOJI,
)


_RARITY_CUSTOM_EMOJI_IDS = {
    str(LIMITED_RARITY_NAME): str(LIMITED_CUSTOM_EMOJI_ID or ""),
    # These keys are resolved from the configured rarity names via RARITY_EMOJI,
    # so renamed rarities continue to use their configured custom emoji.
}

_RARITY_FALLBACK_EMOJIS = {
    str(LIMITED_RARITY_NAME): str(LIMITED_FALLBACK_EMOJI or "🔮"),
}


def _register_rarity_custom_emoji(rarity: str, emoji_id: str, fallback: str) -> None:
    _RARITY_CUSTOM_EMOJI_IDS[str(rarity)] = str(emoji_id or "").strip()
    _RARITY_FALLBACK_EMOJIS[str(rarity)] = str(fallback or "🎴").strip() or "🎴"


# Populate by position-independent configured rarity names.
_register_rarity_custom_emoji(
    RARITY_COMMON_NAME, RARITY_COMMON_CUSTOM_EMOJI_ID, RARITY_COMMON_FALLBACK_EMOJI
)
_register_rarity_custom_emoji(
    RARITY_UNCOMMON_NAME, RARITY_UNCOMMON_CUSTOM_EMOJI_ID, RARITY_UNCOMMON_FALLBACK_EMOJI
)
_register_rarity_custom_emoji(
    RARITY_RARE_NAME, RARITY_RARE_CUSTOM_EMOJI_ID, RARITY_RARE_FALLBACK_EMOJI
)
_register_rarity_custom_emoji(
    RARITY_LEGENDARY_NAME, RARITY_LEGENDARY_CUSTOM_EMOJI_ID, RARITY_LEGENDARY_FALLBACK_EMOJI
)
_register_rarity_custom_emoji(
    RARITY_MYSTICAL_NAME, RARITY_MYSTICAL_CUSTOM_EMOJI_ID, RARITY_MYSTICAL_FALLBACK_EMOJI
)
_register_rarity_custom_emoji(
    RARITY_DIVINE_NAME, RARITY_DIVINE_CUSTOM_EMOJI_ID, RARITY_DIVINE_FALLBACK_EMOJI
)
_register_rarity_custom_emoji(
    RARITY_CROSSVERSE_NAME, RARITY_CROSSVERSE_CUSTOM_EMOJI_ID, RARITY_CROSSVERSE_FALLBACK_EMOJI
)
_register_rarity_custom_emoji(
    RARITY_CATAPHRACT_NAME, RARITY_CATAPHRACT_CUSTOM_EMOJI_ID, RARITY_CATAPHRACT_FALLBACK_EMOJI
)
_register_rarity_custom_emoji(
    RARITY_SUPREME_NAME, RARITY_SUPREME_CUSTOM_EMOJI_ID, RARITY_SUPREME_FALLBACK_EMOJI
)


def get_rarity_custom_emoji_id(rarity: str | None) -> str:
    return str(_RARITY_CUSTOM_EMOJI_IDS.get(str(rarity or ""), "") or "")


def get_rarity_fallback_emoji(rarity: str | None) -> str:
    return str(_RARITY_FALLBACK_EMOJIS.get(str(rarity or ""), "🎴") or "🎴")


def get_rarity_emoji(rarity: str | None) -> str:
    rarity_text = str(rarity or "")
    custom_id = get_rarity_custom_emoji_id(rarity_text)
    fallback = get_rarity_fallback_emoji(rarity_text)
    if custom_id:
        return f'<tg-emoji emoji-id="{custom_id}">{fallback}</tg-emoji>'
    return str(RARITY_EMOJI.get(rarity_text, fallback) or fallback)


def get_rarity_exp(rarity: str | None) -> int:
    return int(RARITY_EXP.get(str(rarity or RARITY_COMMON_NAME), 1))


def get_rarity_button_emoji(rarity: str | None) -> str:
    """Return a button-safe emoji.

    Button text is plain text, so HTML <tg-emoji> tags must not be used there.
    Limited buttons use icon_custom_emoji_id when available and this fallback emoji
    when custom emoji icons are disabled or unsupported.
    """
    rarity_text = str(rarity or "")
    return get_rarity_fallback_emoji(rarity_text)


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
    if n % 400 == 0:
        return DROP_400_RARITY
    if n % 300 == 0:
        return DROP_300_RARITY
    if n % 100 == 0:
        return DROP_100_RARITY
    if n % 20 == 0:
        return DROP_20_RARITY
    return random.choice(tuple(DROP_BASE_RARITIES))
