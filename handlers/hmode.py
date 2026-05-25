from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from database.mongodb import get_db
from utils.cooldown import should_ignore_update
from utils.db_helpers import ensure_user
from utils.text import utcnow
from utils.i18n import t


async def hmode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await should_ignore_update(update):
        return
    await ensure_user(update.effective_user)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(t("hmode_button_default"), callback_data=f"hmode:{update.effective_user.id}:default"),
                InlineKeyboardButton(t("hmode_button_detailed"), callback_data=f"hmode:{update.effective_user.id}:detailed"),
            ],
            [InlineKeyboardButton(t("hmode_button_reset"), callback_data=f"hmode:{update.effective_user.id}:reset")],
        ]
    )
    await update.message.reply_text(t("hmode_intro"), reply_markup=keyboard)


async def hmode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, user_id_raw, choice = query.data.split(":", 2)
    user_id = int(user_id_raw)
    if query.from_user.id != user_id:
        await query.answer(t("not_your_action"), show_alert=True)
        return
    if choice == "reset":
        choice = "default"
    if choice not in ("default", "detailed"):
        await query.answer(t("invalid_mode"), show_alert=True)
        return
    await get_db().users.update_one({"userId": user_id}, {"$set": {"haremView": choice, "updatedAt": utcnow()}})
    await query.edit_message_text(t("hmode_set", mode=choice.upper()))
    await query.answer(t("updated"))


def register_hmode_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("hmode", hmode_cmd))
    app.add_handler(CallbackQueryHandler(hmode_callback, pattern=r"^hmode:\d+:(default|detailed|reset)$"))
