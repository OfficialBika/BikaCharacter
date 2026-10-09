from __future__ import annotations

from typing import Any

from config import CARD_LOG_CHANNEL_ID
from utils.text import escape_html


async def send_card_action_log(bot, action: str, actor_id: int, details: dict[str, Any]) -> bool:
    """Post a best-effort audit message for a successful card/catalog mutation.

    CARD_LOG_CHANNEL_ID falls back to the existing GROUP_LOG_CHANNEL_ID, so
    deployments can keep using their current log channel without changing .env.
    Logging failures must never turn a successful database operation into a
    reported command failure.
    """
    if not CARD_LOG_CHANNEL_ID:
        print("CARD_LOG_CHANNEL_ID is not set; skipped card action log.", flush=True)
        return False

    lines = [
        "🧾 <b>BIKA CARD ACTION LOG</b>",
        f"<b>Action:</b> {escape_html(action)}",
        f"<b>Actor ID:</b> <code>{int(actor_id)}</code>",
    ]
    for key, value in list(details.items())[:30]:
        rendered = "-" if value is None or value == "" else str(value)
        lines.append(f"<b>{escape_html(key)}:</b> {escape_html(rendered)}")
    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3850] + "\n… (details truncated)"

    try:
        await bot.send_message(
            chat_id=CARD_LOG_CHANNEL_ID,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return True
    except Exception as exc:
        print(f"CARD ACTION LOG FAILED: {exc!r}", flush=True)
        return False
