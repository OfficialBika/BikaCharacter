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


def parse_add_caption(caption: str = "") -> Optional[dict]:
    """Parse /add captions.

    Supported formats:
      /add 12 | Yelan | Legendary | Genshin Impact   -> update/save explicit ID 12
      /add Yelan | Legendary | Genshin Impact        -> new card, ID auto-assigned

    Returns `_cardIdProvided=True` when the caption included a numeric ID.
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

    if len(parts) >= 4 and re.fullmatch(r"\d+", parts[0]):
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
    text = str(raw_text or "").replace("\r", "").replace("\u00a0", " ").strip()
    if not text:
        return None

    lines = [x.strip() for x in text.split("\n") if x.strip()]
    anime = ""
    card_id = ""
    name = ""
    rarity = ""

    id_line_index = -1
    for i, line in enumerate(lines):
        match = re.match(r"^(\d+)\s*[:：]\s*(.+)$", line)
        if match:
            card_id = match.group(1).strip()
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

    if not anime or not card_id or not name or not rarity:
        return None

    return {
    "anime": anime,
    "cardId": "",
    "name": name,
    "normalizedName": normalized_search_name(name),
    "rarity": rarity,
    "_cardIdProvided": False,
    }
