"""Developer control panel with granular per-button/per-action permissions."""
from __future__ import annotations
from telebot import types
from config import DEVELOPER_ID
from database import (
    PERMISSION_GROUPS, PERMISSIONS, add_sudo, add_subscription, counts,
    delete_subscription, get_pending, get_permission_state, list_sudos,
    remove_sudo, set_all_permissions, set_pending, set_permission,
    setting_get, setting_set, clear_pending, subscriptions,
)

DEV_IDS = {int(DEVELOPER_ID)}


def is_developer(user_id: int) -> bool:
    return int(user_id) in DEV_IDS


def _pending(uid: int, mode: str | None):
    if mode is None:
        clear_pending(uid)
    else:
        set_pending(uid, mode, None, None)


def _uid(row):
    return int(row[0]) if isinstance(row, (tuple, list)) else int(row)


def _valid_url(value: str) -> bool:
    return value.startswith(("https://", "http://", "tg://"))


def main_markup():
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(types.InlineKeyboardButton("📊 الإحصائيات", callback_data="dev_stats"),
          types.InlineKeyboardButton("📣 الإذاعة", callback_data="dev_broadcast"))
    m.row(types.InlineKeyboardButton("👨‍💻 المشرفين", callback_data="dev_admins"),
          types.InlineKeyboardButton("🔐 الحقوق", callback_data="dev_rights"))
    m.row(types.InlineKeyboardButton("🔒 الاشتراك الإجباري", callback_data="dev_subs"),
          types.InlineKeyboardButton("🎵 لوحة التشغيل", callback_data="dev_playback"))
    m.row(types.InlineKeyboardButton("👤 لوحة /start", callback_data="dev_start_panel"),
          types.InlineKeyboardButton("❌ إغلاق", callback_data="dev_close"))
    return m


def _back(target="dev_main"):
    return types.InlineKeyboardMarkup([[types.InlineKeyboardButton("↩️ رجوع", callback_data=target)]])


def open_panel(bot, message):
    if not is_developer(message.from_user.id):
        return False
    bot.send_message(message.chat.id, "⚡ <b>لوحة المطور</b>\n\nتحكم كامل بكل أقسام البوت والحقوق.", reply_markup=main_markup(), parse_mode="HTML")
    return True


def _group_label(group):
    return {
        "playback":"🎵 التشغيل", "users":"👥 المستخدمون", "admins":"👨‍💻 المشرفون",
        "broadcast":"📣 الإذاعة", "subscriptions":"🔒 الاشتراك الإجباري", "stats":"📊 الإحصائيات",
        "user_panel":"👤 لوحة /start", "playback_panel":"🎛️ تخصيص التشغيل", "settings":"⚙️ الإعدادات",
        "sources":"🌐 المصادر", "channels":"📢 القنوات", "social":"🔗 الروابط",
    }.get(group, group)


def rights_markup(uid: int):
    m=types.InlineKeyboardMarkup(row_width=2)
    for group in PERMISSION_GROUPS:
        m.add(types.InlineKeyboardButton(_group_label(group), callback_data=f"perm_group:{uid}:{group}"))
    m.row(types.InlineKeyboardButton("🟢 تفعيل الكل", callback_data=f"perm_all:{uid}:1"),
          types.InlineKeyboardButton("🔴 تعطيل الكل", callback_data=f"perm_all:{uid}:0"))
    m.add(types.InlineKeyboardButton("↩️ المشرفين", callback_data="dev_admins_list"))
    return m


def group_markup(uid: int, group: str):
    state=get_permission_state(uid,int(DEVELOPER_ID)); m=types.InlineKeyboardMarkup(row_width=1)
    for key,label in PERMISSION_GROUPS[group].items():
        p=f"{group}.{key}"; icon="🟢" if state.get(p,False) else "🔴"
        m.add(types.InlineKeyboardButton(f"{icon} {label}", callback_data=f"perm_toggle:{uid}:{p}"))
    m.row(types.InlineKeyboardButton("🟢 تفعيل القسم", callback_data=f"perm_group_all:{uid}:{group}:1"),
          types.InlineKeyboardButton("🔴 تعطيل القسم", callback_data=f"perm_group_all:{uid}:{group}:0"))
    m.add(types.InlineKeyboardButton("↩️ كل الأقسام", callback_data=f"admin_rights:{uid}"))
    return m


