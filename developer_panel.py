"""Developer panel adapted from the supplied panel and integrated with this bot's SQLite/TeleBot architecture."""

from __future__ import annotations

from telebot import types

from config import DEVELOPER_ID
from database import (
    PERMISSIONS,
    add_sudo,
    add_subscription,
    counts,
    delete_subscription,
    get_permission_state,
    get_pending,
    clear_pending,
    list_sudos,
    set_pending,
    remove_sudo,
    set_permission,
    setting_set,
    subscriptions,
)

DEV_IDS = {int(DEVELOPER_ID)}

PERMISSION_LABELS = {
    "users": "👥 إدارة المستخدمين",
    "channels": "📢 إدارة القنوات",
    "subscriptions": "🔒 الاشتراك الإجباري",
    "broadcast": "📣 الإذاعة",
    "stats": "📊 الإحصائيات",
    "social": "🌐 السوشيال",
    "admins": "👨‍💻 إدارة المشرفين",
    "settings": "⚙️ إعدادات البوت",
    "user_panel": "👤 لوحة العضو",
    "playback": "🎵 التحكم بالتشغيل",
}

def pending_input_set(user_id: int, state: str | None) -> None:
    if state is None:
        clear_pending(user_id)
    else:
        set_pending(user_id, state, None, None)


def pending_input_get(user_id: int):
    pending = get_pending(user_id)
    return pending[0] if pending else None


def list_sudo():
    return list_sudos()


def get_permissions(user_id: int):
    return get_permission_state(user_id, int(DEVELOPER_ID))


MAIN_TEXT = (
    "⚡ <b>أهلاً بك يا مطورنا في لوحة التحكم المركزية</b>\n\n"
    "اختر أحد الأقسام أدناه للتحكم بكافة تفاصيل البوت:"
)


def is_developer(user_id: int) -> bool:
    return int(user_id) in DEV_IDS


def main_markup():
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(
        types.InlineKeyboardButton("📊 إحصائيات البوت", callback_data="dev_stats"),
        types.InlineKeyboardButton("📢 قسم الإذاعة", callback_data="dev_broadcast_menu"),
    )
    m.add(
        types.InlineKeyboardButton("👥 إدارة المشرفين", callback_data="dev_admins_menu"),
        types.InlineKeyboardButton("📢 الاشتراك الإجباري", callback_data="dev_forced_sub"),
    )
    m.add(
        types.InlineKeyboardButton("⚙️ حقوق الخاص", callback_data="dev_private_rights"),
        types.InlineKeyboardButton("❌ إغلاق", callback_data="close_menu"),
    )
    return m


def back_markup(target="back_to_main"):
    return types.InlineKeyboardMarkup(
        [[types.InlineKeyboardButton("🔙 رجوع", callback_data=target)]]
    )


def cancel_markup(target):
    return types.InlineKeyboardMarkup(
        [[types.InlineKeyboardButton("❌ إلغاء", callback_data=target)]]
    )


def admins_markup():
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(
        types.InlineKeyboardButton("➕ صعد مشرف جديد", callback_data="admin_add"),
        types.InlineKeyboardButton("➖ شيل مشرف", callback_data="admin_remove"),
        types.InlineKeyboardButton("📋 رؤية المشرفين وصلاحياتهم", callback_data="admin_list"),
        types.InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main"),
    )
    return m


def broadcast_markup():
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(
        types.InlineKeyboardButton("📢 إذاعة للكل (أعضاء وبوتات وقنوات)", callback_data="bc_all"),
        types.InlineKeyboardButton("👤 إذاعة للأعضاء فقط", callback_data="bc_users"),
        types.InlineKeyboardButton("📢 إذاعة للقنوات فقط", callback_data="bc_channels"),
        types.InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main"),
    )
    return m


def _counts():
    try:
        result = counts()
        if isinstance(result, dict):
            return {
                "users": int(result.get("users", 0)),
                "chats": int(result.get("chats", 0)),
                "admins": int(result.get("admins", len(list_sudos()))),
            }
        users, chats, admins = result
        return {"users": int(users), "chats": int(chats), "admins": int(admins)}
    except Exception:
        return {"users": 0, "chats": 0, "admins": len(list_sudos())}


