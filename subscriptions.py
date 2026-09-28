"""Mandatory Telegram subscription enforcement."""
from __future__ import annotations
from database import subscriptions, setting_get


def mandatory_missing(bot, user_id: int) -> list[tuple]:
    if setting_get("SUBS_ENABLED") != "ON":
        return []
    missing = []
    for row in subscriptions():
        _, title, target, url, is_telegram = row
        if not is_telegram:
            continue
        try:
            member = bot.get_chat_member(target, int(user_id))
        except Exception:
            missing.append(row)
            continue
        status = getattr(member, "status", "")
        if status in {"left", "kicked"}:
            missing.append(row)
        elif status == "restricted" and not getattr(member, "is_member", False):
            missing.append(row)
    return missing


def subscription_markup(types, rows):
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for _, title, _, url, _ in rows:
        if url:
            keyboard.add(types.InlineKeyboardButton(f"📢 {title}", url=url))
    keyboard.add(types.InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="sub_check"))
    return keyboard


def show_subscription_wall(bot, types, message) -> bool:
    missing = mandatory_missing(bot, message.from_user.id)
    if not missing:
        return False
    bot.reply_to(message, "🔒 يجب الاشتراك بالقنوات المطلوبة أولاً، ثم اضغط تحقق من الاشتراك.", reply_markup=subscription_markup(types, missing))
    return True