def show_rights(bot, call, uid):
    bot.edit_message_text(f"🔐 <b>حقوق المشرف {uid}</b>\n\nكل زر/إجراء له حق مستقل.", call.message.chat.id, call.message.message_id, reply_markup=rights_markup(uid), parse_mode="HTML")


def _admins_menu_impl():
    m=types.InlineKeyboardMarkup(row_width=2)
    m.row(types.InlineKeyboardButton("➕ إضافة", callback_data="admin_add"), types.InlineKeyboardButton("➖ حذف", callback_data="admin_remove"))
    m.add(types.InlineKeyboardButton("📋 المشرفون", callback_data="dev_admins_list"), types.InlineKeyboardButton("🔐 الحقوق", callback_data="dev_pick_admin"))
    m.add(types.InlineKeyboardButton("↩️ الرئيسية", callback_data="dev_main"))
    return m


def show_admins(bot, call):
    rows=list_sudos(); text="👨‍💻 <b>المشرفون</b>\n\n"+ ("\n".join(f"• <code>{_uid(x)}</code>" for x in rows) if rows else "لا يوجد مشرفون.")
    m=types.InlineKeyboardMarkup(row_width=1)
    for x in rows:
        uid=_uid(x); m.add(types.InlineKeyboardButton(f"🔐 {uid}", callback_data=f"admin_rights:{uid}"))
    m.add(types.InlineKeyboardButton("↩️ رجوع", callback_data="dev_admins"))
    bot.edit_message_text(text,call.message.chat.id,call.message.message_id,reply_markup=m,parse_mode="HTML")


def show_pick_admin(bot, call):
    rows=list_sudos(); m=types.InlineKeyboardMarkup(row_width=1)
    for x in rows:
        uid=_uid(x); m.add(types.InlineKeyboardButton(f"👤 {uid}",callback_data=f"admin_rights:{uid}"))
    m.add(types.InlineKeyboardButton("↩️ رجوع",callback_data="dev_admins"))
    bot.edit_message_text("🔐 اختر المشرف الذي تريد تعديل حقوقه:",call.message.chat.id,call.message.message_id,reply_markup=m)


def begin(bot, call, mode, prompt, back="dev_main"):
    _pending(call.from_user.id,mode)
    bot.edit_message_text(prompt,call.message.chat.id,call.message.message_id,reply_markup=_back(back),parse_mode="HTML")


def playback_markup():
    m=types.InlineKeyboardMarkup(row_width=1)
    m.add(types.InlineKeyboardButton("✍️ الكتابة: الاسم : الرابط",callback_data="set_play_credit"))
    m.add(types.InlineKeyboardButton("🎵 زر الموسيقى: الاسم : الرابط",callback_data="set_play_music"))
    m.add(types.InlineKeyboardButton("🖼️ صورة لوحة التشغيل",callback_data="set_play_image"))
    m.add(types.InlineKeyboardButton("1️⃣ المصدر الأول: الاسم : الرابط",callback_data="set_source1"))
    m.add(types.InlineKeyboardButton("2️⃣ المصدر الثاني: الاسم : الرابط",callback_data="set_source2"))
    m.add(types.InlineKeyboardButton("↩️ رجوع",callback_data="dev_main")); return m


