from __future__ import annotations

from pymongo import ReturnDocument
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config import CLAIM_DAILY_LIMIT, CLAIM_PREFIX_MIN_LENGTH
from database.mongodb import get_db
from utils.cooldown import should_ignore_update
from utils.claim_stats import log_claim_event, release_daily_claim, reserve_daily_claim
from utils.db_helpers import add_card_to_user, ensure_group, ensure_user, get_photo_by_card_id
from utils.parser import normalized_search_name
from utils.rarity import get_rarity_emoji
from utils.text import escape_html, mention_user, utcnow


async def bika_claim_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    if await should_ignore_update(update):
        return
    guess_raw = " ".join(context.args).strip()
    if not guess_raw:
        await update.message.reply_text("Usage: /bika <character name>")
        return

    await ensure_user(update.effective_user)
    group = await ensure_group(update.effective_chat)
    active = (group or {}).get("activeDrop")
    if not active or not active.get("cardId"):
        await update.message.reply_text("❌ No character is available right now.")
        return

    if active.get("isClaimed"):
        caught_by = active.get("claimedByName") or "Someone"
        await update.message.reply_html(
            f"❌ <b>CHARACTER ALREADY CAUGHT</b>\n\nCaught by: {escape_html(caught_by)}\n🥤 Wait for new character to spawn."
        )
        return

    guess = normalized_search_name(guess_raw)
    target = str(active.get("normalizedName", ""))
    is_match = len(guess) >= CLAIM_PREFIX_MIN_LENGTH and (guess == target or target.startswith(guess))
    if not is_match:
        await update.message.reply_text(f"❌ CHARACTER NAME {guess_raw.lower()} IS INCORRECT\n\n⬆️ CHARACTER is still available.")
        return

    reservation = await reserve_daily_claim(update.effective_user.id)
    if not reservation.get("ok"):
        await update.message.reply_text(
            f"❌ Daily catch limit reached.\n"
            f"Myanmar/Yangon date: {reservation.get('date')}\n"
            f"Used: {reservation.get('used')}/{CLAIM_DAILY_LIMIT}\n"
            f"Remaining: 0"
        )
        return

    claimer_name = " ".join([update.effective_user.first_name or "", update.effective_user.last_name or ""]).strip()
    updated = await get_db().groups.find_one_and_update(
        {
            "groupId": int(update.effective_chat.id),
            "activeDrop.cardId": str(active.get("cardId")),
            "activeDrop.isClaimed": False,
        },
        {
            "$set": {
                "activeDrop.isClaimed": True,
                "activeDrop.claimedByUserId": int(update.effective_user.id),
                "activeDrop.claimedByName": claimer_name or update.effective_user.username or str(update.effective_user.id),
                "updatedAt": utcnow(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    if not updated or int(updated.get("activeDrop", {}).get("claimedByUserId", 0)) != int(update.effective_user.id):
        await release_daily_claim(update.effective_user.id, reservation.get("date"))
        latest = await get_db().groups.find_one({"groupId": int(update.effective_chat.id)})
        caught = latest.get("activeDrop", {}).get("claimedByName", "Someone") if latest else "Someone"
        await update.message.reply_html(
            f"❌ <b>CHARACTER ALREADY CAUGHT</b>\n\nCaught by: {escape_html(caught)}\n🥤 Wait for new character to spawn."
        )
        return

    photo_doc = await get_photo_by_card_id(active.get("cardId"))
    if not photo_doc:
        await release_daily_claim(update.effective_user.id, reservation.get("date"))
        await update.message.reply_text("❌ Drop data missing.")
        return

    await add_card_to_user(update.effective_user, photo_doc, 1)
    await log_claim_event(update.effective_user, update.effective_chat, photo_doc, reservation.get("date"))
    await update.message.reply_html(
        "🎉 <b>YOU GOT A NEW CHARACTER!</b>\n\n"
        f"👤 Claimed by: {mention_user(update.effective_user)}\n"
        f"{get_rarity_emoji(photo_doc.get('rarity'))} Name: <b>{escape_html(photo_doc.get('name'))}</b>\n"
        f"🆔 ID: <b>{escape_html(photo_doc.get('cardId'))}</b>\n"
        f"🏷 RARITY: <b>{escape_html(photo_doc.get('rarity'))}</b>\n"
        f"🌴 ANIME: <b>{escape_html(photo_doc.get('anime'))}</b>\n\n"
        "❄️ CHECK YOUR /harem !"
    )


def register_claim_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("bika", bika_claim_cmd))