def statistics_text():
    s = _counts()
    return (
        "📊 <b>إحصائيات البوت:</b>\n\n"
        f"👤 عدد الأعضاء: <code>{s['users']}</code>\n"
        f"💬 عدد المحادثات: <code>{s['chats']}</code>\n"
        f"🛡️ عدد المشرفين: <code>{s['admins']}</code>"
    )


def show_statistics(bot, call):
    m = types.InlineKeyboardMarkup()
    m.add(types.InlineKeyboardButton("🔄 تحديث الإحصائيات", callback_data="dev_stats"))
    m.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main"))
    bot.edit_message_text(
        statistics_text(), call.message.chat.id, call.message.message_id,
        reply_markup=m, parse_mode="HTML"
    )


def show_broadcast_menu(bot, call):
    bot.edit_message_text(
        "📢 <b>اختر نوع الإذاعة التي تريد إرسالها:</b>",
        call.message.chat.id, call.message.message_id,
        reply_markup=broadcast_markup(), parse_mode="HTML"
    )


def begin_broadcast(bot, call, target):
    pending_input_set(call.from_user.id, f"waiting_broadcast_{target}")
    bot.edit_message_text(
        "✍️ <b>أرسل الآن الرسالة المراد إذاعتها.</b>\n"
        "يمكن أن تكون نص، صورة، فيديو، ملف، صوت أو فويس.",
        call.message.chat.id, call.message.message_id,
        reply_markup=cancel_markup("dev_broadcast_menu"), parse_mode="HTML"
    )


def _admin_id(item):
    if isinstance(item, (tuple, list)):
        return int(item[0])
    if isinstance(item, dict):
        return int(item.get("user_id", item.get("id")))
    return int(item)


def admins_text():
    admins = list_sudos()
    if not admins:
        return "📋 <b>قائمة المشرفين وصلاحياتهم:</b>\n\nلا يوجد مشرفون."

    lines = ["📋 <b>قائمة المشرفين وصلاحياتهم:</b>", ""]
    for item in admins:
        uid = _admin_id(item)
        perms = get_permission_state(uid, int(DEVELOPER_ID))
        enabled = [
            PERMISSION_LABELS.get(k, k)
            for k, v in perms.items() if v
        ]
        lines.append(
            f"• <code>{uid}</code> — "
            + (", ".join(enabled) if enabled else "بدون صلاحيات")
        )
    return "\n".join(lines)


def show_admins_menu(bot, call):
    bot.edit_message_text(
        "🛡️ <b>إدارة المشرفين والصلاحيات:</b>",
        call.message.chat.id, call.message.message_id,
        reply_markup=admins_markup(), parse_mode="HTML"
    )


def show_admin_list(bot, call):
    rows = []
    for item in list_sudos():
        uid = _admin_id(item)
        rows.append([
            types.InlineKeyboardButton(
                f"👤 {uid}", callback_data=f"admin_perms:{uid}"
            )
        ])
    rows.append([types.InlineKeyboardButton("🔙 رجوع", callback_data="dev_admins_menu")])
    bot.edit_message_text(
        admins_text(), call.message.chat.id, call.message.message_id,
        reply_markup=types.InlineKeyboardMarkup(rows), parse_mode="HTML"
    )


def begin_add_admin(bot, call):
    pending_input_set(call.from_user.id, "waiting_add_admin")
    bot.edit_message_text(
        "🆔 <b>أرسل آيدي (ID) الشخص الذي تريد ترقيته مشرفاً:</b>",
        call.message.chat.id, call.message.message_id,
        reply_markup=cancel_markup("dev_admins_menu"), parse_mode="HTML"
    )


def begin_remove_admin(bot, call):
    pending_input_set(call.from_user.id, "waiting_remove_admin")
    bot.edit_message_text(
        "🆔 <b>أرسل آيدي المشرف المراد إزالته:</b>",
        call.message.chat.id, call.message.message_id,
        reply_markup=cancel_markup("dev_admins_menu"), parse_mode="HTML"
    )


