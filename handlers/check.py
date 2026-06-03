from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from utils.cooldown import should_ignore_update
from utils.db_helpers import get_photo_by_card_id, global_card_stats
from utils.rarity import get_rarity_emoji
from utils.text import escape_html, mention_user_doc
from utils.i18n import t


def _media_type_from_doc(card: dict) -> str:
    media_type = str(card.get("mediaType") or "").strip().lower()
    mime_type = str(card.get("mimeType") or "").strip().lower()
    file_name = str(card.get("fileName") or "").strip().lower()

    if media_type in {"photo", "video", "animation", "gif", "document"}:
        return media_type

    if mime_type.startswith("video/"):
        return "video"
    if mime_type == "image/gif":
        return "animation"
    if file_name.endswith((".mp4", ".mov", ".m4v", ".webm", ".mkv")):
        return "video"
    if file_name.endswith(".gif"):
        return "animation"
    if file_name.endswith((".jpg", ".jpeg", ".png", ".webp")):
        return "photo"

    return "photo"


def _candidate_media_types(card: dict) -> list[str]:
    media_type = _media_type_from_doc(card)

    if media_type == "video":
        return ["video", "animation", "document", "photo"]
    if media_type in {"animation", "gif"}:
        return ["animation", "video", "document", "photo"]
    if media_type == "document":
        return ["document", "video", "animation", "photo"]

    # Old cards may not have mediaType. Try photo first, then video fallback.
    return ["photo", "video", "animation", "document"]


async def _reply_card_media(update: Update, card: dict, caption: str) -> None:
    msg = update.effective_message
    file_id = str(card.get("fileId") or "")
    if not msg:
        return
    if not file_id:
        await msg.reply_html(caption, disable_web_page_preview=True)
        return

    last_error: Exception | None = None
    for media_type in _candidate_media_types(card):
        try:
            if media_type == "video":
                await msg.reply_video(video=file_id, caption=caption, parse_mode="HTML")
                return
            if media_type in {"animation", "gif"}:
                await msg.reply_animation(animation=file_id, caption=caption, parse_mode="HTML")
                return
            if media_type == "document":
                await msg.reply_document(document=file_id, caption=caption, parse_mode="HTML")
                return

            await msg.reply_photo(photo=file_id, caption=caption, parse_mode="HTML")
            return
        except Exception as exc:
            last_error = exc
            continue

    print("CHECK SEND MEDIA ERROR:", repr(last_error))
    await msg.reply_html(caption, disable_web_page_preview=True)


async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await should_ignore_update(update):
        return
    if not context.args:
        await update.message.reply_text(t("check_usage"))
        return

    card_id = str(context.args[0]).strip()
    photo = await get_photo_by_card_id(card_id)
    if not photo:
        await update.message.reply_text(t("check_not_found", card_id=card_id))
        return

    stats = await global_card_stats(card_id)
    lines = [
        t("card_check_header"),
        "",
        f"<b>{escape_html(photo.get('anime'))}</b>",
        f"<b>{escape_html(photo.get('cardId'))}:</b> {escape_html(photo.get('name'))}",
        t("rarity_line", emoji=get_rarity_emoji(photo.get("rarity")), rarity=escape_html(photo.get("rarity"))),
        "",
        t("caught_globally", total=stats["totalOwned"]),
        "",
        t("top10_catchers"),
    ]
    if not stats["topCatchers"]:
        lines.append(t("no_catch_data"))
    else:
        for catcher in stats["topCatchers"]:
            lines.append(f"➳ {mention_user_doc(catcher)} x{catcher.get('count', 0)}")

    await _reply_card_media(update, photo, "\n".join(lines))


def register_check_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("check", check_cmd))
