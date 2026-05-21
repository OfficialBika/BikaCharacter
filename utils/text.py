from __future__ import annotations

import html
from datetime import datetime, timezone
from telegram import User


def escape_html(text: object = "") -> str:
    return html.escape(str(text or ""), quote=False)


def full_name_from_tg(user: User | None) -> str:
    if not user:
        return ""
    return " ".join([user.first_name or "", user.last_name or ""]).strip()


def full_name_from_doc(user_doc: dict | None) -> str:
    if not user_doc:
        return ""
    return " ".join([user_doc.get("firstName", ""), user_doc.get("lastName", "")]).strip()


def mention_user(user: User) -> str:
    name = full_name_from_tg(user) or user.username or f"User {user.id}"
    return f'<a href="tg://user?id={user.id}">{escape_html(name)}</a>'


def mention_user_doc(user_doc: dict) -> str:
    user_id = int(user_doc.get("userId", 0) or 0)
    name = full_name_from_doc(user_doc) or user_doc.get("username") or f"User {user_id}"
    return f'<a href="tg://user?id={user_id}">{escape_html(name)}</a>'


def safe_chat_title(chat) -> str:
    return getattr(chat, "title", None) or getattr(chat, "username", None) or str(getattr(chat, "id", "Unknown"))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uptime_text(seconds: int) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins, sec = divmod(rem, 60)
    return f"{days}d {hours}h {mins}m {sec}s"


def level_from_exp(exp: int | float) -> dict:
    total = max(0, int(exp or 0))
    level = 1
    need = 30
    used = 0
    while total >= used + need:
        used += need
        level += 1
        need = 30 + (level - 1) * 20
    current = total - used
    percent = 0 if need <= 0 else int((current / need) * 100)
    return {"level": level, "current": current, "need": need, "percent": max(0, min(100, percent))}


def progress_bar(percent: int, size: int = 10) -> str:
    filled = max(0, min(size, round((percent / 100) * size)))
    return "█" * filled + "░" * (size - filled)
