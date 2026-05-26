from __future__ import annotations

from telegram import ChatMemberUpdated, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import Application, ChatMemberHandler, ContextTypes

from database.mongodb import get_db
from utils.db_helpers import ensure_group
from utils.text import utcnow


MIN_GROUP_MEMBERS = 30
LEAVE_MESSAGE = "This group can't afford me. I'm leaving now...."

ACTIVE_STATUSES = {
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.OWNER,
}

INACTIVE_STATUSES = {
    ChatMemberStatus.LEFT,
    ChatMemberStatus.BANNED,
}


async def _get_member_count(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> int:
    try:
        return int(await context.bot.get_chat_member_count(chat_id))
    except Exception as exc:
        print("CHECKGP MEMBER COUNT ERROR:", repr(exc))
        return 0


async def check_group_requirements(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    member_update: ChatMemberUpdated | None = update.my_chat_member
    if not member_update:
        return

    chat = member_update.chat
    if chat.type not in ("group", "supergroup"):
        return

    old_status = member_update.old_chat_member.status
    new_status = member_update.new_chat_member.status

    # Bot group ထဲအသစ်ဝင်တဲ့အချိန်ပဲ စစ်မယ်
    if old_status not in INACTIVE_STATUSES or new_status not in ACTIVE_STATUSES:
        return

    await ensure_group(chat)

    member_count = await _get_member_count(context, int(chat.id))

    if member_count >= MIN_GROUP_MEMBERS:
        await get_db().groups.update_one(
            {"groupId": int(chat.id)},
            {
                "$set": {
                    "checkgpPassed": True,
                    "checkgpMemberCount": member_count,
                    "checkgpCheckedAt": utcnow(),
                    "updatedAt": utcnow(),
                }
            },
            upsert=True,
        )
        return

    await get_db().groups.update_one(
        {"groupId": int(chat.id)},
        {
            "$set": {
                "checkgpPassed": False,
                "checkgpMemberCount": member_count,
                "checkgpCheckedAt": utcnow(),
                "checkgpLeaveReason": f"members={member_count}/{MIN_GROUP_MEMBERS}",
                "updatedAt": utcnow(),
            }
        },
        upsert=True,
    )

    try:
        await context.bot.send_message(
            chat_id=int(chat.id),
            text=LEAVE_MESSAGE,
        )
    except Exception as exc:
        print("CHECKGP SEND LEAVE MESSAGE ERROR:", repr(exc))

    try:
        await context.bot.leave_chat(int(chat.id))
    except Exception as exc:
        print("CHECKGP LEAVE CHAT ERROR:", repr(exc))


def register_checkgp_handlers(app: Application) -> None:
    app.add_handler(ChatMemberHandler(check_group_requirements, ChatMemberHandler.MY_CHAT_MEMBER))
