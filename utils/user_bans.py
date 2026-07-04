from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config import CLAIM_TIMEZONE
from database.mongodb import get_db
from utils.text import utcnow


def _as_utc(value: datetime | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_timezone():
    try:
        return ZoneInfo(str(CLAIM_TIMEZONE or "Asia/Yangon"))
    except Exception:
        return timezone.utc


def format_release_date(value: datetime | None) -> str:
    if value is None:
        return "Lifetime"
    dt = _as_utc(value)
    if dt is None:
        return "Unknown"
    return dt.astimezone(_safe_timezone()).strftime("%d %B %Y, %I:%M %p")


def build_ban_notice(ban: dict) -> str:
    reason = str((ban or {}).get("reason") or "No reason provided.").strip()
    release_date = format_release_date((ban or {}).get("expiresAt"))
    return (
        "🚫 You are banned from using this bot.\n\n"
        f"Release Date - {release_date}\n"
        f"Reason - {reason}"
    )


async def log_owner_action(
    owner_id: int,
    action: str,
    user_id: int,
    details: dict | None = None,
) -> bool:
    try:
        await get_db().owner_action_logs.insert_one(
            {
                "ownerId": int(owner_id),
                "action": str(action),
                "userId": int(user_id),
                "details": dict(details or {}),
                "createdAt": utcnow(),
            }
        )
        return True
    except Exception as exc:
        print("OWNER ACTION LOG ERROR:", repr(exc), flush=True)
        return False


async def get_active_ban(user_id: int) -> dict | None:
    db = get_db()
    user_id = int(user_id)
    ban = await db.user_bans.find_one({"userId": user_id, "active": True})
    if not ban:
        return None

    expires_at = _as_utc(ban.get("expiresAt"))
    if expires_at is not None and expires_at <= utcnow():
        now = utcnow()
        result = await db.user_bans.update_one(
            {"_id": ban["_id"], "active": True},
            {
                "$set": {
                    "active": False,
                    "expiredAt": now,
                    "updatedAt": now,
                }
            },
        )
        if result.modified_count:
            try:
                await log_owner_action(
                    owner_id=0,
                    action="auto_unban_expired",
                    user_id=user_id,
                    details={"expiredAt": expires_at},
                )
            except Exception:
                pass
        return None

    return ban


async def set_user_ban(
    user_id: int,
    banned_by: int,
    reason: str,
    *,
    days: int | None = None,
    target_username: str = "",
    target_name: str = "",
) -> dict:
    db = get_db()
    now = utcnow()
    user_id = int(user_id)

    if days is None:
        ban_type = "lifetime"
        expires_at = None
    else:
        days = int(days)
        if days <= 0:
            raise ValueError("days must be greater than zero")
        ban_type = "timed"
        expires_at = now + timedelta(days=days)

    clean_reason = str(reason or "Owner Decision").strip()[:500] or "Owner Decision"

    await db.user_bans.update_one(
        {"userId": user_id},
        {
            "$set": {
                "userId": user_id,
                "active": True,
                "banType": ban_type,
                "reason": clean_reason,
                "bannedAt": now,
                "bannedBy": int(banned_by),
                "expiresAt": expires_at,
                "targetUsername": str(target_username or "").strip(),
                "targetName": str(target_name or "").strip(),
                "updatedAt": now,
            },
            "$unset": {
                "unbannedAt": "",
                "unbannedBy": "",
                "expiredAt": "",
            },
            "$setOnInsert": {
                "createdAt": now,
            },
        },
        upsert=True,
    )

    return await db.user_bans.find_one({"userId": user_id})


async def unban_user(user_id: int, unbanned_by: int) -> tuple[bool, dict | None]:
    db = get_db()
    user_id = int(user_id)
    current = await db.user_bans.find_one({"userId": user_id, "active": True})
    if not current:
        return False, None

    now = utcnow()
    result = await db.user_bans.update_one(
        {"_id": current["_id"], "active": True},
        {
            "$set": {
                "active": False,
                "unbannedAt": now,
                "unbannedBy": int(unbanned_by),
                "updatedAt": now,
            }
        },
    )
    return result.modified_count > 0, current
