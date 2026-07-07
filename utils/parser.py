from __future__ import annotations

import re
from typing import Optional

from config import RARITY_ORDER, LIMITED_RARITY_NAME
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
    # Remove bracket/parenthesis decorations such as [💠], (Ver. 2), etc.
    text = re.sub(r"\[[^\]]*]", " ", text)
    text = re.sub(r"\([^)]*\)", " ", text)
    # Convert punctuation/symbols to spaces. This makes "No.2" searchable as "no 2".
    text = re.sub(r"[^a-z0-9\s\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _compact(text: str = "") -> str:
    """Normalize and remove spaces/hyphens for loose matching."""
    return re.sub(r"[\s\-]+", "", normalized_search_name(text))


def is_character_name_match(guess_text: str = "", target_name: str = "", min_length: int = 3) -> bool:
    """Return True when a claim guess is a valid full-name or hint match.

    Exact normalized full-name matches are accepted before the minimum hint-length
    rule. This allows genuinely short names such as "C.c." / "CC" while keeping
    one- or two-character partial hints blocked when min_length=3.
    """
    guess = normalized_search_name(guess_text)
    target = normalized_search_name(target_name)

    if not guess or not target:
        return False

    compact_guess = _compact(guess)
    compact_target = _compact(target)

    # Exact full-name match MUST be checked before min_length.
    if guess == target or compact_guess == compact_target:
        return True

    # Minimum length applies only to partial/prefix/hint matching.
    if len(compact_guess) < int(min_length):
        return False

    # Prefix from the beginning of the full name.
    if target.startswith(guess) or compact_target.startswith(compact_guess):
        return True

    # Phrase match anywhere, e.g. "no 2" inside "yorha no 2 type b".
    if f" {guess} " in f" {target} ":
        return True

    target_words = set(target.split())
    guess_words = guess.split()

    # Single-word hint match.
    if len(guess_words) == 1 and guess_words[0] in target_words:
        return True

    # Multi-word hint: all words must exist in the target, e.g. "type yorha".
    if len(guess_words) > 1 and all(word in target_words for word in guess_words):
        return True

    return False


def parse_add_caption(caption: str = "") -> Optional[dict]:
    """Parse /add captions.

    Supported formats:
      /add 12 | Yelan | Legendary | Genshin Impact   -> update/save explicit numeric ID 12
      /add 1a | Special | Limited | Bika Limited      -> save owner-only limited card ID 1a
      /add Yelan | Legendary | Genshin Impact        -> new normal card, ID auto-assigned

    Returns `_cardIdProvided=True` when the caption included an explicit ID.
    For auto-ID new cards, cardId is an empty string and the add handler assigns it.
    """
    first_line = str(caption or "").split("\n")[0].strip()
    if not first_line.lower().startswith("/add"):
        return None

    body = first_line[4:].strip()
    parts = [x.strip() for x in body.split("|") if x.strip()]

    card_id = ""
    name = ""
    rarity_raw = ""
    anime = ""
    card_id_provided = False

    if len(parts) >= 4 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", parts[0]):
        card_id, name, rarity_raw, anime = parts[:4]
        card_id_provided = True
    elif len(parts) >= 3:
        name, rarity_raw, anime = parts[:3]
    else:
        return None

    rarity = normalize_rarity(rarity_raw)
    if not name or not anime or rarity is None:
        return None

    return {
        "cardId": str(card_id).strip(),
        "name": name.strip(),
        "normalizedName": normalized_search_name(name),
        "rarity": rarity,
        "anime": anime.strip(),
        "_cardIdProvided": card_id_provided,
    }


def parse_forward_character(raw_text: str = "") -> Optional[dict]:
    """Parse forwarded character captions.

    Forward captions may contain an original source ID such as "131: Yelan".
    This bot intentionally ignores that original ID and uses the bot database's
    own auto-ID system. Only anime, name, and rarity are imported.
    """
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
            if any(
                skip in lower
                for skip in (
                    "owo! check out this character",
                    "caught how many times",
                    "rarity",
                )
            ):
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
    }