def start_panel_markup():
    m=types.InlineKeyboardMarkup(row_width=1)
    m.add(types.InlineKeyboardButton("📝 نص /start",callback_data="set_start_text"))
    m.add(types.InlineKeyboardButton("🖼️ صورة /start",callback_data="set_start_image"))
    m.add(types.InlineKeyboardButton("🔘 الزر الأول: الاسم : الرابط",callback_data="set_start_btn1"))
    m.add(types.InlineKeyboardButton("🔘 الزر الثاني: الاسم : الرابط",callback_data="set_start_btn2"))
    m.add(types.InlineKeyboardButton("↩️ رجوع",callback_data="dev_main")); return m


def subs_markup():
    m=types.InlineKeyboardMarkup(row_width=2)
    m.row(types.InlineKeyboardButton("➕ إضافة",callback_data="sub_add"),types.InlineKeyboardButton("➖ حذف",callback_data="sub_remove"))
    m.row(types.InlineKeyboardButton("📋 القائمة",callback_data="sub_list"),types.InlineKeyboardButton("🔛 تشغيل/إيقاف",callback_data="sub_toggle"))
    m.add(types.InlineKeyboardButton("↩️ رجوع",callback_data="dev_main")); return m


def broadcast_markup():
    m=types.InlineKeyboardMarkup(row_width=1)
    m.add(types.InlineKeyboardButton("👤 للأعضاء",callback_data="bc_users"))
    m.add(types.InlineKeyboardButton("🌐 للكل",callback_data="bc_all"))
    m.add(types.InlineKeyboardButton("📢 للقنوات",callback_data="bc_channels"))
    m.add(types.InlineKeyboardButton("↩️ رجوع",callback_data="dev_main")); return m


def _counts():
    try:
        u,c,a=counts(); return u,c,a
    except Exception: return 0,0,len(list_sudos())


def statistics_text():
    u,c,a=_counts(); return f"📊 <b>الإحصائيات</b>\n\n👤 الأعضاء: <code>{u}</code>\n💬 المحادثات: <code>{c}</code>\n👨‍💻 المشرفون: <code>{a}</code>"


def _set_pair(bot,message,state,text):
    # Accept both ``اسم الزر : الرابط`` and ``اسم الزر: الرابط``.
    # Split only at the first separator so ``https://`` remains intact.
    if " : " in text:
        name, url = [x.strip() for x in text.split(" : ", 1)]
    elif ":" in text:
        name, url = [x.strip() for x in text.split(":", 1)]
    else:
        bot.reply_to(message,"⚠️ الصيغة: اسم الزر : رابط الزر"); return True
    if not name or not url or not _valid_url(url):
        bot.reply_to(message,"⚠️ الاسم والرابط مطلوبان والرابط يجب أن يبدأ بـ https:// أو http:// أو tg://"); return True
    mapping={
        "set_play_credit":("PLAY_CREDIT_NAME","PLAY_CREDIT_URL"),
        "set_play_music":("PLAY_MUSIC_BUTTON_NAME","PLAY_MUSIC_BUTTON_URL"),
        "set_source1":("SOURCE1_NAME","SOURCE1_URL"),
        "set_source2":("SOURCE2_NAME","SOURCE2_URL"),
        "set_start_btn1":("CUSTOM_BTN1_NAME","CUSTOM_BTN1_URL"),
        "set_start_btn2":("CUSTOM_BTN2_NAME","CUSTOM_BTN2_URL"),
    }
    nk,uk=mapping[state]; setting_set(nk,name); setting_set(uk,url); _pending(message.from_user.id,None)
    bot.reply_to(message,"✅ تم الحفظ."); return True


