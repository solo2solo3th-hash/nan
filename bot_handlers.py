"""All Bot API handlers; keeps main.py as a tiny bootstrap file."""
from __future__ import annotations
import logging
from urllib.parse import urlparse

from telebot import types

from admin_panel import playback_markup, subscriptions_markup, users_markup
from calls import VoiceCallRunner
from config import CONTROL_ADMINS_ONLY, DEVELOPER_ID, MAX_QUEUE_SIZE
from database import (
    PERMISSIONS, add_subscription, add_sudo, ban_user, banned_ids, clear_pending,
    counts, delete_subscription, get_pending, get_permission_state, has_permission,
    is_admin, is_banned, list_sudos, remove_sudo, save_chat, save_user,
    set_permission, set_pending, setting_get, setting_set, unban_user, user_ids,
    subscriptions,
)
from developer_panel import (
    admin_text, admins_menu, developer_markup, permission_markup, permissions_select_markup,
    handle_callback as handle_developer_panel_callback,
    handle_input as handle_developer_panel_input,
)
from downloader import download_audio
from member_panel import handle_start, handle_user_panel_callback
from music_player import MusicPlayer, Track
from subscriptions import mandatory_missing
from utils import alert, duration_text

log = logging.getLogger(__name__)


def admin_can(user_id: int, permission: str | None = None) -> bool:
    if not is_admin(user_id, DEVELOPER_ID):
        return False
    if user_id == DEVELOPER_ID or permission is None:
        return True
    return has_permission(user_id, permission, DEVELOPER_ID)


def require_group(bot, message) -> bool:
    if message.chat.type not in {"group", "supergroup"}:
        bot.reply_to(message, "❌ هذا الأمر يجب استخدامه داخل مجموعة.")
        return False
    return True


def user_can_play(message) -> bool:
    if is_banned(message.from_user.id):
        return False
    if CONTROL_ADMINS_ONLY:
        return admin_can(message.from_user.id, "playback")
    return True


def playback_controls():
    keyboard = types.InlineKeyboardMarkup(row_width=3)
    keyboard.row(types.InlineKeyboardButton("⏸️", callback_data="music_pause"), types.InlineKeyboardButton("▶️", callback_data="music_resume"), types.InlineKeyboardButton("⏭️", callback_data="music_skip"))
    keyboard.row(types.InlineKeyboardButton("⏹️", callback_data="music_stop"), types.InlineKeyboardButton("📋 القائمة", callback_data="music_queue"))
    return keyboard


def _send_now(bot, chat_id: int, track: Track | None) -> None:
    image_id = setting_get("PLAY_IMAGE_FILE_ID")
    image_type = setting_get("PLAY_IMAGE_TYPE") or "photo"
    text = "⏹️ انتهت قائمة التشغيل." if track is None else f"🎵 الآن: {track.title}\n⏱️ {duration_text(track.duration)}"
    markup = None if track is None else playback_controls()
    if image_id and track is not None:
        try:
            if image_type == "animation":
                bot.send_animation(chat_id, image_id, caption=text, reply_markup=markup)
            else:
                bot.send_photo(chat_id, image_id, caption=text, reply_markup=markup)
            return
        except Exception:
            log.exception("Failed to send playback image")
    bot.send_message(chat_id, text, reply_markup=markup)


def _send_queue(bot, player: MusicPlayer, chat_id: int) -> None:
    current, queue = player.status(chat_id)
    lines = [f"🎵 الآن: {current.title}" if current else "⏹️ لا يوجد تشغيل حالياً."]
    if queue:
        lines.append("\n📋 القائمة:")
        lines.extend(f"{i}. {track.title}" for i, track in enumerate(queue, 1))
    bot.send_message(chat_id, "\n".join(lines))


def _start_track(bot, calls: VoiceCallRunner, player: MusicPlayer, chat_id: int) -> Track | None:
    track = player.take_next(chat_id)
    if track is None:
        return None
    try:
        calls.play(chat_id, track.path)
    except Exception:
        player.stop(chat_id)
        raise
    _send_now(bot, chat_id, track)
    return track