def add_admin_from_message(bot, message):
    if get_pending(message.from_user.id) != "waiting_add_admin":
        return False

    try:
        uid = int((message.text or "").strip())
        if uid <= 0:
            raise ValueError
    except ValueError:
        bot.reply_to(message, "⚠️ أرسل آيدي صحيح (أرقام فقط).")
        return True

    if uid == int(DEVELOPER_ID):
        pending_input_set(message.from_user.id, None)
        bot.reply_to(message, "ℹ️ هذا هو المطور الأساسي أصلاً.")
        return True

    try:
        add_sudo(uid, message.from_user.id)
        permission_keys = list(PERMISSIONS.keys()) if isinstance(PERMISSIONS, dict) else list(PERMISSIONS)
        for key in permission_keys:
            set_permission(uid, key, False)
    except Exception as exc:
        bot.reply_to(message, f"❌ تعذر إضافة المشرف: {exc}")
        return True

    pending_input_set(message.from_user.id, None)
    m = types.InlineKeyboardMarkup()
    m.add(types.InlineKeyboardButton("⚙️ إدارة الصلاحيات", callback_data=f"admin_perms:{uid}"))
    m.add(types.InlineKeyboardButton("🔙 إدارة المشرفين", callback_data="dev_admins_menu"))
    bot.reply_to(
        message,
        f"✅ تم رفع المستخدم <code>{uid}</code> مشرفاً بنجاح!\n"
        "⚠️ المشرف يبدأ بدون صلاحيات.",
        reply_markup=m, parse_mode="HTML"
    )
    return True


def remove_admin_from_message(bot, message):
    if get_pending(message.from_user.id) != "waiting_remove_admin":
        return False

    try:
        uid = int((message.text or "").strip())
    except ValueError:
        bot.reply_to(message, "⚠️ أرسل آيدي صحيح (أرقام فقط).")
        return True

    if uid == int(DEVELOPER_ID):
        bot.reply_to(message, "⛔ لا يمكن حذف المطور الأساسي.")
        return True

    admin_ids = {_admin_id(x) for x in list_sudos()}
    if uid not in admin_ids:
        bot.reply_to(message, "⚠️ هذا الآيدي غير موجود في قائمة المشرفين.")
        return True

    try:
        remove_sudo(uid)
    except Exception as exc:
        bot.reply_to(message, f"❌ تعذر حذف المشرف: {exc}")
        return True

    pending_input_set(message.from_user.id, None)
    bot.reply_to(message, f"✅ تم تنزيل المشرف <code>{uid}</code> بنجاح.", parse_mode="HTML")
    return True


def permissions_markup(uid):
    perms = get_permission_state(uid, int(DEVELOPER_ID))
    rows = []
    permission_keys = list(PERMISSIONS.keys()) if isinstance(PERMISSIONS, dict) else list(PERMISSIONS)
    for key in permission_keys:
        label = PERMISSION_LABELS.get(key, key)
        icon = "🟢" if perms.get(key) else "🔴"
        rows.append([
            types.InlineKeyboardButton(
                f"{icon} {label}",
                callback_data=f"toggle_perm:{uid}:{key}"
            )
        ])
    rows.append([types.InlineKeyboardButton("🔙 قائمة المشرفين", callback_data="admin_list")])
    return types.InlineKeyboardMarkup(rows)


def show_permissions(bot, call, uid):
    if uid == int(DEVELOPER_ID):
        bot.edit_message_text(
            "👑 <b>المطور الأساسي</b>\n\n"
            "المطور يمتلك تحكماً كاملاً دائماً.",
            call.message.chat.id, call.message.message_id,
            reply_markup=back_markup("admin_list"), parse_mode="HTML"
        )
        return

    perms = get_permission_state(uid, int(DEVELOPER_ID))
    active = sum(bool(v) for v in perms.values())
    bot.edit_message_text(
        f"⚙️ <b>صلاحيات المشرف</b>\n\n"
        f"🆔 <code>{uid}</code>\n"
        f"🟢 المفعلة: <code>{active}</code>\n\n"
        "اضغط على الصلاحية لتفعيلها أو تعطيلها:",
        call.message.chat.id, call.message.message_id,
        reply_markup=permissions_markup(uid), parse_mode="HTML"
    )


def toggle_permission(bot, call, uid, key):
    if uid == int(DEVELOPER_ID):
        bot.answer_callback_query(call.id, "المطور لديه تحكم كامل.", show_alert=True)
        return
    if key not in PERMISSION_LABELS:
        bot.answer_callback_query(call.id, "صلاحية غير معروفة.", show_alert=True)
        return

    current = bool(get_permission_state(uid, int(DEVELOPER_ID)).get(key))
    set_permission(uid, key, not current)
    bot.answer_callback_query(
        call.id,
        f"{PERMISSION_LABELS[key]}: " + ("تفعّلت 🟢" if not current else "تعطلت 🔴")
    )
    show_permissions(bot, call, uid)


