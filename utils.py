"""Small Telegram formatting helpers."""
from __future__ import annotations

def duration_text(seconds: int | float | None) -> str:
    try:
        total = max(0, int(seconds or 0))
    except (TypeError, ValueError):
        total = 0
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def safe_delete(bot, chat_id: int, message_id: int | None) -> None:
    if message_id is None:
        return
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass


def edit_text(bot, chat_id: int, message_id: int, text: str, reply_markup=None):
    return bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup)


def alert(bot, call, text: str, show: bool = False) -> None:
    try:
        bot.answer_callback_query(call.id, text, show_alert=show)
    except Exception:
        pass