async def _stream_end(bot, calls: VoiceCallRunner, player: MusicPlayer, chat_id: int) -> None:
    try:
        track = player.finish_and_take_next(chat_id)
        if track is None:
            bot.send_message(chat_id, "⏹️ انتهت قائمة التشغيل.")
            return
        await calls.aplay(chat_id, track.path)
        _send_now(bot, chat_id, track)
    except Exception:
        log.exception("Failed to advance queue after stream end in %s", chat_id)
        player.stop(chat_id)
        try:
            bot.send_message(chat_id, "❌ انتهى الصوت لكن تعذر تشغيل المقطع التالي.")
        except Exception:
            pass


def _set_pending_from_callback(call, mode: str, prompt: str, bot) -> None:
    set_pending(call.from_user.id, mode, call.message.chat.id, call.message.message_id)
    back = types.InlineKeyboardMarkup()
    back.add(types.InlineKeyboardButton("↩️ رجوع", callback_data="adm_home"))
    bot.edit_message_text(prompt, call.message.chat.id, call.message.message_id, reply_markup=back)


def _handle_pending(bot, message) -> bool:
    if handle_developer_panel_input(bot, message):
        return True
    pending = get_pending(message.from_user.id)
    if not pending:
        return False
    if not admin_can(message.from_user.id):
        clear_pending(message.from_user.id)
        return True
    mode = pending[0]
    text = (message.text or "").strip()
    if mode == "admin_add":
        try:
            uid = int(text)
            if uid == DEVELOPER_ID:
                raise ValueError
            add_sudo(uid, message.from_user.id)
            bot.reply_to(message, "✅ تمت إضافة المشرف. كل صلاحياته تبدأ مغلقة.")
            clear_pending(message.from_user.id)
        except ValueError:
            bot.reply_to(message, "❌ أرسل Telegram ID رقمي صحيح، ولا يمكن إضافة المطور كمشرف.")
        return True
    if mode == "admin_remove":
        try:
            uid = int(text)
            if uid == DEVELOPER_ID:
                raise ValueError
            remove_sudo(uid)
            bot.reply_to(message, "✅ تم حذف المشرف وصلاحياته.")
            clear_pending(message.from_user.id)
        except ValueError:
            bot.reply_to(message, "❌ معرف غير صالح أو محاولة حذف المطور.")
        return True
    if mode == "broadcast":
        if not text:
            bot.reply_to(message, "❌ أرسل نص الإذاعة.")
            return True
        ok = fail = 0
        for uid in user_ids():
            try:
                bot.send_message(uid, text)
                ok += 1
            except Exception:
                fail += 1
        bot.reply_to(message, f"📣 اكتملت الإذاعة.\n✅ {ok}\n❌ {fail}")
        clear_pending(message.from_user.id)
        return True
    if mode == "sub_add":
        parts = [p.strip() for p in text.split("|", 2)]
        if len(parts) != 3:
            bot.reply_to(message, "❌ الصيغة: اسم القناة | @channel أو -100... | رابط القناة")
        else:
            add_subscription(parts[0], parts[1], parts[2], True)
            bot.reply_to(message, "✅ تمت إضافة القناة.")
            clear_pending(message.from_user.id)
        return True
    if mode == "sub_remove":
        delete_subscription(text)
        bot.reply_to(message, "✅ تم الحذف إن كان موجوداً.")
        clear_pending(message.from_user.id)
        return True
    if mode in {"edit_start_text", "edit_btn1_input", "edit_btn2_input", "play_source1", "play_source1_url", "play_source2", "play_source2_url"}:
        if not text:
            bot.reply_to(message, "❌ أرسل قيمة صحيحة.")
            return True
        if mode == "edit_start_text":
            setting_set("START_TEXT", text)
        elif mode in {"edit_btn1_input", "edit_btn2_input"}:
            parts = text.split("|", 1)
            if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
                bot.reply_to(message, "❌ الصيغة: اسم الزر | الرابط")
                return True
            prefix = "CUSTOM_BTN1" if mode == "edit_btn1_input" else "CUSTOM_BTN2"
            setting_set(prefix + "_NAME", parts[0].strip())
            setting_set(prefix + "_URL", parts[1].strip())
        else:
            mapping = {
                "play_source1":"SOURCE1_NAME", "play_source1_url":"SOURCE1_URL",
                "play_source2":"SOURCE2_NAME", "play_source2_url":"SOURCE2_URL",
            }
            setting_set(mapping[mode], text)
        bot.reply_to(message, "✅ تم الحفظ.")
        clear_pending(message.from_user.id)
        return True
    if mode in {"edit_start_image", "play_set_image"}:
        if message.photo:
            key = "START_IMAGE" if mode == "edit_start_image" else "PLAY_IMAGE"
            setting_set(key + "_FILE_ID", message.photo[-1].file_id)
            setting_set(key + "_TYPE", "photo")
        elif message.animation:
            key = "START_IMAGE" if mode == "edit_start_image" else "PLAY_IMAGE"
            setting_set(key + "_FILE_ID", message.animation.file_id)
            setting_set(key + "_TYPE", "animation")
        else:
            bot.reply_to(message, "❌ أرسل صورة أو GIF.")
            return True
        bot.reply_to(message, "✅ تم تحديث الصورة.")
        clear_pending(message.from_user.id)
        return True
    if mode in {"user_ban", "user_unban"}:
        try:
            uid = int(text)
            if uid == DEVELOPER_ID:
                raise ValueError
            (ban_user if mode == "user_ban" else unban_user)(uid)
            bot.reply_to(message, "✅ تم التنفيذ.")
            clear_pending(message.from_user.id)
        except ValueError:
            bot.reply_to(message, "❌ معرف غير صالح أو لا يمكن حظر المطور.")
        return True
    return False


