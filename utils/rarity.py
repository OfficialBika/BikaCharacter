from __future__ import annotations

from config import RARITY_EMOJI, RARITY_EXP, RARITY_ORDER


def get_rarity_emoji(rarity: str | None) -> str:
    return RARITY_EMOJI.get(str(rarity or ""), "🎴")


def get_rarity_exp(rarity: str | None) -> int:
    return int(RARITY_EXP.get(str(rarity or "Common"), 1))


def normalize_rarity(raw: str | None) -> str | None:
    text = str(raw or "").strip().lower()
    for rarity in RARITY_ORDER:
        if rarity.lower() == text:
            return rarity
    return None
