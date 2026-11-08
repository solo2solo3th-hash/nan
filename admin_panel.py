"""Backward-compatible admin UI facade.

The implementation is intentionally split: developer-only controls live in
`developer_panel.py`, while general admin controls live here.
"""
from __future__ import annotations
from telebot import types
from config import DEVELOPER_ID
from database import PERMISSIONS, PERMISSION_GROUPS, get_permission_state, list_sudos
from developer_panel import (
    admin_text as _admin_text,
    admins_menu as _admins_menu,
    developer_markup as _developer_markup,
    permission_markup as _permission_markup,
    permissions_select_markup as _permissions_select_markup,
)


def _admin_button(text, *args, emoji_key=None, **kwargs):
    from database import setting_get
    key = emoji_key or str(kwargs.get("callback_data") or text).split(":", 1)[0].upper().replace("-", "_")
    emoji_id = (setting_get(f"EMOJI_BTN_{key}") or "").strip()
    if emoji_id:
        kwargs["icon_custom_emoji_id"] = emoji_id
    try:
        return types.InlineKeyboardButton(text, *args, **kwargs)
    except TypeError:
        kwargs.pop("icon_custom_emoji_id", None)
        return types.InlineKeyboardButton(text, *args, **kwargs)


def developer_markup():
    return _developer_markup()


def admins_menu():
    return _admins_menu()


def permissions_select_markup():
    return _permissions_select_markup()


def permission_markup(user_id: int):
    return _permission_markup(user_id)


def admin_text() -> str:
    return _admin_text()


def sudo_markup(user_id: int):
    """Build a restricted menu from this admin's actual granular permissions."""
    state = get_permission_state(int(user_id), int(DEVELOPER_ID))
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    if state.get("stats.view"):
        keyboard.add(_admin_button("📊 الإحصائيات", callback_data="adm_stats"))
    if any(state.get(f"users.{key}", False) for key in PERMISSION_GROUPS["users"]):
        keyboard.add(_admin_button("👥 إدارة المستخدمين", callback_data="adm_users"))
    if any(state.get(f"subscriptions.{key}", False) for key in PERMISSION_GROUPS["subscriptions"]):
        keyboard.add(_admin_button("🔒 الاشتراك الإجباري", callback_data="adm_subs"))
    if state.get("broadcast.send"):
        keyboard.add(_admin_button("📣 الإذاعة", callback_data="adm_broadcast"))
    if state.get("admins.view"):
        keyboard.add(_admin_button("👨‍💻 قائمة المشرفين", callback_data="adm_list"))
    keyboard.add(_admin_button("❌ إغلاق", callback_data="adm_close"))
    return keyboard


def playback_markup():
    # Single source of truth: the developer panel owns playback-panel callbacks.
    from developer_panel import playback_settings_markup
    return playback_settings_markup()


def users_markup(user_id: int | None = None):
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    if user_id is None or get_permission_state(user_id, int(DEVELOPER_ID)).get("users.ban"):
        keyboard.add(_admin_button("🚫 حظر مستخدم", callback_data="user_ban"))
    if user_id is None or get_permission_state(user_id, int(DEVELOPER_ID)).get("users.unban"):
        keyboard.add(_admin_button("✅ رفع الحظر", callback_data="user_unban"))
    if user_id is None or get_permission_state(user_id, int(DEVELOPER_ID)).get("users.banned"):
        keyboard.add(_admin_button("📋 المحظورون", callback_data="user_banned"))
    keyboard.add(_admin_button("↩️ الرئيسية", callback_data="adm_home"))
    return keyboard


def subscriptions_markup(user_id: int | None = None):
    state = get_permission_state(user_id, int(DEVELOPER_ID)) if user_id is not None else None
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    if state is None or state.get("subscriptions.add"):
        keyboard.add(_admin_button("➕ إضافة", callback_data="sub_add"))
    if state is None or state.get("subscriptions.remove"):
        keyboard.add(_admin_button("🗑️ حذف", callback_data="sub_remove"))
    if state is None or state.get("subscriptions.view"):
        keyboard.add(_admin_button("📋 القنوات", callback_data="sub_list"))
    if state is None or state.get("subscriptions.toggle"):
        keyboard.add(_admin_button("🔛 تشغيل/إيقاف الإجباري", callback_data="sub_toggle"))
    keyboard.add(_admin_button("↩️ الرئيسية", callback_data="adm_home"))
    return keyboard


def register_admin_handlers(bot, save_user, save_chat, is_admin):
    @bot.message_handler(commands=["admin", "panel", "dev"])
    def open_panel(message):
        save_user(message.from_user)
        save_chat(message.chat)
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "❌ هذا الأمر مخصص للمطور والمشرفين فقط.")
            return
        bot.send_message(message.chat.id, admin_text(), reply_markup=developer_markup())