def handle_input(bot,message):
    if not is_developer(message.from_user.id): return False
    state=get_pending(message.from_user.id)
    if not state: return False
    text=(message.text or "").strip()
    if state in {"waiting_add_admin","waiting_remove_admin"}:
        try: uid=int(text)
        except: bot.reply_to(message,"⚠️ أرسل ID رقمي صحيح."); return True
        if uid==int(DEVELOPER_ID): bot.reply_to(message,"⛔ لا يمكن تعديل المطور الأساسي."); return True
        if state=="waiting_add_admin":
            add_sudo(uid,message.from_user.id); bot.reply_to(message,"✅ تمت إضافة المشرف، وكل الحقوق تبدأ مغلقة.")
        else:
            remove_sudo(uid); bot.reply_to(message,"✅ تم حذف المشرف وحقوقه.")
        _pending(message.from_user.id,None); return True
    if state=="waiting_sub_add":
        parts=[x.strip() for x in text.split("|",2)]
        if len(parts)!=3: bot.reply_to(message,"⚠️ الصيغة: اسم القناة | المعرف | الرابط"); return True
        add_subscription(parts[0],parts[1],parts[2],True); bot.reply_to(message,"✅ تمت إضافة الاشتراك."); _pending(message.from_user.id,None); return True
    if state=="waiting_sub_remove":
        delete_subscription(text); bot.reply_to(message,"✅ تم الحذف إن وجد."); _pending(message.from_user.id,None); return True
    if state=="set_start_text":
        if not text: bot.reply_to(message,"⚠️ أرسل نصًا."); return True
        setting_set("START_TEXT",text); bot.reply_to(message,"✅ تم تحديث النص."); _pending(message.from_user.id,None); return True
    if state in {"set_play_credit","set_play_music","set_source1","set_source2","set_start_btn1","set_start_btn2"}:
        return _set_pair(bot,message,state,text)
    if state in {"set_start_image","set_play_image"}:
        if message.photo:
            key="START_IMAGE" if state=="set_start_image" else "PLAY_IMAGE"; setting_set(key+"_FILE_ID",message.photo[-1].file_id); setting_set(key+"_TYPE","photo")
        elif message.animation:
            key="START_IMAGE" if state=="set_start_image" else "PLAY_IMAGE"; setting_set(key+"_FILE_ID",message.animation.file_id); setting_set(key+"_TYPE","animation")
        else: bot.reply_to(message,"⚠️ أرسل صورة أو GIF."); return True
        bot.reply_to(message,"✅ تم تحديث الصورة."); _pending(message.from_user.id,None); return True
    return False


