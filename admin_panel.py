"""Backward-compatible admin UI facade.

The implementation is intentionally split: developer-only controls live in
`developer_panel.py`, while general admin controls live here.
"""
from __future__ import annotations
from telebot import types
from config import DEVELOPER_ID
from database import PERMISSIONS, get_permission_state, list_sudos
from developer_panel import (
    admin_text as _admin_text,
    admins_menu as _admins_menu,
    developer_markup as _developer_markup,
    permission_markup as _permission_markup,
    permissions_select_markup as _permissions_select_markup,
)


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


def playback_markup():
    # Single source of truth: the developer panel owns playback-panel callbacks.
    from developer_panel import playback_settings_markup
    return playback_settings_markup()


def users_markup():
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("🚫 حظر مستخدم", callback_data="user_ban"))
    keyboard.add(types.InlineKeyboardButton("✅ رفع الحظر", callback_data="user_unban"))
    keyboard.add(types.InlineKeyboardButton("📋 المحظورون", callback_data="user_banned"))
    keyboard.add(types.InlineKeyboardButton("↩️ الرئيسية", callback_data="adm_home"))
    return keyboard


def subscriptions_markup():
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.row(types.InlineKeyboardButton("➕ إضافة", callback_data="sub_add"), types.InlineKeyboardButton("🗑️ حذف", callback_data="sub_remove"))
    keyboard.add(types.InlineKeyboardButton("📋 القنوات", callback_data="sub_list"))
    keyboard.add(types.InlineKeyboardButton("🔛 تشغيل/إيقاف الإجباري", callback_data="sub_toggle"))
    keyboard.add(types.InlineKeyboardButton("↩️ الرئيسية", callback_data="adm_home"))
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
