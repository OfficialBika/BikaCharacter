from __future__ import annotations

import re
from typing import Optional

from config import RARITY_ORDER
from utils.rarity import normalize_rarity


def normalize_name(text: str = "") -> str:
    return (
        str(text or "")
        .lower()
        .strip()
        .replace("\u00a0", " ")
        .replace("’", "")
        .replace("'", "")
    )


def normalized_search_name(text: str = "") -> str:
    text = normalize_name(text)
    text = re.sub(r"\[[^\]]*]", " ", text)
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[^a-z0-9\s\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _compact(text: str = "") -> str:
    return re.sub(r"[\s\-]+", "", normalized_search_name(text))


def is_character_name_match(guess_text: str = "", target_name: str = "", min_length: int = 3) -> bool:
    guess = normalized_search_name(guess_text)
    target = normalized_search_name(target_name)
    if not guess or not target:
        return False
    compact_guess = _compact(guess)
    compact_target = _compact(target)
    if guess == target or compact_guess == compact_target:
        return True
    if len(compact_guess) < int(min_length):
        return False
    if target.startswith(guess) or compact_target.startswith(compact_guess):
        return True
    if f" {guess} " in f" {target} ":
        return True
    target_words = set(target.split())
    guess_words = guess.split()
    if len(guess_words) == 1 and guess_words[0] in target_words:
        return True
    if len(guess_words) > 1 and all(word in target_words for word in guess_words):
        return True
    return False


_SHORT_CODES = ("su", "cv", "ca", "dv", "my", "lg", "ra", "un", "co")


def normalize_add_rarity(raw: str = "") -> str | None:
    text = str(raw or "").strip().lower()
    non_limited = [r for r in RARITY_ORDER if str(r).lower() != "limited"]
    aliases = {str(r).lower(): r for r in RARITY_ORDER}
    aliases.update({code: rarity for code, rarity in zip(_SHORT_CODES, reversed(non_limited))})
    return aliases.get(text) or normalize_rarity(raw)


def parse_add_caption(caption: str = "") -> Optional[dict]:
    """Parse /add.

    Supported:
      /add Yelan | Lg | Genshin Impact
      /add Yelan | Legendary | Genshin Impact
      /add Yelan | Lg                  (anime may come from /addmode)
      /add 2 | Yelan | Lg | Genshin Impact
      /add 1a | Special | Limited | Bika Limited
    """
    first_line = str(caption or "").split("\n")[0].strip()
    if not re.match(r"^/add(?:@[^\s]+)?(?:\s|$)", first_line, flags=re.I):
        return None

    body = re.sub(r"^/add(?:@[^\s]+)?", "", first_line, flags=re.I).strip()
    parts = [x.strip() for x in body.split("|") if x.strip()]
    if not parts:
        return None

    card_id = ""
    card_id_provided = False
    if len(parts) >= 3 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", parts[0]) and len(parts) >= 4:
        card_id, name, rarity_raw, anime = parts[:4]
        card_id_provided = True
    else:
        name = parts[0]
        rarity_raw = parts[1] if len(parts) >= 2 else ""
        anime = parts[2] if len(parts) >= 3 else ""

    rarity = normalize_add_rarity(rarity_raw) if rarity_raw else None
    if not name:
        return None

    return {
        "cardId": str(card_id).strip(),
        "name": name.strip(),
        "normalizedName": normalized_search_name(name),
        "rarity": rarity,
        "anime": anime.strip(),
        "_cardIdProvided": card_id_provided,
        "_animeProvided": bool(anime.strip()),
        "_rarityProvided": bool(rarity_raw.strip()),
    }


def parse_update_caption(caption: str = "") -> Optional[dict]:
    """Parse a strict explicit-update caption: /update Name | Rarity | Anime.

    The target card ID is intentionally not parsed from the caption; it comes
    only from the user's active /update ID session.
    """
    first_line = str(caption or "").split("\n")[0].strip()
    if not re.match(r"^/update(?:@[^\\s]+)?(?:\\s|$)", first_line, flags=re.I):
        return None

    body = re.sub(r"^/update(?:@[^\\s]+)?", "", first_line, flags=re.I).strip()
    parts = [part.strip() for part in body.split("|")]
    if len(parts) != 3 or not all(parts):
        return None

    name, rarity_raw, anime = parts
    return {
        "cardId": "",
        "name": name,
        "normalizedName": normalized_search_name(name),
        "rarity": normalize_add_rarity(rarity_raw),
        "anime": anime,
        "_cardIdProvided": False,
        "_animeProvided": True,
        "_rarityProvided": True,
    }


def parse_forward_character(raw_text: str = "") -> Optional[dict]:
    text = str(raw_text or "").replace("\r", "").replace("\u00a0", " ").strip()
    if not text:
        return None

    lines = [x.strip() for x in text.split("\n") if x.strip()]
    anime = ""
    original_card_id = ""
    name = ""
    rarity = ""

    id_line_index = -1
    for i, line in enumerate(lines):
        match = re.match(r"^(\d+)\s*[:：]\s*(.+)$", line)
        if match:
            original_card_id = match.group(1).strip()
            name = match.group(2).strip()
            id_line_index = i
            break

    if id_line_index > 0:
        for i in range(id_line_index - 1, -1, -1):
            lower = lines[i].lower()
            if any(skip in lower for skip in ("owo! check out this character", "caught how many times", "rarity")):
                continue
            anime = lines[i].strip()
            break

    for line in lines:
        for r in RARITY_ORDER:
            if re.search(rf"\b{re.escape(r)}\b", line, flags=re.I):
                rarity = r
                break
        if rarity:
            break

    if not anime or not original_card_id or not name or not rarity:
        return None

    return {
        "anime": anime,
        "cardId": "",
        "name": name,
        "normalizedName": normalized_search_name(name),
        "rarity": rarity,
        "_cardIdProvided": False,
        "_animeProvided": True,
    }
