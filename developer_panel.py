"""Developer panel adapted from the supplied panel and integrated with this bot's SQLite/TeleBot architecture."""

from __future__ import annotations

import logging
from telebot import types

from config import DEVELOPER_ID
from assistant_login import begin_login, complete_login, cancel_login
from assistant_accounts import validate_encryption_key
from database import (
    PERMISSION_GROUPS,
    PERMISSIONS,
    add_sudo,
    add_subscription,
    counts,
    chat_ids_by_type,
    private_user_count,
    delete_subscription,
    get_permission_state,
    get_pending,
    clear_pending,
    list_sudos,
    set_pending,
    remove_sudo,
    set_permission,
    set_all_permissions,
    setting_get,
    setting_set,
    subscriptions,
)

log = logging.getLogger(__name__)

DEV_IDS = {int(DEVELOPER_ID)}
_ASSISTANT_POOL = None


def configure_assistant_pool(pool) -> None:
    """Attach the runtime pool used by the developer-only assistant menu."""
    global _ASSISTANT_POOL
    _ASSISTANT_POOL = pool

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
        types.InlineKeyboardButton("🔐 الحقوق", callback_data="dev_rights"),
        types.InlineKeyboardButton("🎛️ لوحة التشغيل", callback_data="dev_playback_settings"),
    )
    m.add(types.InlineKeyboardButton("💬 أوامر الشات", callback_data="dev_chat_commands"))
    m.add(types.InlineKeyboardButton("👥 المساعدين", callback_data="dev_assistants"))
    m.add(
        types.InlineKeyboardButton("👤 لوحة الخاص", callback_data="adm_user_panel"),
        types.InlineKeyboardButton("❌ إغلاق", callback_data="close_menu"),
    )
    return m


def developer_markup():
    """Compatibility wrapper used by bot_handlers/admin_panel."""
    return main_markup()


def admin_text() -> str:
    """Compatibility wrapper for the main developer panel text."""
    return MAIN_TEXT


def admins_menu():
    """Compatibility wrapper for the admins section keyboard."""
    return admins_markup()


