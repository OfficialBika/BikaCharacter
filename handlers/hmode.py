from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from config import RARITY_ORDER
from database.mongodb import get_db
from utils.cooldown import should_ignore_update
from utils.db_helpers import ensure_user
from utils.rarity import get_rarity_button_emoji, get_rarity_emoji
from utils.text import utcnow
from utils.i18n import t
from utils.buttons import action_button, rarity_button


async def hmode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await should_ignore_update(update):
        return
    await ensure_user(update.effective_user)
    user_id = int(update.effective_user.id)

    keyboard = InlineKeyboardMarkup(
        [
            [
                action_button(t("hmode_sort_by_rarity"), "primary", callback_data=f"hmode:{user_id}:rarity_menu"),
                action_button(t("hmode_sort_by_anime"), "primary", callback_data=f"hmode:{user_id}:anime"),
            ],
            [action_button(t("hmode_close"), "danger", callback_data=f"hmode:{user_id}:close")],
        ]
    )
    await update.message.reply_text(t("hmode_choose_sort"), parse_mode="HTML", reply_markup=keyboard)


def _rarity_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for rarity in RARITY_ORDER:
        rows.append(
            [
                rarity_button(
                    t("hmode_rarity_button", emoji=get_rarity_button_emoji(rarity), rarity=rarity),
                    rarity,
                    "primary",
                    callback_data=f"hmode:{user_id}:rarity:{rarity}",
                )
            ]
        )
    rows.append([action_button(t("hmode_back"), "primary", callback_data=f"hmode:{user_id}:main")])
    rows.append([action_button(t("hmode_close"), "danger", callback_data=f"hmode:{user_id}:close")])
    return InlineKeyboardMarkup(rows)


def _main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                action_button(t("hmode_sort_by_rarity"), "primary", callback_data=f"hmode:{user_id}:rarity_menu"),
                action_button(t("hmode_sort_by_anime"), "primary", callback_data=f"hmode:{user_id}:anime"),
            ],
            [action_button(t("hmode_close"), "danger", callback_data=f"hmode:{user_id}:close")],
        ]
    )


async def hmode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    parts = query.data.split(":", 3)
    if len(parts) < 3:
        await query.answer(t("invalid_mode"), show_alert=True)
        return

    _, user_id_raw, action = parts[:3]
    user_id = int(user_id_raw)
    if int(query.from_user.id) != user_id:
        await query.answer(t("not_your_action"), show_alert=True)
        return

    if action == "close":
        try:
            await query.message.delete()
        except Exception:
            await query.edit_message_text(t("cancelled"))
        await query.answer(t("cancelled"))
        return

    if action == "main":
        await query.edit_message_text(t("hmode_choose_sort"), parse_mode="HTML", reply_markup=_main_keyboard(user_id))
        await query.answer()
        return

    if action == "rarity_menu":
        await query.edit_message_text(t("hmode_choose_rarity"), parse_mode="HTML", reply_markup=_rarity_keyboard(user_id))
        await query.answer()
        return

    now = utcnow()
    if action == "anime":
        await get_db().users.update_one(
            {"userId": user_id},
            {
                "$set": {
                    "haremSort": "anime",
                    "haremRarity": "",
                    "haremView": "anime",
                    "updatedAt": now,
                }
            },
            upsert=True,
        )
        await query.edit_message_text(t("hmode_set_anime"), parse_mode="HTML")
        await query.answer(t("updated"))
        return

    if action == "rarity":
        if len(parts) < 4:
            await query.answer(t("invalid_mode"), show_alert=True)
            return
        rarity = parts[3]
        if rarity not in RARITY_ORDER:
            await query.answer(t("invalid_mode"), show_alert=True)
            return
        await get_db().users.update_one(
            {"userId": user_id},
            {
                "$set": {
                    "haremSort": "rarity",
                    "haremRarity": rarity,
                    "haremView": "rarity",
                    "updatedAt": now,
                }
            },
            upsert=True,
        )
        await query.edit_message_text(t("hmode_set_rarity", emoji=get_rarity_emoji(rarity), rarity=rarity), parse_mode="HTML")
        await query.answer(t("updated"))
        return

    # Backward compatibility with old buttons, if an old inline keyboard is still open.
    if action in ("default", "detailed", "reset"):
        await get_db().users.update_one(
            {"userId": user_id},
            {"$set": {"haremSort": "anime", "haremRarity": "", "haremView": "anime", "updatedAt": now}},
            upsert=True,
        )
        await query.edit_message_text(t("hmode_set_anime"), parse_mode="HTML")
        await query.answer(t("updated"))
        return

    await query.answer(t("invalid_mode"), show_alert=True)


def register_hmode_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("hmode", hmode_cmd))
    app.add_handler(CallbackQueryHandler(hmode_callback, pattern=r"^hmode:\d+:.+$"))
