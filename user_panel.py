"""Backward-compatible facade for the split member_panel module."""
from member_panel import (
    handle_start as _handle_start,
    handle_user_panel_callback as _handle_user_panel_callback,
    start_markup as _start_markup,
    user_panel_settings_markup as _user_panel_settings_markup,
)


def user_panel_settings_markup():
    return _user_panel_settings_markup()


def start_markup(bot_username: str):
    return _start_markup(bot_username)


def handle_start(bot, message, bot_username: str) -> None:
    return _handle_start(bot, message, bot_username)


def handle_user_panel_callback(bot, call, alert) -> bool:
    return _handle_user_panel_callback(bot, call, alert)