def permissions_select_markup():
    """Keyboard for selecting an admin whose permissions should be edited."""
    rows = []
    for item in list_sudos():
        uid = _admin_id(item)
        rows.append([types.InlineKeyboardButton(f"👤 {uid}", callback_data=f"admin_perms:{uid}")])
    rows.append([types.InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")])
    return types.InlineKeyboardMarkup(rows)


def permission_markup(user_id: int):
    """Compatibility wrapper for the existing permission keyboard."""
    return permissions_markup(user_id)


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


def _stats_counts():
    """Return dashboard counts by actual chat type, not generic DB row totals."""
    try:
        channel_count = len(chat_ids_by_type(("channel",)))
        group_count = len(chat_ids_by_type(("group", "supergroup")))
        private_count = int(private_user_count())
        stored_admins = int(counts()[2])
        admins = stored_admins + (1 if int(DEVELOPER_ID) else 0)
        return {
            "channels": channel_count,
            "groups": group_count,
            "private": private_count,
            "admins": admins,
        }
    except Exception:
        return {
            "channels": 0,
            "groups": 0,
            "private": 0,
            "admins": 1 if int(DEVELOPER_ID) else 0,
        }


def statistics_text(bot=None):
    s = _stats_counts()
    return (
        "📊 <b>إحصائيات البوت:</b>\n\n"
        f"📢 عدد القنوات: <code>{s['channels']}</code>\n"
        f"👥 عدد الكروبات: <code>{s['groups']}</code>\n"
        f"💬 عدد الخاص (/start): <code>{s['private']}</code>\n"
        f"👤 مستخدمو البوت من الخاص: <code>{s['private']}</code>\n"
        f"🛡️ عدد المشرفين: <code>{s['admins']}</code>"
    )


def show_statistics(bot, call):
    m = types.InlineKeyboardMarkup()
    m.add(types.InlineKeyboardButton("🔄 تحديث الإحصائيات", callback_data="dev_stats"))
    m.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main"))
    bot.edit_message_text(
        statistics_text(bot), call.message.chat.id, call.message.message_id,
        reply_markup=m, parse_mode="HTML"
    )

def show_assistants_menu(bot, call):
    """Show configured assistant slots without exposing session secrets."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    lines = ["🤖 <b>إدارة المساعدين (حتى 5 حسابات)</b>", ""]
    if _ASSISTANT_POOL is None:
        lines.append("🔴 نظام المساعدين غير متصل بواجهة التشغيل.")
    else:
        for item in _ASSISTANT_POOL.slots_status():
            slot = item["slot"]
            if not item["configured"]:
                icon, status = "⚪", "غير مضبوط في Railway"
            elif item["running"]:
                icon, status = "🟢", "متصل"
            else:
                icon, status = "🟡", "مضبوط وغير مشغّل"
            selected = "  ← المحدد" if item["selected"] else ""
            lines.append(f"{icon} المساعد {slot}: <b>{status}</b>{selected}")
            if item["configured"]:
                label = f"✅ المساعد {slot} (المحدد)" if item["selected"] else f"🔀 اختيار المساعد {slot}"
                markup.add(types.InlineKeyboardButton(label, callback_data=f"dev_assistant_select:{slot}"))
    if _ASSISTANT_POOL is not None:
        if _ASSISTANT_POOL.available_slots():
            markup.add(types.InlineKeyboardButton("➕ إضافة حساب مساعد", callback_data="dev_assistant_add"))
        removable = [item for item in _ASSISTANT_POOL.slots_status() if item["configured"] and not item.get("managed_by_railway", False)]
        if removable:
            markup.add(types.InlineKeyboardButton("➖ حذف حساب مساعد", callback_data="dev_assistant_remove"))
        markup.add(types.InlineKeyboardButton("🎯 تعيين مساعد لمجموعة", callback_data="dev_assistant_assign_chat"))
        markup.add(types.InlineKeyboardButton("🧹 إلغاء تعيين مجموعة", callback_data="dev_assistant_unassign_chat"))
    markup.add(types.InlineKeyboardButton("🔗 زر المساعد", callback_data="dev_assistant_button_menu"))
    markup.add(types.InlineKeyboardButton("🔄 تحديث الحالة", callback_data="dev_assistants"))
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main"))
    lines.extend([
        "",
        "🔐 الجلسات المخزنة من اللوحة مشفّرة في SQLite، ولا تُعرض داخل اللوحة.",
        "المساعد المحدد هو الافتراضي، ويمكن تعيين مساعد مستقل لكل مجموعة؛ التعيينات محفوظة في SQLite.",
        "حسابات Railway ثابتة من اللوحة؛ الحسابات المضافة من هنا يمكن حذفها وإدارتها.",
        "⚠️ أضف ASSISTANT_ENCRYPTION_KEY في Railway قبل تسجيل حساب جديد. لا تشارك كود الدخول أو كلمة المرور مع أي شخص.",
    ])
    bot.edit_message_text(
        "\n".join(lines),
        call.message.chat.id, call.message.message_id,
        reply_markup=markup, parse_mode="HTML"
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
    if pending_input_get(message.from_user.id) != "waiting_add_admin":
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
    if pending_input_get(message.from_user.id) != "waiting_remove_admin":
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
    enabled = setting_get("SUBS_ENABLED") == "ON"
    m.add(types.InlineKeyboardButton(
        "اجباري الاستخدام", callback_data="fs_toggle_usage"
    ))
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
    enabled = setting_get("SUBS_ENABLED") == "ON"
    status = "مفعل 🟢" if enabled and rows else "معطل 🔴"
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
    if pending_input_get(message.from_user.id) != "waiting_fs_channel":
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


def _rights_group_label(group):
    return {
        "playback": "🎵 التشغيل", "users": "👥 المستخدمون",
        "admins": "👨‍💻 المشرفون", "broadcast": "📣 الإذاعة",
        "subscriptions": "🔒 الاشتراك الإجباري", "stats": "📊 الإحصائيات",
        "user_panel": "👤 لوحة /start", "playback_panel": "🎛️ تخصيص التشغيل",
        "settings": "⚙️ الإعدادات", "sources": "🌐 المصادر",
        "channels": "📢 القنوات", "social": "🔗 الروابط",
    }.get(group, group)


def rights_markup(uid: int):
    m = types.InlineKeyboardMarkup(row_width=2)
    for group in PERMISSION_GROUPS:
        m.add(types.InlineKeyboardButton(_rights_group_label(group), callback_data=f"rights_group:{uid}:{group}"))
    m.row(
        types.InlineKeyboardButton("🟢 تفعيل الكل", callback_data=f"rights_all:{uid}:1"),
        types.InlineKeyboardButton("🔴 تعطيل الكل", callback_data=f"rights_all:{uid}:0"),
    )
    m.add(types.InlineKeyboardButton("↩️ المشرفين", callback_data="dev_admins_menu"))
    return m


def rights_group_markup(uid: int, group: str):
    state = get_permission_state(uid, int(DEVELOPER_ID))
    m = types.InlineKeyboardMarkup(row_width=1)
    for key, label in PERMISSION_GROUPS[group].items():
        permission = f"{group}.{key}"
        icon = "🟢" if state.get(permission, False) else "🔴"
        m.add(types.InlineKeyboardButton(f"{icon} {label}", callback_data=f"rights_toggle:{uid}:{permission}"))
    m.row(
        types.InlineKeyboardButton("🟢 تفعيل القسم", callback_data=f"rights_group_all:{uid}:{group}:1"),
        types.InlineKeyboardButton("🔴 تعطيل القسم", callback_data=f"rights_group_all:{uid}:{group}:0"),
    )
    m.add(types.InlineKeyboardButton("↩️ كل الأقسام", callback_data=f"admin_rights:{uid}"))
    return m


def show_rights(bot, call, uid):
    bot.edit_message_text(
        f"🔐 <b>حقوق المشرف {uid}</b>\n\nكل قسم له حقوق مستقلة.",
        call.message.chat.id, call.message.message_id,
        reply_markup=rights_markup(uid), parse_mode="HTML"
    )


def show_rights_admins(bot, call):
    rows = []
    for item in list_sudos():
        uid = _admin_id(item)
        rows.append([types.InlineKeyboardButton(f"👤 {uid}", callback_data=f"admin_rights:{uid}")])
    rows.append([types.InlineKeyboardButton("↩️ رجوع", callback_data="back_to_main")])
    bot.edit_message_text(
        "🔐 <b>اختر المشرف الذي تريد تعديل حقوقه:</b>",
        call.message.chat.id, call.message.message_id,
        reply_markup=types.InlineKeyboardMarkup(rows), parse_mode="HTML"
    )


def show_private_rights(bot, call):
    bot.edit_message_text(
        "⚙️ <b>حقوق الخاص</b>\n\n"
        "إعدادات صلاحيات الخاص ولوحة العضو يمكن التحكم بها من صلاحيات المشرفين.",
        call.message.chat.id, call.message.message_id,
        reply_markup=back_markup(), parse_mode="HTML"
    )



def playback_settings_markup():
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(types.InlineKeyboardButton("✍️ الكتابة: الاسم + الرابط", callback_data="play_credit_pair"))
    m.add(types.InlineKeyboardButton("🎵 زر الموسيقى: الاسم + الرابط", callback_data="play_music_pair"))
    m.add(types.InlineKeyboardButton("🖼️ صورة لوحة التشغيل", callback_data="play_set_image"))
    m.add(types.InlineKeyboardButton("🎨 ألوان أزرار لوحة التشغيل", callback_data="play_button_colors"))
    m.add(types.InlineKeyboardButton("📝 أسماء أزرار التشغيل", callback_data="play_button_labels"))
    m.add(types.InlineKeyboardButton("🎵 أغنية الجات", callback_data="jat_audio_menu"))
    m.add(types.InlineKeyboardButton("↩️ رجوع", callback_data="back_to_main"))
    return m


_PLAYBACK_BUTTON_LABELS = {
    "skip": "⏭️ تخطي",
    "stop": "⏹️ إنهاء",
    "pause": "⏸️ إيقاف مؤقت",
    "rewind": "⏪ ترجيع 10 ثوانٍ",
    "resume": "▶️ استئناف",
    "forward": "⏩ تقديم 10 ثوانٍ",
    "custom1": "🔗 الزر المخصص الأول",
    "custom2": "🔗 الزر المخصص الثاني",
    "top": "🔝 زر الأعلى",
}

_PLAYBACK_COLOR_LABELS = {
    "default": "⚪ افتراضي",
    "primary": "🔵 أزرق",
    "success": "🟢 أخضر",
    "danger": "🔴 أحمر",
}


def playback_button_colors_markup():
    m = types.InlineKeyboardMarkup(row_width=1)
    for key, label in _PLAYBACK_BUTTON_LABELS.items():
        current = (setting_get(f"PLAY_BTN_COLOR_{key.upper()}") or "default").strip().lower()
        if current not in _PLAYBACK_COLOR_LABELS:
            current = "default"
        m.add(types.InlineKeyboardButton(
            f"{label} — {_PLAYBACK_COLOR_LABELS[current]}",
            callback_data=f"play_color:{key}",
        ))
    m.add(types.InlineKeyboardButton("🧹 إرجاع كل الألوان افتراضي", callback_data="play_colors_reset"))
    m.add(types.InlineKeyboardButton("↩️ رجوع", callback_data="dev_playback_settings"))
    return m


def playback_button_color_picker(key: str):
    label = _PLAYBACK_BUTTON_LABELS.get(key, key)
    m = types.InlineKeyboardMarkup(row_width=2)
    for color, color_label in _PLAYBACK_COLOR_LABELS.items():
        m.add(types.InlineKeyboardButton(
            color_label, callback_data=f"play_color_set:{key}:{color}"
        ))
    m.add(types.InlineKeyboardButton("↩️ رجوع", callback_data="play_button_colors"))
    return m


def jat_audio_markup():
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(types.InlineKeyboardButton("🔘 الزر تحت الأغنية: الاسم + الرابط", callback_data="jat_audio_button"))
    m.add(types.InlineKeyboardButton("📝 الكتابة داخل الأغنية: الاسم + الرابط", callback_data="jat_audio_credit"))
    m.add(types.InlineKeyboardButton("🎤 مصدر الأغنية", callback_data="jat_audio_performer"))
    m.add(types.InlineKeyboardButton("🗑️ مسح إعدادات أغنية الجات", callback_data="jat_audio_clear"))
    m.add(types.InlineKeyboardButton("↩️ رجوع", callback_data="dev_playback_settings"))
    return m


def show_jat_audio(bot, call):
    from database import setting_get
    button_name = setting_get("JAT_AUDIO_BUTTON_NAME") or "غير محدد"
    button_url = setting_get("JAT_AUDIO_BUTTON_URL") or "غير محدد"
    credit_name = setting_get("JAT_AUDIO_CREDIT_NAME") or "غير محدد"
    credit_url = setting_get("JAT_AUDIO_CREDIT_URL") or "غير محدد"
    performer = setting_get("JAT_AUDIO_PERFORMER") or "من نينو"
    bot.edit_message_text(
        "🎵 <b>أغنية الجات</b>\n\n"
        f"🔘 زر تحت الأغنية: <code>{button_name}</code>\n"
        f"🔗 الرابط: <code>{button_url}</code>\n\n"
        f"📝 الكتابة داخل الأغنية: <code>{credit_name}</code>\n"
        f"🔗 الرابط: <code>{credit_url}</code>\n\n"
        f"🎤 المصدر الظاهر: <code>{performer}</code>",
        call.message.chat.id, call.message.message_id,
        reply_markup=jat_audio_markup(), parse_mode="HTML"
    )


def chat_commands_markup():
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(types.InlineKeyboardButton("✍️ اسم الزر + الرابط", callback_data="chat_cmd_button"))
    m.add(types.InlineKeyboardButton("🗑️ حذف الزر", callback_data="chat_cmd_button_clear"))
    m.add(types.InlineKeyboardButton("↩️ رجوع", callback_data="back_to_main"))
    return m

def show_chat_commands(bot, call):
    from database import setting_get
    name = setting_get("CHAT_COMMANDS_BUTTON_NAME") or "غير محدد"
    url = setting_get("CHAT_COMMANDS_BUTTON_URL") or "غير محدد"
    bot.edit_message_text(
        "💬 <b>أوامر الشات</b>\n\n"
        "عند كتابة «اوامر» داخل المجموعة تظهر قائمة الأوامر للعضو أو المشرف أو المالك.\n\n"
        f"🔘 اسم الزر: <code>{name}</code>\n"
        f"🔗 الرابط: <code>{url}</code>",
        call.message.chat.id, call.message.message_id,
        reply_markup=chat_commands_markup(), parse_mode="HTML"
    )

def show_playback_settings(bot, call):
    from database import setting_get
    credit_name = setting_get("PLAY_CREDIT_NAME") or "غير محدد"
    credit_url = setting_get("PLAY_CREDIT_URL") or "غير محدد"
    music_name = setting_get("PLAY_MUSIC_BUTTON_NAME") or "غير محدد"
    music_url = setting_get("PLAY_MUSIC_BUTTON_URL") or "غير محدد"
    image_type = setting_get("PLAY_IMAGE_TYPE") or "photo"
    bot.edit_message_text(
        "🎛️ <b>لوحة التشغيل</b>\n\n"
        f"✍️ الكتابة: <code>{credit_name}</code>\n"
        f"🔗 الرابط: <code>{credit_url}</code>\n"
        f"🎵 زر الموسيقى: <code>{music_name}</code>\n"
        f"🔗 الرابط: <code>{music_url}</code>\n"
        f"🖼️ الصورة: <code>{image_type}</code>",
        call.message.chat.id, call.message.message_id,
        reply_markup=playback_settings_markup(), parse_mode="HTML"
    )


def begin_playback_input(bot, call, mode, prompt):
    pending_input_set(call.from_user.id, mode)
    bot.edit_message_text(
        prompt, call.message.chat.id, call.message.message_id,
        reply_markup=cancel_markup("dev_playback_settings"), parse_mode="HTML"
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


def _handle_callback_impl(bot, call):
    """Internal callback dispatcher; returns True when consumed."""
    if not is_developer(call.from_user.id):
        if (call.data or "").startswith(("dev_", "admin_", "bc_", "toggle_perm:", "fs_", "set_fs_", "back_to_main", "close_menu")):
            bot.answer_callback_query(call.id, "⛔ هذه اللوحة خاصة بالمطور.", show_alert=True)
            return True
        return False

    data = call.data or ""

    if data == "dev_assistant_button_menu":
        bot.answer_callback_query(call.id)
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("📝 تعديل كتابة رسالة الحظر", callback_data="dev_assistant_button_text"),
            types.InlineKeyboardButton("🔗 تعديل اسم الزر ورابطه", callback_data="dev_assistant_button_link"),
            types.InlineKeyboardButton("🔙 رجوع للمساعدين", callback_data="dev_assistants"),
        )
        bot.edit_message_text(
            "🔗 <b>إعدادات زر المساعد</b>\n\n"
            "تظهر الرسالة والزر في المجموعة إذا كان المساعد محظوراً أو مطروداً.\n"
            "يمكنك تخصيص النص، واسم الزر والرابط من هنا.",
            call.message.chat.id, call.message.message_id,
            reply_markup=markup, parse_mode="HTML"
        )
        return True
    if data == "dev_assistant_button_text":
        bot.answer_callback_query(call.id)
        pending_input_set(call.from_user.id, "waiting_assistant_notice_text")
        bot.edit_message_text(
            "📝 أرسل النص الذي تريد أن يظهر للمجموعة عند حظر المساعد.",
            call.message.chat.id, call.message.message_id,
            reply_markup=cancel_markup("dev_assistant_button_menu")
        )
        return True
    if data == "dev_assistant_button_link":
        bot.answer_callback_query(call.id)
        pending_input_set(call.from_user.id, "waiting_assistant_button")
        bot.edit_message_text(
            "🔗 أرسل اسم الزر ثم نقطتين ثم الرابط.\n"
            "مثال: ⛓️ فك الحظر عن المساعد : https://t.me/your_username\n"
            "لإزالة الزر أرسل: OFF",
            call.message.chat.id, call.message.message_id,
            reply_markup=cancel_markup("dev_assistant_button_menu")
        )
        return True
    if data == "dev_assistants":
        if pending_input_get(call.from_user.id) in {"waiting_assistant_phone", "waiting_assistant_code", "waiting_assistant_password"}:
            cancel_login(call.from_user.id)
            pending_input_set(call.from_user.id, None)
        bot.answer_callback_query(call.id)
        show_assistants_menu(bot, call)
        return True
    if data == "dev_assistant_add":
        if call.message.chat.type != "private":
            bot.answer_callback_query(call.id, "افتح لوحة المطور في الخاص لإضافة حساب بأمان.", show_alert=True)
            return True
        if _ASSISTANT_POOL is None or not _ASSISTANT_POOL.available_slots():
            bot.answer_callback_query(call.id, "لا توجد خانات فارغة (الحد 5 حسابات).", show_alert=True)
            return True
        try:
            validate_encryption_key()
        except Exception as exc:
            bot.answer_callback_query(call.id, str(exc), show_alert=True)
            return True
        bot.answer_callback_query(call.id)
        pending_input_set(call.from_user.id, "waiting_assistant_phone")
        bot.edit_message_text(
            "📱 <b>إضافة حساب مساعد</b>\n\nأرسل رقم الهاتف بصيغة دولية، مثال: <code>+9647XXXXXXXXX</code>\n"
            "ستصلك رسالة كود من Telegram. لا ترسل الكود لأي شخص. هذه الخطوة تعمل في الخاص فقط.",
            call.message.chat.id, call.message.message_id,
            reply_markup=cancel_markup("dev_assistants"), parse_mode="HTML"
        )
        return True
    if data == "dev_assistant_remove":
        if _ASSISTANT_POOL is None:
            bot.answer_callback_query(call.id, "نظام المساعدين غير جاهز.", show_alert=True)
            return True
        markup = types.InlineKeyboardMarkup(row_width=1)
        for item in _ASSISTANT_POOL.slots_status():
            if item["configured"] and not item.get("managed_by_railway", False):
                markup.add(types.InlineKeyboardButton(
                    f"🗑 حذف المساعد {item['slot']}",
                    callback_data=f"dev_assistant_remove:{item['slot']}"
                ))
        markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="dev_assistants"))
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            "اختر الحساب الذي تريد حذفه. حسابات Railway لا يمكن حذفها من اللوحة.",
            call.message.chat.id, call.message.message_id, reply_markup=markup
        )
        return True
    if data.startswith("dev_assistant_remove:"):
        try:
            slot = int(data.split(":", 1)[1])
            if _ASSISTANT_POOL is None:
                raise RuntimeError("نظام المساعدين غير جاهز.")
            _ASSISTANT_POOL.remove_session(slot)
            bot.answer_callback_query(call.id, f"تم حذف المساعد {slot}.")
            show_assistants_menu(bot, call)
        except Exception as exc:
            bot.answer_callback_query(call.id, f"تعذر الحذف: {exc}", show_alert=True)
        return True
    if data == "dev_assistant_assign_chat":
        bot.answer_callback_query(call.id)
        pending_input_set(call.from_user.id, "waiting_assistant_chat_id")
        bot.edit_message_text(
            "🆔 أرسل آيدي المجموعة التي تريد تعيين مساعد لها.\n"
            "لإلغاء العملية اضغط رجوع.",
            call.message.chat.id, call.message.message_id,
            reply_markup=cancel_markup("dev_assistants")
        )
        return True
    if data == "dev_assistant_unassign_chat":
        bot.answer_callback_query(call.id)
        pending_input_set(call.from_user.id, "waiting_assistant_unassign_chat_id")
        bot.edit_message_text(
            "🆔 أرسل آيدي المجموعة التي تريد إرجاعها إلى المساعد الافتراضي.",
            call.message.chat.id, call.message.message_id,
            reply_markup=cancel_markup("dev_assistants")
        )
        return True
    if data.startswith("dev_assistant_assign:"):
        try:
            _, raw_chat_id, raw_slot = data.split(":", 2)
            chat_id, slot = int(raw_chat_id), int(raw_slot)
            if _ASSISTANT_POOL is None:
                raise RuntimeError("نظام المساعدين غير جاهز.")
            _ASSISTANT_POOL.assign_chat(chat_id, slot)
            pending_input_set(call.from_user.id, None)
            bot.answer_callback_query(call.id, "تم تعيين المساعد للمجموعة.")
            show_assistants_menu(bot, call)
        except Exception as exc:
            bot.answer_callback_query(call.id, f"تعذر التعيين: {exc}", show_alert=True)
        return True
    if data.startswith("dev_assistant_select:"):
        try:
            slot = int(data.split(":", 1)[1])
            if _ASSISTANT_POOL is None:
                raise RuntimeError("نظام المساعدين غير جاهز.")
            _ASSISTANT_POOL.select(slot)
            bot.answer_callback_query(call.id, f"تم اختيار المساعد {slot}.")
        except Exception as exc:
            bot.answer_callback_query(call.id, f"تعذر التبديل: {exc}", show_alert=True)
        show_assistants_menu(bot, call)
        return True
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
    if data == "fs_toggle_usage":
        enabled = setting_get("SUBS_ENABLED") == "ON"
        if not subscriptions():
            bot.answer_callback_query(call.id, "⚠️ أضف قناة أو كروب أولاً.", show_alert=True)
            return True
        setting_set("SUBS_ENABLED", "OFF" if enabled else "ON")
        bot.answer_callback_query(call.id, "🟢 تم تفعيل إجباري الاستخدام." if not enabled else "🔴 تم إيقاف إجباري الاستخدام.")
        show_forced_sub(bot, call)
        return True
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
    if data == "dev_chat_commands":
        bot.answer_callback_query(call.id)
        show_chat_commands(bot, call)
        return True
    if data == "chat_cmd_button":
        bot.answer_callback_query(call.id)
        begin_playback_input(bot, call, "chat_cmd_button", "💬 أرسل بالصيغة: <code>اسم الزر : الرابط</code>")
        return True
    if data == "chat_cmd_button_clear":
        setting_set("CHAT_COMMANDS_BUTTON_NAME", "")
        setting_set("CHAT_COMMANDS_BUTTON_URL", "")
        bot.answer_callback_query(call.id, "✅ تم حذف زر أوامر الشات.")
        show_chat_commands(bot, call)
        return True
    if data == "play_button_labels":
        bot.answer_callback_query(call.id)
        begin_playback_input(
            bot, call, "play_button_labels",
            "📝 أرسل أسماء الأزرار السبعة بهذا الترتيب، وافصل بينها بعلامة |\n"
            "1 تخطي | 2 إنهاء | 3 إيقاف | 4 ترجيع 10 ثوانٍ | 5 تشغيل/استئناف | 6 تقديم 10 ثوانٍ | 7 الأعلى\n\n"
            "مثال: ⏭️ تخطي | ⏹️ إنهاء | ⏸️ إيقاف | -10s | ▶️ | +10s | 🔝\n"
            "لإرجاع اسم افتراضي اكتب DEFAULT مكانه. الحد الأقصى 64 حرفاً لكل اسم."
        )
        return True

    if data == "play_button_colors":
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            "🎨 <b>ألوان أزرار لوحة التشغيل</b>\n\nاختر الزر ثم اللون. ألوان Telegram هي أنماط دلالية (أزرق/أخضر/أحمر) وتظهر حسب دعم تطبيق Telegram؛ لا يمكن فرض لون مخصص أو ضمانه على كل الأجهزة.",
            call.message.chat.id, call.message.message_id,
            reply_markup=playback_button_colors_markup(), parse_mode="HTML"
        )
        return True
    if data.startswith("play_color:"):
        key = data.split(":", 1)[1]
        if key not in _PLAYBACK_BUTTON_LABELS:
            bot.answer_callback_query(call.id, "الزر غير معروف.", show_alert=True)
            return True
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            f"🎨 <b>{_PLAYBACK_BUTTON_LABELS[key]}</b>\n\nاختر اللون:",
            call.message.chat.id, call.message.message_id,
            reply_markup=playback_button_color_picker(key), parse_mode="HTML"
        )
        return True
    if data.startswith("play_color_set:"):
        _, key, color = data.split(":", 2)
        if key not in _PLAYBACK_BUTTON_LABELS or color not in _PLAYBACK_COLOR_LABELS:
            bot.answer_callback_query(call.id, "إعداد غير صالح.", show_alert=True)
            return True
        setting_set(f"PLAY_BTN_COLOR_{key.upper()}", color)
        bot.answer_callback_query(call.id, f"تم اختيار {_PLAYBACK_COLOR_LABELS[color]}")
        bot.edit_message_text(
            "🎨 <b>ألوان أزرار لوحة التشغيل</b>\n\nاختر الزر ثم اللون. ألوان Telegram هي أنماط دلالية (أزرق/أخضر/أحمر) وتظهر حسب دعم تطبيق Telegram؛ لا يمكن فرض لون مخصص أو ضمانه على كل الأجهزة.",
            call.message.chat.id, call.message.message_id,
            reply_markup=playback_button_colors_markup(), parse_mode="HTML"
        )
        return True
    if data == "play_colors_reset":
        for key in _PLAYBACK_BUTTON_LABELS:
            setting_set(f"PLAY_BTN_COLOR_{key.upper()}", "default")
        bot.answer_callback_query(call.id, "✅ رجعت كل الألوان للوضع الافتراضي.")
        bot.edit_message_text(
            "🎨 <b>ألوان أزرار لوحة التشغيل</b>\n\nاختر الزر ثم اللون. ألوان Telegram هي أنماط دلالية (أزرق/أخضر/أحمر) وتظهر حسب دعم تطبيق Telegram؛ لا يمكن فرض لون مخصص أو ضمانه على كل الأجهزة.",
            call.message.chat.id, call.message.message_id,
            reply_markup=playback_button_colors_markup(), parse_mode="HTML"
        )
        return True

    if data == "jat_audio_menu":
        bot.answer_callback_query(call.id)
        show_jat_audio(bot, call)
        return True
    if data == "jat_audio_button":
        bot.answer_callback_query(call.id)
        begin_playback_input(bot, call, "jat_audio_button", "🔘 أرسل بالصيغة: <code>اسم الزر : الرابط</code>")
        return True
    if data == "jat_audio_credit":
        bot.answer_callback_query(call.id)
        begin_playback_input(bot, call, "jat_audio_credit", "📝 أرسل بالصيغة: <code>الكتابة : الرابط</code>\nستظهر ككتابة قابلة للضغط أسفل الأغنية، وليست زرًا.")
        return True
    if data == "jat_audio_performer":
        bot.answer_callback_query(call.id)
        begin_playback_input(bot, call, "jat_audio_performer", "🎤 أرسل اسم المصدر الظاهر أسفل اسم الأغنية.\nمثال: <code>من نينو</code> أو <code>غير معروف</code>")
        return True
    if data == "jat_audio_clear":
        setting_set("JAT_AUDIO_BUTTON_NAME", "")
        setting_set("JAT_AUDIO_BUTTON_URL", "")
        setting_set("JAT_AUDIO_CREDIT_NAME", "")
        setting_set("JAT_AUDIO_CREDIT_URL", "")
        setting_set("JAT_AUDIO_PERFORMER", "من نينو")
        bot.answer_callback_query(call.id, "✅ تم مسح إعدادات أغنية الجات.")
        show_jat_audio(bot, call)
        return True
    if data == "dev_playback_settings":
        bot.answer_callback_query(call.id)
        show_playback_settings(bot, call)
        return True
    if data == "play_credit_pair":
        bot.answer_callback_query(call.id)
        begin_playback_input(bot, call, "play_credit_pair", "✍️ أرسل بالصيغة: <code>اسم الزر : الرابط</code>")
        return True
    if data == "play_music_pair":
        bot.answer_callback_query(call.id)
        begin_playback_input(bot, call, "play_music_pair", "🎵 أرسل بالصيغة: <code>اسم الزر : الرابط</code>")
        return True
    if data == "play_set_image":
        bot.answer_callback_query(call.id)
        begin_playback_input(bot, call, "play_set_image", "🖼️ أرسل صورة أو GIF لوحة التشغيل:")
        return True
    if data == "dev_rights":
        bot.answer_callback_query(call.id); show_rights_admins(bot, call); return True
    if data.startswith("admin_rights:"):
        bot.answer_callback_query(call.id)
        show_rights(bot, call, int(data.split(":", 1)[1]))
        return True
    if data.startswith("rights_group:"):
        _, raw_uid, group = data.split(":", 2)
        bot.answer_callback_query(call.id)
        if group not in PERMISSION_GROUPS:
            bot.answer_callback_query(call.id, "قسم غير معروف.", show_alert=True); return True
        bot.edit_message_text(
            f"🔐 <b>{_rights_group_label(group)}</b>\n\nاختر الحق المطلوب:",
            call.message.chat.id, call.message.message_id,
            reply_markup=rights_group_markup(int(raw_uid), group), parse_mode="HTML"
        )
        return True
    if data.startswith("rights_toggle:"):
        _, raw_uid, permission = data.split(":", 2)
        uid = int(raw_uid)
        if uid == int(DEVELOPER_ID):
            bot.answer_callback_query(call.id, "المطور لديه كل الحقوق دائماً.", show_alert=True); return True
        state = get_permission_state(uid, int(DEVELOPER_ID))
        current = bool(state.get(permission, False))
        set_permission(uid, permission, not current)
        bot.answer_callback_query(call.id, "تم تحديث الحق.")
        group = permission.split(".", 1)[0]
        bot.edit_message_reply_markup(
            call.message.chat.id, call.message.message_id,
            reply_markup=rights_group_markup(uid, group)
        )
        return True
    if data.startswith("rights_group_all:"):
        _, raw_uid, group, raw_value = data.split(":", 3)
        uid = int(raw_uid)
        if uid == int(DEVELOPER_ID):
            bot.answer_callback_query(call.id, "المطور لديه كل الحقوق دائماً.", show_alert=True); return True
        value = raw_value == "1"
        for key in PERMISSION_GROUPS[group]:
            set_permission(uid, f"{group}.{key}", value)
        bot.answer_callback_query(call.id, "تم تحديث حقوق القسم.")
        bot.edit_message_reply_markup(
            call.message.chat.id, call.message.message_id,
            reply_markup=rights_group_markup(uid, group)
        )
        return True
    if data.startswith("rights_all:"):
        _, raw_uid, raw_value = data.split(":", 2)
        uid = int(raw_uid)
        if uid == int(DEVELOPER_ID):
            bot.answer_callback_query(call.id, "المطور لديه كل الحقوق دائماً.", show_alert=True); return True
        set_all_permissions(uid, raw_value == "1")
        bot.answer_callback_query(call.id, "تم تحديث كل الحقوق.")
        show_rights(bot, call, uid)
        return True
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


def handle_callback(bot, call):
    """Dispatch developer callbacks and surface/log unexpected errors."""
    try:
        return _handle_callback_impl(bot, call)
    except Exception:
        log.exception("Developer panel callback failed: %s", getattr(call, "data", None))
        try:
            bot.answer_callback_query(
                call.id,
                "❌ تعذر تنفيذ الزر. راجع سجل Railway.",
                show_alert=True,
            )
        except Exception:
            pass
        return True


def handle_input(bot, message):
    """Handle non-broadcast text input belonging to the developer panel."""
    if not is_developer(message.from_user.id):
        return False

    state = pending_input_get(message.from_user.id)
    if state in {"waiting_assistant_phone", "waiting_assistant_code", "waiting_assistant_password"} and message.chat.type != "private":
        bot.reply_to(message, "⛔ أكواد تسجيل الدخول وكلمة المرور مسموح بها في الخاص فقط.")
        return True
    if state in {"waiting_assistant_notice_text", "waiting_assistant_button"}:
        if message.chat.type != "private":
            bot.reply_to(message, "⛔ إعدادات زر المساعد تُعدّل من الخاص فقط.")
            return True
        value = (message.text or "").strip()
        if state == "waiting_assistant_notice_text":
            if not value:
                bot.reply_to(message, "❌ النص لا يمكن أن يكون فارغاً.")
                return True
            if len(value) > 1000:
                bot.reply_to(message, "❌ الحد الأقصى للنص 1000 حرف.")
                return True
            setting_set("ASSISTANT_BLOCKED_TEXT", value)
            pending_input_set(message.from_user.id, None)
            bot.reply_to(message, "✅ تم حفظ نص رسالة المساعد.")
            return True
        if value.upper() == "OFF":
            setting_set("ASSISTANT_BUTTON_NAME", "")
            setting_set("ASSISTANT_BUTTON_URL", "")
            pending_input_set(message.from_user.id, None)
            bot.reply_to(message, "✅ تمت إزالة زر المساعد.")
            return True
        parts = [part.strip() for part in value.split(":", 1)]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            bot.reply_to(message, "❌ الصيغة الصحيحة: اسم الزر : الرابط")
            return True
        name, url = parts
        if len(name) > 64 or len(url) > 2048:
            bot.reply_to(message, "❌ اسم الزر بحد أقصى 64 حرفاً والرابط 2048 حرفاً.")
            return True
        if not url.startswith(("https://", "http://", "tg://")):
            bot.reply_to(message, "❌ الرابط يجب أن يبدأ بـ https:// أو http:// أو tg://")
            return True
        setting_set("ASSISTANT_BUTTON_NAME", name)
        setting_set("ASSISTANT_BUTTON_URL", url)
        pending_input_set(message.from_user.id, None)
        bot.reply_to(message, "✅ تم حفظ زر المساعد ورابطه.")
        return True

    if state == "waiting_assistant_phone":
        if message.chat.type != "private":
            bot.reply_to(message, "⛔ أرسل رقم الهاتف في الخاص فقط.")
            return True
        if _ASSISTANT_POOL is None or not _ASSISTANT_POOL.available_slots():
            pending_input_set(message.from_user.id, None)
            bot.reply_to(message, "❌ لا توجد خانة فارغة للمساعدين.")
            return True
        try:
            begin_login(message.from_user.id, (message.text or "").strip())
            pending_input_set(message.from_user.id, "waiting_assistant_code")
            bot.reply_to(message, "📩 تم إرسال كود Telegram. أرسل الكود هنا خلال 10 دقائق. لا تشاركه مع أحد.")
        except Exception as exc:
            bot.reply_to(message, f"❌ تعذر بدء تسجيل الدخول: {exc}")
        return True

    if state == "waiting_assistant_code":
        code = (message.text or "").strip()
        # A phone number sent while we are waiting for the code must not
        # restart the login flow or trigger another Telegram code.
        if code.startswith("+"):
            bot.reply_to(
                message,
                "⚠️ الكود انرسل بالفعل. لا تعيد إرسال رقم الهاتف هنا؛ أرسل رمز Telegram فقط. "
                "إذا تريد البدء من جديد اضغط رجوع ثم «إضافة مساعد»."
            )
            return True
        if not code or not code.replace(" ", "").isdigit():
            bot.reply_to(message, "❌ أرسل رمز Telegram الرقمي فقط. بقيت عملية التسجيل الحالية فعالة.")
            return True
        try:
            session = complete_login(message.from_user.id, code=code)
            try:
                bot.delete_message(message.chat.id, message.message_id)
            except Exception:
                pass
            if session is None:
                pending_input_set(message.from_user.id, "waiting_assistant_password")
                bot.reply_to(message, "🔐 الحساب عليه تحقق بخطوتين. أرسل كلمة مرور التحقق هنا خلال 10 دقائق.")
                return True
            available = _ASSISTANT_POOL.available_slots() if _ASSISTANT_POOL else []
            if not available:
                raise RuntimeError("لا توجد خانة فارغة للمساعدين.")
            slot = available[0]
            _ASSISTANT_POOL.add_session(slot, session)
            pending_input_set(message.from_user.id, None)
            bot.reply_to(message, f"✅ تمت إضافة الحساب كمساعد {slot}. افتح لوحة المساعدين لتحديده أو تعيينه لمجموعة.")
        except Exception as exc:
            # Keep the flow alive for a mistyped code; the user can try again
            # without having to request another code or start from scratch.
            log.warning("Assistant login code step failed for developer %s: %s", message.from_user.id, type(exc).__name__)
            bot.reply_to(message, "❌ لم يتم قبول الرمز أو تعذّر إكمال الخطوة. تأكد من الرمز وأعد إرساله، أو اضغط رجوع ثم ابدأ إضافة المساعد من جديد.")
        return True

    if state == "waiting_assistant_password":
        password = (message.text or "").strip()
        try:
            if not password:
                bot.reply_to(message, "❌ أرسل كلمة مرور التحقق بخطوتين.")
                return True
            session = complete_login(message.from_user.id, password=password)
            try:
                bot.delete_message(message.chat.id, message.message_id)
            except Exception:
                pass
            if not session:
                raise RuntimeError("لم يتم إنشاء جلسة بعد.")
            available = _ASSISTANT_POOL.available_slots() if _ASSISTANT_POOL else []
            if not available:
                raise RuntimeError("لا توجد خانة فارغة للمساعدين.")
            slot = available[0]
            _ASSISTANT_POOL.add_session(slot, session)
            pending_input_set(message.from_user.id, None)
            bot.reply_to(message, f"✅ تمت إضافة الحساب كمساعد {slot}.")
        except Exception as exc:
            log.warning("Assistant login password step failed for developer %s: %s", message.from_user.id, type(exc).__name__)
            bot.reply_to(message, "❌ لم يتم قبول كلمة المرور أو تعذّر إكمال الخطوة. أعد إدخال كلمة المرور، أو اضغط رجوع وابدأ من جديد.")
        return True

    if state in {"waiting_assistant_chat_id", "waiting_assistant_unassign_chat_id"}:
        try:
            chat_id = int((message.text or "").strip())
            if chat_id >= 0:
                raise ValueError
        except ValueError:
            bot.reply_to(message, "❌ أرسل آيدي مجموعة صحيحاً (عادةً يبدأ بـ -100).")
            return True
        if _ASSISTANT_POOL is None:
            pending_input_set(message.from_user.id, None)
            bot.reply_to(message, "❌ نظام المساعدين غير جاهز.")
            return True
        if state == "waiting_assistant_unassign_chat_id":
            try:
                _ASSISTANT_POOL.unassign_chat(chat_id)
                pending_input_set(message.from_user.id, None)
                bot.reply_to(message, "✅ رجعت المجموعة إلى المساعد الافتراضي.")
            except Exception as exc:
                bot.reply_to(message, f"❌ تعذر إلغاء التعيين: {exc}")
            return True
        pending_input_set(message.from_user.id, None)
        markup = types.InlineKeyboardMarkup(row_width=1)
        if _ASSISTANT_POOL is None:
            bot.reply_to(message, "❌ نظام المساعدين غير جاهز.")
            return True
        for item in _ASSISTANT_POOL.slots_status():
            if item["configured"]:
                markup.add(types.InlineKeyboardButton(
                    f"🤖 تعيين المساعد {item['slot']}",
                    callback_data=f"dev_assistant_assign:{chat_id}:{item['slot']}"
                ))
        markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="dev_assistants"))
        bot.reply_to(message, f"اختر المساعد للمجموعة <code>{chat_id}</code>:", reply_markup=markup, parse_mode="HTML")
        return True

    if state == "waiting_add_admin":
        return add_admin_from_message(bot, message)
    if state == "waiting_remove_admin":
        return remove_admin_from_message(bot, message)
    if state == "waiting_fs_channel":
        return add_forced_channel_from_message(bot, message)

    if state == "play_button_labels":
        raw = (message.text or "").strip()
        parts = [part.strip() for part in raw.split("|")]
        keys = ("SKIP", "STOP", "PAUSE", "REWIND", "RESUME", "FORWARD", "TOP")
        defaults = ("⏭️ تخطي", "⏹️ إنهاء", "⏸️ إيقاف", "-10s", "▶️", "+10s", "🔝")
        if len(parts) != len(keys) or any(not part for part in parts):
            bot.reply_to(message, "❌ لازم ترسل 7 أسماء بالترتيب وتفصل بينها بعلامة |.")
            return True
        if any(len(part) > 64 for part in parts):
            bot.reply_to(message, "❌ كل اسم زر يجب ألا يتجاوز 64 حرفاً.")
            return True
        for key, value, default in zip(keys, parts, defaults):
            setting_set(f"PLAY_BTN_LABEL_{key}", "" if value.upper() == "DEFAULT" else value)
        pending_input_set(message.from_user.id, None)
        bot.reply_to(message, "✅ تم تحديث أسماء أزرار لوحة التشغيل.")
        return True

    if state in {"play_credit_pair", "play_music_pair", "chat_cmd_button", "jat_audio_button", "jat_audio_credit"}:
        text = (message.text or "").strip()
        parts = [part.strip() for part in text.split(":", 1)]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            bot.reply_to(message, "❌ الصيغة الصحيحة: اسم الزر : الرابط")
            return True
        name, url = parts
        if len(name) > 64:
            bot.reply_to(message, "❌ اسم الزر يجب ألا يتجاوز 64 حرفاً.")
            return True
        if len(url) > 2048:
            bot.reply_to(message, "❌ الرابط طويل جداً (الحد 2048 حرفاً).")
            return True
        if not url.startswith(("https://", "http://", "tg://")):
            bot.reply_to(message, "❌ الرابط يجب أن يبدأ بـ https:// أو http:// أو tg://")
            return True
        if state == "play_credit_pair":
            setting_set("PLAY_CREDIT_NAME", name)
            setting_set("PLAY_CREDIT_URL", url)
        elif state == "play_music_pair":
            setting_set("PLAY_MUSIC_BUTTON_NAME", name)
            setting_set("PLAY_MUSIC_BUTTON_URL", url)
        elif state == "jat_audio_button":
            setting_set("JAT_AUDIO_BUTTON_NAME", name)
            setting_set("JAT_AUDIO_BUTTON_URL", url)
        elif state == "jat_audio_credit":
            setting_set("JAT_AUDIO_CREDIT_NAME", name)
            setting_set("JAT_AUDIO_CREDIT_URL", url)
        else:
            setting_set("CHAT_COMMANDS_BUTTON_NAME", name)
            setting_set("CHAT_COMMANDS_BUTTON_URL", url)
        pending_input_set(message.from_user.id, None)
        bot.reply_to(message, "✅ تم حفظ الزر والرابط.")
        return True

    if state == "jat_audio_performer":
        performer = (message.text or "").strip()
        if not performer:
            bot.reply_to(message, "❌ اكتب اسم المصدر، مثال: من نينو")
            return True
        setting_set("JAT_AUDIO_PERFORMER", performer[:64])
        pending_input_set(message.from_user.id, None)
        bot.reply_to(message, "✅ تم حفظ مصدر الأغنية.")
        return True

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
    "register_developer_panel", "statistics_text", "admins_text", "playback_settings_markup", "show_playback_settings",
]