def handle_callback(bot,call):
    if not is_developer(call.from_user.id):
        if (call.data or "").startswith(("dev_","admin_","perm_","bc_","sub_","set_")):
            bot.answer_callback_query(call.id,"⛔ هذه اللوحة للمطور فقط.",show_alert=True); return True
        return False
    d=call.data or ""
    bot.answer_callback_query(call.id)
    if d=="dev_main":
        _pending(call.from_user.id,None); bot.edit_message_text("⚡ <b>لوحة المطور</b>",call.message.chat.id,call.message.message_id,reply_markup=main_markup(),parse_mode="HTML"); return True
    if d=="dev_close":
        _pending(call.from_user.id,None)
        try: bot.delete_message(call.message.chat.id,call.message.message_id)
        except: pass
        return True
    if d=="dev_stats":
        m=_back(); m.add(types.InlineKeyboardButton("🔄 تحديث",callback_data="dev_stats")); bot.edit_message_text(statistics_text(),call.message.chat.id,call.message.message_id,reply_markup=m,parse_mode="HTML"); return True
    if d=="dev_broadcast": bot.edit_message_text("📣 <b>الإذاعة</b>",call.message.chat.id,call.message.message_id,reply_markup=broadcast_markup(),parse_mode="HTML"); return True
    if d.startswith("bc_"):
        _pending(call.from_user.id,"broadcast_"+d[3:]); bot.edit_message_text("✍️ أرسل الآن المحتوى المطلوب إرساله.",call.message.chat.id,call.message.message_id,reply_markup=_back("dev_broadcast")); return True
    if d=="dev_admins": bot.edit_message_text("👨‍💻 <b>إدارة المشرفين</b>",call.message.chat.id,call.message.message_id,reply_markup=_admins_menu_impl(),parse_mode="HTML"); return True
    if d=="dev_admins_list": show_admins(bot,call); return True
    if d=="dev_pick_admin": show_pick_admin(bot,call); return True
    if d=="admin_add": begin(bot,call,"waiting_add_admin","🆔 أرسل Telegram ID للمشرف الجديد:","dev_admins"); return True
    if d=="admin_remove": begin(bot,call,"waiting_remove_admin","🆔 أرسل Telegram ID للمشرف المراد حذفه:","dev_admins"); return True
    if d.startswith("admin_rights:"):
        uid=int(d.split(":",1)[1]); show_rights(bot,call,uid); return True
    if d.startswith("perm_group:"):
        _,uid,group=d.split(":",2); bot.edit_message_text(f"🔐 <b>{_group_label(group)}</b>\nالمشرف: <code>{uid}</code>",call.message.chat.id,call.message.message_id,reply_markup=group_markup(int(uid),group),parse_mode="HTML"); return True
    if d.startswith("perm_toggle:"):
        _,uid,key=d.split(":",2); uid=int(uid); state=get_permission_state(uid,int(DEVELOPER_ID)); set_permission(uid,key,not state.get(key,False)); group=key.split(".",1)[0]; bot.edit_message_reply_markup(call.message.chat.id,call.message.message_id,reply_markup=group_markup(uid,group)); return True
    if d.startswith("perm_all:"):
        _,uid,val=d.split(":",2); set_all_permissions(int(uid),val=="1"); show_rights(bot,call,int(uid)); return True
    if d.startswith("perm_group_all:"):
        _,uid,group,val=d.split(":",3); uid=int(uid); enabled=val=="1"
        for key in PERMISSION_GROUPS[group]: set_permission(uid,f"{group}.{key}",enabled)
        bot.edit_message_reply_markup(call.message.chat.id,call.message.message_id,reply_markup=group_markup(uid,group)); return True
    if d=="dev_rights": show_pick_admin(bot,call); return True
    if d=="dev_subs": bot.edit_message_text("🔒 <b>الاشتراك الإجباري</b>",call.message.chat.id,call.message.message_id,reply_markup=subs_markup(),parse_mode="HTML"); return True
    if d=="sub_add": begin(bot,call,"waiting_sub_add","➕ أرسل: اسم القناة | المعرف | الرابط","dev_subs"); return True
    if d=="sub_remove": begin(bot,call,"waiting_sub_remove","➖ أرسل معرف القناة للحذف","dev_subs"); return True
    if d=="sub_toggle":
        setting_set("SUBS_ENABLED","OFF" if setting_get("SUBS_ENABLED")=="ON" else "ON"); bot.edit_message_text("🔒 <b>الاشتراك الإجباري</b>",call.message.chat.id,call.message.message_id,reply_markup=subs_markup(),parse_mode="HTML"); return True
    if d=="sub_list":
        rows=subscriptions(); text="📋 <b>القنوات</b>\n\n"+("\n".join(f"• {x[1]} — {x[2]}" for x in rows) if rows else "لا توجد قنوات."); bot.edit_message_text(text,call.message.chat.id,call.message.message_id,reply_markup=subs_markup(),parse_mode="HTML"); return True
    if d=="dev_playback":
        bot.edit_message_text("🎵 <b>لوحة التشغيل</b>\n\nكل اسم ورابط يدخلان معًا بالصيغة: <code>اسم الزر : رابط الزر</code>",call.message.chat.id,call.message.message_id,reply_markup=playback_markup(),parse_mode="HTML"); return True
    if d=="dev_start_panel": bot.edit_message_text("👤 <b>لوحة /start</b>\n\nالأسماء والروابط تُدخل معًا.",call.message.chat.id,call.message.message_id,reply_markup=start_panel_markup(),parse_mode="HTML"); return True
    prompts={
        "set_play_credit":"✍️ أرسل: اسم الكتابة : رابط الكتابة",
        "set_play_music":"🎵 أرسل: اسم الزر : رابط الزر",
        "set_source1":"1️⃣ أرسل: اسم المصدر : رابط المصدر",
        "set_source2":"2️⃣ أرسل: اسم المصدر : رابط المصدر",
        "set_start_btn1":"🔘 أرسل: اسم الزر : رابط الزر",
        "set_start_btn2":"🔘 أرسل: اسم الزر : رابط الزر",
        "set_start_text":"📝 أرسل نص /start الجديد",
        "set_start_image":"🖼️ أرسل صورة أو GIF /start",
        "set_play_image":"🖼️ أرسل صورة أو GIF لوحة التشغيل",
    }
    if d in prompts:
        begin(bot,call,d,prompts[d],"dev_playback" if d.startswith("set_play_") or d.startswith("set_source") else "dev_start_panel"); return True
    return False