def _sub_id(item):
    if isinstance(item, dict):
        return item.get("id")
    if isinstance(item, (tuple, list)) and item:
        return item[0]
    return None


def _sub_target(item):
    if isinstance(item, dict):
        return item.get("target") or item.get("channel") or item.get("chat_id")
    if isinstance(item, (tuple, list)):
        return item[-1] if item else ""
    return item


def forced_sub_markup():
    m = types.InlineKeyboardMarkup(row_width=1)
    for item in subscriptions():
        sid = _sub_id(item)
        target = _sub_target(item)
        if sid is not None:
            m.add(types.InlineKeyboardButton(
                f"❌ حذف {target}", callback_data=f"fs_remove:{target}"
            ))
    m.add(types.InlineKeyboardButton(
        "🔗 تعيين قناة الاشتراك الإجباري", callback_data="set_fs_channel"
    ))
    m.add(types.InlineKeyboardButton("🔄 تحديث", callback_data="dev_forced_sub"))
    m.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main"))
    return m


def show_forced_sub(bot, call):
    rows = subscriptions()
    current = ", ".join(str(_sub_target(x)) for x in rows) if rows else "لا توجد قناة"
    status = "مفعل 🟢" if rows else "معطل 🔴"
    bot.edit_message_text(
        "📢 <b>إعدادات الاشتراك الإجباري:</b>\n\n"
        f"الحالة: <b>{status}</b>\n"
        f"القنوات الحالية: <code>{current}</code>",
        call.message.chat.id, call.message.message_id,
        reply_markup=forced_sub_markup(), parse_mode="HTML"
    )


def begin_set_forced_channel(bot, call):
    pending_input_set(call.from_user.id, "waiting_fs_channel")
    bot.edit_message_text(
        "🔗 <b>أرسل معرف قناة الاشتراك الإجباري:</b>\n\n"
        "مثال: <code>@YourChannel</code>",
        call.message.chat.id, call.message.message_id,
        reply_markup=cancel_markup("dev_forced_sub"), parse_mode="HTML"
    )


def add_forced_channel_from_message(bot, message):
    if get_pending(message.from_user.id) != "waiting_fs_channel":
        return False

    text = (message.text or "").strip()
    if not text:
        bot.reply_to(message, "⚠️ أرسل بيانات القناة.")
        return True

    parts = [p.strip() for p in text.split("|", 2)]
    if len(parts) != 3 or not parts[0] or not parts[1] or not parts[2]:
        bot.reply_to(message, "⚠️ الصيغة: اسم القناة | @channel أو -100... | رابط القناة")
        return True

    title, target, url = parts
    if not target.startswith("@") and not target.lstrip("-").isdigit():
        bot.reply_to(message, "⚠️ معرف القناة غير صحيح.")
        return True

    try:
        add_subscription(title, target, url, True)
    except Exception as exc:
        bot.reply_to(message, f"❌ تعذر إضافة القناة: {exc}")
        return True

    pending_input_set(message.from_user.id, None)
    bot.reply_to(message, f"✅ تمت إضافة <code>{target}</code> للاشتراك الإجباري.", parse_mode="HTML")
    return True


def show_private_rights(bot, call):
    bot.edit_message_text(
        "⚙️ <b>حقوق الخاص</b>\n\n"
        "إعدادات صلاحيات الخاص ولوحة العضو يمكن التحكم بها من صلاحيات المشرفين.",
        call.message.chat.id, call.message.message_id,
        reply_markup=back_markup(), parse_mode="HTML"
    )


def open_panel(bot, message):
    if not is_developer(message.from_user.id):
        return False
    bot.send_message(
        message.chat.id, MAIN_TEXT,
        reply_markup=main_markup(), parse_mode="HTML"
    )
    return True


def _back_to_main(bot, call):
    pending_input_set(call.from_user.id, None)
    bot.edit_message_text(
        MAIN_TEXT, call.message.chat.id, call.message.message_id,
        reply_markup=main_markup(), parse_mode="HTML"
    )


