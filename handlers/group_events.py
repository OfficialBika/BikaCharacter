from __future__ import annotations

from html import escape

from telegram import Chat, ChatMemberUpdated, Update, User
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.ext import Application, ChatMemberHandler, ContextTypes

from config import CREATE_INVITE_LINK_FOR_PRIVATE_GROUPS, GROUP_LOG_CHANNEL_ID
from utils.db_helpers import ensure_group
from utils.text import safe_chat_title

ACTIVE_STATUSES = {
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.OWNER,
}
INACTIVE_STATUSES = {
    ChatMemberStatus.LEFT,
    ChatMemberStatus.BANNED,
}


def html_user_link(user: User | None) -> str:
    if not user:
        return "Unknown"
    name = " ".join([user.first_name or "", user.last_name or ""]).strip() or user.username or str(user.id)
    return f'<a href="tg://user?id={int(user.id)}">{escape(name)}</a>'


def plain_chat_name(chat: Chat) -> str:
    return safe_chat_title(chat) or str(chat.id)


async def build_group_link(context: ContextTypes.DEFAULT_TYPE, chat: Chat) -> str:
    title = escape(plain_chat_name(chat))
    username = getattr(chat, "username", None)
    if username:
        return f'<a href="https://t.me/{escape(username)}">{title}</a>'

    if CREATE_INVITE_LINK_FOR_PRIVATE_GROUPS:
        try:
            invite = await context.bot.create_chat_invite_link(
                chat_id=chat.id,
                name="BIKA Bot group log link",
                creates_join_request=False,
            )
            if invite and invite.invite_link:
                return f'<a href="{escape(invite.invite_link)}">{title}</a>'
        except Exception as exc:
            print("CREATE GROUP INVITE LINK FAILED:", repr(exc))

    return f"{title} (private/no public link)"


async def send_bot_added_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    member_update: ChatMemberUpdated | None = update.my_chat_member
    if not member_update:
        return

    chat = member_update.chat
    if chat.type not in ("group", "supergroup"):
        return

    old_status = member_update.old_chat_member.status
    new_status = member_update.new_chat_member.status

    # Trigger only when the bot becomes active in a group.
    if old_status not in INACTIVE_STATUSES or new_status not in ACTIVE_STATUSES:
        return

    await ensure_group(chat)

    if not GROUP_LOG_CHANNEL_ID:
        print("GROUP_LOG_CHANNEL_ID is not set; skipped bot-added log.")
        return

    adder = member_update.from_user
    group_name = plain_chat_name(chat)
    group_link = await build_group_link(context, chat)
    adder_link = html_user_link(adder)
    adder_id = int(adder.id) if adder else "Unknown"

    text = (
        "🤖 <b>Bot Added To New Group</b>\n\n"
        f"<b>Group Name:</b> {escape(group_name)}\n"
        f"<b>Group ID :</b> <code>{int(chat.id)}</code>\n"
        f"<b>Group :</b> {group_link}\n"
        f"<b>Added By :</b> {adder_link}\n"
        f"<b>Adder ID :</b> <code>{adder_id}</code>"
    )

    try:
        await context.bot.send_message(
            chat_id=GROUP_LOG_CHANNEL_ID,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        print("SEND GROUP LOG FAILED:", repr(exc))


def register_group_event_handlers(app: Application) -> None:
    app.add_handler(ChatMemberHandler(send_bot_added_log, ChatMemberHandler.MY_CHAT_MEMBER))
