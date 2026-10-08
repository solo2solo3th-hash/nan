"""Member-facing /start panel and its editable settings."""
from __future__ import annotations
from telebot import types
from config import DEVELOPER_ID
from database import has_permission, is_banned, save_chat, save_user, set_pending, setting_get
from subscriptions import show_subscription_wall


def types.InlineKeyboardButton(text, *args, emoji_key=None, **kwargs):
    key = emoji_key or str(kwargs.get("callback_data") or text).split(":", 1)[0].upper().replace("-", "_")
    emoji_id = (setting_get(f"EMOJI_BTN_{key}") or "").strip()
    if emoji_id:
        kwargs["icon_custom_emoji_id"] = emoji_id
    try:
        return types.InlineKeyboardButton(text, *args, **kwargs)
    except TypeError:
        kwargs.pop("icon_custom_emoji_id", None)
        return types.InlineKeyboardButton(text, *args, **kwargs)


def user_panel_settings_markup():
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(_member_button("📝 نص /start", callback_data="set_start_text"))
    keyboard.add(_member_button("🖼️ صورة /start", callback_data="set_start_image"))
    keyboard.add(_member_button("⚙️ الزر الأول", callback_data="set_btn1"))
    keyboard.add(_member_button("⚙️ الزر الثاني", callback_data="set_btn2"))
    keyboard.add(_member_button("↩️ الرئيسية", callback_data="adm_home"))
    return keyboard


def start_markup(bot_username: str):
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(_member_button("➕ أضفني إلى مجموعتك أو قناتك", url=f"https://t.me/{bot_username}?startgroup=true"))
    for name_key, url_key in (("CUSTOM_BTN1_NAME", "CUSTOM_BTN1_URL"), ("CUSTOM_BTN2_NAME", "CUSTOM_BTN2_URL")):
        name, url = setting_get(name_key), setting_get(url_key)
        if name and url and url.startswith(("https://", "http://", "tg://")):
            keyboard.add(_member_button(name, url=url))
    return keyboard


def handle_start(bot, message, bot_username: str) -> None:
    save_user(message.from_user)
    save_chat(message.chat)
    if is_banned(message.from_user.id):
        return
    if show_subscription_wall(bot, types, message):
        return
    text = setting_get("START_TEXT") or "أهلاً بك في بوت الموسيقى."
    image_id = setting_get("START_IMAGE_FILE_ID")
    image_type = setting_get("START_IMAGE_TYPE") or "photo"
    keyboard = start_markup(bot_username)
    if not image_id:
        bot.reply_to(message, text, reply_markup=keyboard)
    elif image_type == "animation":
        bot.send_animation(message.chat.id, image_id, caption=text, reply_markup=keyboard)
    else:
        bot.send_photo(message.chat.id, image_id, caption=text, reply_markup=keyboard)


def handle_user_panel_callback(bot, call, alert) -> bool:
    data = call.data or ""
    allowed = {"adm_user_panel", "set_start_text", "set_start_image", "set_btn1", "set_btn2"}
    if data not in allowed:
        return False
    if not has_permission(call.from_user.id, "user_panel", DEVELOPER_ID):
        alert(call, "🚫 ليست لديك صلاحية لهذا القسم.", True)
        return True
    if data == "adm_user_panel":
        bot.edit_message_text("👤 لوحة العضو\n\nتحكم برسالة /start والصورة والأزرار:", call.message.chat.id, call.message.message_id, reply_markup=user_panel_settings_markup())
        return True
    modes = {"set_start_text":"edit_start_text", "set_start_image":"edit_start_image", "set_btn1":"edit_btn1_input", "set_btn2":"edit_btn2_input"}
    prompts = {"set_start_text":"📝 أرسل النص الجديد لرسالة /start:", "set_start_image":"🖼️ أرسل الصورة أو GIF الجديدة:", "set_btn1":"⚙️ أرسل: اسم الزر | الرابط", "set_btn2":"⚙️ أرسل: اسم الزر | الرابط"}
    set_pending(call.from_user.id, modes[data], call.message.chat.id, call.message.message_id)
    back = types.InlineKeyboardMarkup()
    back.add(_member_button("↩️ رجوع", callback_data="adm_user_panel"))
    bot.edit_message_text(prompts[data], call.message.chat.id, call.message.message_id, reply_markup=back)
    return True