def handle_callback(bot, call):
    """Return True when this developer-panel callback was consumed."""
    if not is_developer(call.from_user.id):
        if (call.data or "").startswith(("dev_", "admin_", "bc_", "toggle_perm:", "fs_", "set_fs_", "back_to_main", "close_menu")):
            bot.answer_callback_query(call.id, "⛔ هذه اللوحة خاصة بالمطور.", show_alert=True)
            return True
        return False

    data = call.data or ""

    if data == "dev_stats":
        bot.answer_callback_query(call.id); show_statistics(bot, call); return True
    if data == "dev_broadcast_menu":
        bot.answer_callback_query(call.id); show_broadcast_menu(bot, call); return True
    if data in {"bc_all", "bc_users", "bc_channels"}:
        bot.answer_callback_query(call.id); begin_broadcast(bot, call, data[3:]); return True
    if data == "dev_admins_menu":
        bot.answer_callback_query(call.id); show_admins_menu(bot, call); return True
    if data == "admin_list":
        bot.answer_callback_query(call.id); show_admin_list(bot, call); return True
    if data == "admin_add":
        bot.answer_callback_query(call.id); begin_add_admin(bot, call); return True
    if data == "admin_remove":
        bot.answer_callback_query(call.id); begin_remove_admin(bot, call); return True
    if data.startswith("admin_perms:"):
        bot.answer_callback_query(call.id)
        show_permissions(bot, call, int(data.split(":", 1)[1]))
        return True
    if data.startswith("toggle_perm:"):
        _, uid, key = data.split(":", 2)
        toggle_permission(bot, call, int(uid), key)
        return True
    if data == "dev_forced_sub":
        bot.answer_callback_query(call.id); show_forced_sub(bot, call); return True
    if data == "set_fs_channel":
        bot.answer_callback_query(call.id); begin_set_forced_channel(bot, call); return True
    if data.startswith("fs_remove:"):
        target = data.split(":", 1)[1]
        try:
            delete_subscription(target)
            bot.answer_callback_query(call.id, "✅ تم حذف القناة.")
            show_forced_sub(bot, call)
        except Exception as exc:
            bot.answer_callback_query(call.id, f"❌ {exc}", show_alert=True)
        return True
    if data == "dev_private_rights":
        bot.answer_callback_query(call.id); show_private_rights(bot, call); return True
    if data == "back_to_main":
        bot.answer_callback_query(call.id); _back_to_main(bot, call); return True
    if data == "close_menu":
        pending_input_set(call.from_user.id, None)
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        return True

    return False


def handle_input(bot, message):
    """Handle non-broadcast text input belonging to the developer panel."""
    if not is_developer(message.from_user.id):
        return False

    state = get_pending(message.from_user.id)
    if state == "waiting_add_admin":
        return add_admin_from_message(bot, message)
    if state == "waiting_remove_admin":
        return remove_admin_from_message(bot, message)
    if state == "waiting_fs_channel":
        return add_forced_channel_from_message(bot, message)

    # Playback panel image/GIF input is handled here before the generic
    # router so a media message cannot be swallowed by another handler.
    if state == "play_set_image":
        if getattr(message, "photo", None):
            setting_set("PLAY_IMAGE_FILE_ID", message.photo[-1].file_id)
            setting_set("PLAY_IMAGE_TYPE", "photo")
        elif getattr(message, "animation", None):
            setting_set("PLAY_IMAGE_FILE_ID", message.animation.file_id)
            setting_set("PLAY_IMAGE_TYPE", "animation")
        else:
            bot.reply_to(message, "❌ أرسل صورة أو GIF فقط.")
            return True
        pending_input_set(message.from_user.id, None)
        bot.reply_to(message, "✅ تم حفظ صورة لوحة التشغيل.")
        return True

    # Broadcast is intentionally left for the central bot_handlers broadcast
    # routine, so media/text forwarding stays in one place.
    return False


def register_developer_panel(bot):
    """Register /panel only; central callback/input routing uses the functions above."""
    @bot.message_handler(commands=["panel"])
    def _panel(message):
        open_panel(bot, message)


# Compatibility names for older wiring.
developer_panel_markup = main_markup
dev_main_panel = open_panel
dev_callbacks_handler = handle_callback
handle_admin_inputs = handle_input

__all__ = [
    "DEV_IDS", "PERMISSION_LABELS", "main_markup", "developer_panel_markup",
    "open_panel", "dev_main_panel", "handle_callback",
    "dev_callbacks_handler", "handle_input", "handle_admin_inputs",
    "register_developer_panel", "statistics_text", "admins_text",
]