# Legacy names imported by admin_panel.py and older integrations.
PERMISSION_LABELS = {k: v for k, v in PERMISSIONS.items()}

def admin_text() -> str:
    return "🛠️ لوحة الإدارة\n\nاختر القسم المطلوب:"

def developer_markup():
    k=types.InlineKeyboardMarkup(row_width=2)
    k.row(types.InlineKeyboardButton("👨‍💻 المشرفين",callback_data="adm_admins"),types.InlineKeyboardButton("🔐 الصلاحيات",callback_data="adm_permissions"))
    k.row(types.InlineKeyboardButton("🎛️ التشغيل",callback_data="adm_playback"),types.InlineKeyboardButton("📢 الاشتراك",callback_data="adm_subs"))
    k.row(types.InlineKeyboardButton("👤 لوحة العضو",callback_data="adm_user_panel"),types.InlineKeyboardButton("📣 الإذاعة",callback_data="adm_broadcast"))
    k.row(types.InlineKeyboardButton("📊 الإحصائيات",callback_data="adm_stats"),types.InlineKeyboardButton("👥 المستخدمون",callback_data="adm_users"))
    return k

def admins_menu():
    k=types.InlineKeyboardMarkup(row_width=2)
    k.row(types.InlineKeyboardButton("➕ إضافة",callback_data="adm_add"),types.InlineKeyboardButton("➖ حذف",callback_data="adm_remove"))
    k.row(types.InlineKeyboardButton("📋 القائمة",callback_data="adm_list"),types.InlineKeyboardButton("🔐 الصلاحيات",callback_data="adm_choose"))
    k.add(types.InlineKeyboardButton("↩️ الرئيسية",callback_data="adm_home"))
    return k

def permissions_select_markup():
    k=types.InlineKeyboardMarkup(row_width=1)
    rows=list_sudos()
    if not rows: k.add(types.InlineKeyboardButton("لا يوجد مشرفون",callback_data="adm_noop"))
    for x in rows:
        uid=_uid(x); k.add(types.InlineKeyboardButton(f"👤 {uid}",callback_data=f"perm_user:{uid}"))
    k.add(types.InlineKeyboardButton("↩️ رجوع",callback_data="adm_admins")); return k

def permission_markup(user_id):
    state=get_permission_state(int(user_id),int(DEVELOPER_ID)); k=types.InlineKeyboardMarkup(row_width=1)
    # Legacy screen shows granular permissions in a flat list, so older callbacks
    # remain usable while the new panel provides the hierarchical view.
    for key,label in PERMISSIONS.items():
        icon="🟢" if state.get(key,False) else "🔴"
        k.add(types.InlineKeyboardButton(f"{icon} {label}",callback_data=f"perm_toggle:{user_id}:{key}"))
    k.row(types.InlineKeyboardButton("↩️ اختيار مشرف",callback_data="adm_choose"),types.InlineKeyboardButton("🏠 الرئيسية",callback_data="adm_home"))
    return k

developer_panel_markup=main_markup
dev_main_panel=open_panel
dev_callbacks_handler=handle_callback
handle_admin_inputs=handle_input

def register_developer_panel(bot):
    @bot.message_handler(commands=["panel"])
    def _panel(message):
        open_panel(bot,message)

