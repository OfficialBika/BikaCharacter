from __future__ import annotations

from telegram.ext import ContextTypes

from config import RARITY_ORDER
from utils.db_helpers import rarity_counts, get_photo_by_card_id
from utils.rarity import get_rarity_emoji
from utils.text import escape_html, level_from_exp, progress_bar


def detect_card_media_type(card: dict) -> str:
    media_type = str(card.get("mediaType") or "").strip().lower()
    if media_type in {"photo", "video", "animation", "document"}:
        return media_type
    if media_type == "gif":
        return "animation"

    mime_type = str(card.get("mimeType") or "").strip().lower()
    file_name = str(card.get("fileName") or "").strip().lower()

    if mime_type.startswith("video/") or file_name.endswith((".mp4", ".mov", ".mkv", ".webm")):
        return "video"
    if mime_type == "image/gif" or file_name.endswith(".gif"):
        return "animation"
    if mime_type and not mime_type.startswith("image/"):
        return "document"
    return "photo"


async def hydrate_card_media(card: dict | None) -> dict | None:
    """Backward-compatible media hydration used by owner control panel."""
    if not card:
        return None

    merged = dict(card)
    card_id = str(merged.get("cardId", "") or "")
    needs_lookup = not merged.get("mediaType") or detect_card_media_type(merged) == "photo"

    if card_id and needs_lookup:
        photo_doc = await get_photo_by_card_id(card_id)
        if photo_doc:
            for key in (
                "fileId",
                "fileUniqueId",
                "mediaType",
                "mimeType",
                "fileName",
            ):
                value = photo_doc.get(key)
                if value:
                    merged[key] = value

    return merged


async def reply_profile_media(message, cover: dict, text: str, reply_markup=None) -> None:
    """Backward-compatible media sender used by /cp owner panel."""
    media_type = detect_card_media_type(cover)
    file_id = str(cover.get("fileId") or "")

    if not file_id:
        await message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
        return

    try:
        if media_type == "video":
            await message.reply_video(
                file_id,
                caption=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        elif media_type == "animation":
            await message.reply_animation(
                file_id,
                caption=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        elif media_type == "document":
            await message.reply_document(
                file_id,
                caption=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        else:
            await message.reply_photo(
                file_id,
                caption=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
    except Exception as exc:
        print("OWNER PROFILE MEDIA ERROR:", repr(exc), flush=True)
        await message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )


def build_profile_text(user_doc: dict, total_photo_count: int) -> str:
    """Legacy text profile used only by owner /cp control panel.

    The public /profile command continues using the new generated-image system.
    """
    cards = list(user_doc.get("cards", []))
    total_owned = sum(int(c.get("count", 0) or 0) for c in cards)
    unique_owned = len(cards)
    harem_percent = (
        unique_owned / int(total_photo_count) * 100
        if int(total_photo_count or 0) > 0
        else 0
    )
    level = level_from_exp(user_doc.get("exp", 0))
    counts = rarity_counts(cards)

    username = " ".join(
        [
            str(user_doc.get("firstName", "") or ""),
            str(user_doc.get("lastName", "") or ""),
        ]
    ).strip()
    username = username or str(user_doc.get("username", "") or "") or "Unknown"
    username = escape_html(username)

    fav = next(
        (
            c
            for c in cards
            if str(c.get("cardId")) == str(user_doc.get("favoriteCardId", ""))
        ),
        None,
    )
    if fav:
        fav_text = (
            f'{escape_html(fav.get("name", "Unknown"))} '
            f'<code>[{escape_html(fav.get("cardId", ""))}]</code>'
        )
    else:
        fav_text = "ɴᴏᴛ ꜱᴇᴛ"

    lines = [
        "🎗 <b>𝐂𝐀𝐓𝐂𝐇𝐄𝐑 𝐏𝐑𝐎𝐅𝐈𝐋𝐄</b> 🎗",
        "━━━━━━━━━━━━━━",
        f"👤 <b>ᴜꜱᴇʀ</b> : {username}",
        f"🆔 <b>ᴜꜱᴇʀ ɪᴅ</b> : <code>{escape_html(user_doc.get('userId'))}</code>",
        "",
        "🎴 <b>𝐂𝐎𝐋𝐋𝐄𝐂𝐓𝐈𝐎𝐍</b>",
        f"├ ᴛᴏᴛᴀʟ : <b>{total_owned}</b> ᴄᴀʀᴅꜱ",
        f"├ ᴜɴɪǫᴜᴇ : <b>{unique_owned}</b>/<b>{int(total_photo_count or 0)}</b>",
        f"└ ʜᴀʀᴇᴍ : <b>{harem_percent:.3f}%</b>",
        "",
        "⚡ <b>𝐋𝐄𝐕𝐄𝐋</b>",
        f"├ ʟᴠʟ : <b>{level['level']}</b>",
        f"└ ᴘʀᴏɢʀᴇꜱꜱ : {progress_bar(level['percent'])}",
        "",
        "💖 <b>𝐅𝐀𝐕𝐎𝐔𝐑𝐈𝐓𝐄</b>",
        f"└ {fav_text}",
        "",
        "🏷 <b>𝐑𝐀𝐑𝐈𝐓𝐘 𝐒𝐓𝐀𝐓𝐒</b>",
    ]

    for rarity in RARITY_ORDER:
        data = counts.get(rarity, {"unique": 0, "total": 0})
        lines.append(
            f"{get_rarity_emoji(rarity)} <b>{escape_html(rarity)}</b> · "
            f"<code>{int(data.get('unique', 0) or 0)}</code> unique / "
            f"<code>{int(data.get('total', 0) or 0)}</code> total"
        )

    lines.append("━━━━━━━━━━━━━━")
    return "\n".join(lines)