def register_handlers(bot, bot_username: str, calls: VoiceCallRunner, player: MusicPlayer) -> None:
    @bot.message_handler(commands=["start"])
    def start_handler(message):
        handle_start(bot, message, bot_username)

    @bot.message_handler(commands=["admin", "panel", "dev"])
    def admin_handler(message):
        save_user(message.from_user); save_chat(message.chat)
        if not admin_can(message.from_user.id):
            bot.reply_to(message, "❌ هذا الأمر مخصص للمطور والمشرفين فقط.")
            return
        bot.send_message(message.chat.id, admin_text(), reply_markup=developer_markup())

    @bot.message_handler(commands=["play", "p"])
    def play_handler(message):
        save_user(message.from_user); save_chat(message.chat)
        if not require_group(bot, message) or not user_can_play(message):
            if message.chat.type in {"group", "supergroup"} and not user_can_play(message):
                bot.reply_to(message, "🚫 ليست لديك صلاحية التشغيل.")
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            bot.reply_to(message, "🎵 استخدم: /play اسم الأغنية أو رابطها")
            return
        bot.send_chat_action(message.chat.id, "typing")
        status = bot.reply_to(message, "🔎 جاري البحث والتنزيل...")
        try:
            info, path, job = download_audio(parts[1])
            track = Track(
                title=info.get("title") or "Unknown",
                duration=int(info.get("duration") or 0),
                path=path, job=job,
                requester_id=message.from_user.id,
                source_url=info.get("webpage_url") or info.get("original_url") or "",
            )
            started, position, claimed = player.enqueue_or_claim(message.chat.id, track)
            if started and claimed is not None:
                try:
                    calls.play(message.chat.id, claimed.path)
                except Exception:
                    player.rollback_claim(message.chat.id, claimed)
                    raise
                _send_now(bot, message.chat.id, claimed)
            else:
                bot.send_message(message.chat.id, f"➕ تمت الإضافة: {track.title}\n📋 الترتيب: {position}")
            try: bot.delete_message(message.chat.id, status.message_id)
            except Exception: pass
        except RuntimeError as exc:
            bot.send_message(message.chat.id, "❌ قائمة التشغيل ممتلئة." if str(exc) == "QUEUE_FULL" else f"❌ فشل التشغيل: {exc}")
        except Exception:
            log.exception("play failed")
            bot.send_message(message.chat.id, "❌ صار خطأ أثناء البحث أو التشغيل.")

    @bot.message_handler(commands=["skip", "next"])
    def skip_handler(message):
        if not require_group(bot, message) or not user_can_play(message): return
        try:
            next_track = player.skip_and_take_next(message.chat.id)
            if next_track:
                calls.play(message.chat.id, next_track.path); _send_now(bot, message.chat.id, next_track)
            else:
                try: calls.leave(message.chat.id)
                except Exception: pass
                _send_now(bot, message.chat.id, None)
        except Exception:
            log.exception("skip failed"); bot.reply_to(message, "❌ تعذر التخطي.")

    @bot.message_handler(commands=["pause"])
    def pause_handler(message):
        if not require_group(bot, message) or not user_can_play(message) or player.current(message.chat.id) is None: return
        try: calls.pause(message.chat.id); bot.reply_to(message, "⏸️ تم الإيقاف المؤقت.")
        except Exception: log.exception("pause failed"); bot.reply_to(message, "❌ تعذر الإيقاف المؤقت.")

    @bot.message_handler(commands=["resume", "continue"])
    def resume_handler(message):
        if not require_group(bot, message) or not user_can_play(message) or player.current(message.chat.id) is None: return
        try: calls.resume(message.chat.id); bot.reply_to(message, "▶️ تم الاستئناف.")
        except Exception: log.exception("resume failed"); bot.reply_to(message, "❌ تعذر الاستئناف.")

    @bot.message_handler(commands=["stop", "leave"])
    def stop_handler(message):
        if not require_group(bot, message) or not user_can_play(message): return
        try: calls.leave(message.chat.id)
        except Exception: pass
        player.stop(message.chat.id); bot.reply_to(message, "⏹️ تم إيقاف التشغيل.")

    @bot.message_handler(commands=["queue"])
    def queue_handler(message):
        if require_group(bot, message): _send_queue(bot, player, message.chat.id)

    @bot.message_handler(content_types=["text", "photo", "animation", "audio", "document"])
    def message_router(message):
        if _handle_pending(bot, message): return
        save_user(message.from_user); save_chat(message.chat)

        # تشغيل ملف صوتي بالرد عليه: شغل / تشغيل
        text = (message.text or "").strip()
        normalized = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").strip()
        if normalized in {"شغل", "تشغيل"}:
            if not require_group(bot, message) or not user_can_play(message):
                if message.chat.type in {"group", "supergroup"} and not user_can_play(message):
                    bot.reply_to(message, "🚫 ليست لديك صلاحية التشغيل.")
                return

            replied = getattr(message, "reply_to_message", None)
            if replied is None:
                bot.reply_to(message, "↩️ رد على ملف صوتي أو MP3 واكتب: شغل")
                return

            audio = getattr(replied, "audio", None)
            document = getattr(replied, "document", None)
            file_obj = audio or (document if document and (getattr(document, "mime_type", "") or "").startswith("audio/") else None)
            if file_obj is None:
                bot.reply_to(message, "❌ الرسالة المردود عليها ليست ملفاً صوتياً أو MP3.")
                return

            try:
                file_info = bot.get_file(file_obj.file_id)
                raw = bot.download_file(file_info.file_path)
                if not raw:
                    raise RuntimeError("EMPTY_FILE")
                from config import DOWNLOAD_DIR
                from pathlib import Path
                import uuid
                DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
                suffix = Path(getattr(file_info, "file_path", "")).suffix or ".mp3"
                local_path = DOWNLOAD_DIR / f"telegram_{message.chat.id}_{uuid.uuid4().hex}{suffix}"
                local_path.write_bytes(raw)

                title = getattr(file_obj, "title", None) or getattr(file_obj, "file_name", None) or "ملف صوتي"
                duration = int(getattr(file_obj, "duration", 0) or 0)
                track = Track(
                    title=title, duration=duration, path=str(local_path), job=None,
                    requester_id=message.from_user.id, source_url="",
                )
                started, position, claimed = player.enqueue_or_claim(message.chat.id, track)
                if started and claimed is not None:
                    try:
                        calls.play(message.chat.id, claimed.path)
                    except Exception:
                        player.rollback_claim(message.chat.id, claimed)
                        raise
                    _send_now(bot, message.chat.id, claimed)
                else:
                    bot.reply_to(message, f"➕ تمت إضافة الملف إلى القائمة.\n📋 الترتيب: {position}")
            except Exception:
                log.exception("telegram audio reply playback failed")
                bot.reply_to(message, "❌ تعذر تحميل الملف أو تشغيله. تأكد أن الملف صوتي صالح وحاول مرة أخرى.")
            return

        if text and text.startswith("/"):
            bot.reply_to(message, "ℹ️ هذا الأمر غير موجود.")

    @bot.callback_query_handler(func=lambda call: True)
    def callback_router(call):
        data = call.data or ""
        if handle_developer_panel_callback(bot, call):
            return
        if handle_user_panel_callback(bot, call, lambda c,t,show=False: alert(bot,c,t,show)): return
        if data == "sub_check":
            missing = mandatory_missing(bot, call.from_user.id)
            alert(bot, call, "❌ ما زال الاشتراك مطلوباً." if missing else "✅ تم التحقق من الاشتراك.", bool(missing)); return
        if data == "adm_home":
            if not admin_can(call.from_user.id): alert(bot, call, "🚫 لا تملك الصلاحية.", True); return
            bot.edit_message_text(admin_text(), call.message.chat.id, call.message.message_id, reply_markup=developer_markup()); return
        if data == "adm_admins":
            if not admin_can(call.from_user.id, "admins"): alert(bot, call, "🚫 لا تملك الصلاحية.", True); return
            bot.edit_message_text("👨‍💻 إدارة المشرفين", call.message.chat.id, call.message.message_id, reply_markup=admins_menu()); return
        if data in {"adm_choose", "adm_permissions"}:
            if call.from_user.id != DEVELOPER_ID: alert(bot, call, "🚫 المطور فقط.", True); return
            bot.edit_message_text("🔐 اختر المشرف:", call.message.chat.id, call.message.message_id, reply_markup=permissions_select_markup()); return
        if data.startswith("perm_user:"):
            if call.from_user.id != DEVELOPER_ID: alert(bot, call, "🚫 المطور فقط.", True); return
            uid = int(data.split(":",1)[1]); bot.edit_message_text(f"🔐 صلاحيات المشرف {uid}", call.message.chat.id, call.message.message_id, reply_markup=permission_markup(uid)); return
        if data.startswith("perm_toggle:"):
            if call.from_user.id != DEVELOPER_ID: alert(bot, call, "🚫 المطور فقط.", True); return
            _, raw_uid, permission = data.split(":",2); uid=int(raw_uid)
            if not is_admin(uid, DEVELOPER_ID) or uid == DEVELOPER_ID:
                alert(bot, call, "❌ هذا ليس مشرفاً قابلاً للتعديل.", True); return
            state=get_permission_state(uid, DEVELOPER_ID); set_permission(uid, permission, not state.get(permission, False))
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=permission_markup(uid)); alert(bot, call, "✅ تم تحديث الصلاحية."); return
        if data == "adm_list":
            if not admin_can(call.from_user.id,"admins"): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            rows=list_sudos(); text="👥 المشرفون:\n\n"+ ("\n".join(f"• {uid}" for uid, in rows) if rows else "لا يوجد مشرفون.")
            bot.edit_message_text(text,call.message.chat.id,call.message.message_id,reply_markup=admins_menu()); return
        if data == "adm_noop": alert(bot,call,"ℹ️ لا يوجد مشرفون."); return
        if data in {"adm_add","adm_remove"}:
            if call.from_user.id != DEVELOPER_ID: alert(bot,call,"🚫 المطور فقط.",True); return
            _set_pending_from_callback(call, "admin_add" if data=="adm_add" else "admin_remove", "✍️ أرسل Telegram ID:", bot); return
        if data == "adm_playback":
            if not admin_can(call.from_user.id,"settings"): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            bot.edit_message_text("🎛️ تخصيص لوحة التشغيل",call.message.chat.id,call.message.message_id,reply_markup=playback_markup()); return
        if data == "adm_subs":
            if not admin_can(call.from_user.id,"subscriptions"): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            bot.edit_message_text("📢 إدارة الاشتراك الإجباري",call.message.chat.id,call.message.message_id,reply_markup=subscriptions_markup()); return
        if data == "adm_broadcast":
            if not admin_can(call.from_user.id,"broadcast"): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            _set_pending_from_callback(call,"broadcast","📣 أرسل نص الإذاعة الآن:",bot); return
        if data == "adm_stats":
            if not admin_can(call.from_user.id,"stats"): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            u,c,a=counts(); alert(bot,call,f"👥 المستخدمون: {u}\n💬 المحادثات: {c}\n👨‍💻 المشرفون: {a}",True); return
        if data == "adm_users":
            if not admin_can(call.from_user.id,"users"): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            bot.edit_message_text("👥 إدارة المستخدمين",call.message.chat.id,call.message.message_id,reply_markup=users_markup()); return
        if data in {"user_ban","user_unban"}:
            if not admin_can(call.from_user.id,"users"): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            _set_pending_from_callback(call,data,"✍️ أرسل Telegram ID:",bot); return
        if data == "user_banned":
            if not admin_can(call.from_user.id,"users"): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            ids=banned_ids(); alert(bot,call,"🚫 المحظورون:\n"+ ("\n".join(map(str,ids)) if ids else "لا يوجد."),True); return
        if data in {"sub_add","sub_remove"}:
            if not admin_can(call.from_user.id,"subscriptions"): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            prompt="➕ أرسل: اسم القناة | @channel أو -100... | رابط القناة" if data=="sub_add" else "🗑️ أرسل @channel أو chat ID للحذف:"
            _set_pending_from_callback(call,data,prompt,bot); return
        if data == "sub_list":
            rows=subscriptions(); text="📋 القنوات:\n\n"+ ("\n".join(f"• {title} — {target}" for _,title,target,_,_ in rows) if rows else "لا توجد قنوات.")
            bot.edit_message_text(text,call.message.chat.id,call.message.message_id,reply_markup=subscriptions_markup()); return
        if data == "sub_toggle":
            if not admin_can(call.from_user.id,"subscriptions"): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            current=setting_get("SUBS_ENABLED")=="ON"; setting_set("SUBS_ENABLED","OFF" if current else "ON"); alert(bot,call,"🔛 تم تبديل الاشتراك الإجباري."); return
        if data in {"play_source1","play_source1_url","play_source2","play_source2_url","play_set_image"}:
            if not admin_can(call.from_user.id,"settings"): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            prompts={"play_source1":"📝 اسم المصدر الأول:","play_source1_url":"🔗 رابط المصدر الأول:","play_source2":"📝 اسم المصدر الثاني:","play_source2_url":"🔗 رابط المصدر الثاني:","play_set_image":"🖼️ أرسل صورة أو GIF لوحة التشغيل:"}
            _set_pending_from_callback(call,data,prompts[data],bot); return
        if data.startswith("music_"):
            if not admin_can(call.from_user.id,"playback") and CONTROL_ADMINS_ONLY: alert(bot,call,"🚫 ليست لديك صلاحية التشغيل.",True); return
            try:
                chat_id=call.message.chat.id
                if data=="music_skip":
                    next_track=player.skip_and_take_next(chat_id)
                    if next_track: calls.play(chat_id,next_track.path); _send_now(bot,chat_id,next_track)
                    else:
                        try: calls.leave(chat_id)
                        except Exception: pass
                        _send_now(bot,chat_id,None)
                elif data=="music_stop":
                    try: calls.leave(chat_id)
                    except Exception: pass
                    player.stop(chat_id); bot.send_message(chat_id,"⏹️ تم الإيقاف.")
                elif data=="music_pause": calls.pause(chat_id); bot.send_message(chat_id,"⏸️ تم الإيقاف المؤقت.")
                elif data=="music_resume": calls.resume(chat_id); bot.send_message(chat_id,"▶️ تم الاستئناف.")
                elif data=="music_queue": _send_queue(bot,player,chat_id)
                alert(bot,call,"تم.")
            except Exception: log.exception("playback callback failed"); alert(bot,call,"❌ تعذر التنفيذ.",True)
            return
        alert(bot,call,"ℹ️ الطلب غير معروف.")

    calls.set_stream_end_handler(lambda chat_id: _stream_end(bot, calls, player, chat_id))
