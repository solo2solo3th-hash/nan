"""All Bot API handlers; keeps main.py as a tiny bootstrap file."""
from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone
import threading
import html
from telebot import types

from admin_panel import playback_markup, subscriptions_markup, users_markup
from calls import VoiceCallRunner
from config import CONTROL_ADMINS_ONLY, DEVELOPER_ID
from database import (
    PERMISSIONS, add_subscription, add_sudo, ban_user, banned_ids, clear_pending,
    counts, delete_subscription, get_pending, get_permission_state, has_permission,
    is_admin, is_banned, list_sudos, remove_sudo, save_chat, save_user,
    set_permission, set_pending, setting_get, setting_set, unban_user, user_ids,
    subscriptions, chat_ids_by_type,
)
from developer_panel import (
    admin_text, admins_menu, developer_markup, permission_markup, permissions_select_markup,
    open_panel, handle_callback as handle_developer_panel_callback,
    handle_input as handle_developer_panel_input,
)
from downloader import cleanup_job, download_audio
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


def is_group_admin(bot, chat_id: int, user_id: int) -> bool:
    """Check whether the user is an admin/owner of this exact group."""
    if int(user_id) == int(DEVELOPER_ID):
        return True
    try:
        member = bot.get_chat_member(int(chat_id), int(user_id))
        return getattr(member, "status", "") in {"administrator", "creator"}
    except Exception:
        log.exception("group admin check failed: chat=%s user=%s", chat_id, user_id)
        return False


def group_control_allowed(bot, chat_id: int, user_id: int) -> bool:
    """Only this group's admins/owner (plus developer) get group playback control."""
    return is_group_admin(bot, chat_id, user_id)


def require_group(bot, message) -> bool:
    if message.chat.type not in {"group", "supergroup"}:
        bot.reply_to(message, "❌ هذا الأمر يجب استخدامه داخل مجموعة.")
        return False
    return True


def user_can_play(message, action: str = "play", bot=None) -> bool:
    if is_banned(message.from_user.id):
        return False
    # Group admins always control playback for their own group. This does not
    # grant them access to the developer panel or global bot administration.
    if bot is not None and message.chat.type in {"group", "supergroup"}:
        if group_control_allowed(bot, message.chat.id, message.from_user.id):
            return True
    if message.from_user.id == DEVELOPER_ID:
        return True
    if is_admin(message.from_user.id, DEVELOPER_ID):
        if action == "play":
            return admin_can(message.from_user.id, "playback.play") and admin_can(message.from_user.id, "playback.join")
        if action == "stop":
            return admin_can(message.from_user.id, "playback.stop") and admin_can(message.from_user.id, "playback.leave")
        return admin_can(message.from_user.id, f"playback.{action}")
    if CONTROL_ADMINS_ONLY:
        return False
    return True


def playback_controls(chat_id: int, user_id: int | None = None, bot=None):
    keyboard = types.InlineKeyboardMarkup(row_width=3)

    def allowed(action: str) -> bool:
        if user_id is None or user_id == DEVELOPER_ID:
            return True
        # A Telegram administrator of this exact group always gets the local
        # playback controls, even when CONTROL_ADMINS_ONLY is enabled.
        if bot is not None and group_control_allowed(bot, chat_id, user_id):
            return True
        if is_admin(user_id, DEVELOPER_ID):
            if action == "stop":
                return admin_can(user_id, "playback.stop") and admin_can(user_id, "playback.leave")
            return admin_can(user_id, f"playback.{action}")
        return not CONTROL_ADMINS_ONLY

    row1 = []
    if allowed("skip"):
        row1.append(types.InlineKeyboardButton("تخطي", callback_data="music_skip"))
    if allowed("stop"):
        row1.append(types.InlineKeyboardButton("إنهاء", callback_data="music_stop"))
    if allowed("pause"):
        row1.append(types.InlineKeyboardButton("إيقاف", callback_data="music_pause"))
    if row1:
        keyboard.row(*row1)

    row2 = []
    if allowed("seek"):
        row2.append(types.InlineKeyboardButton("-10s", callback_data="music_seek_-10"))
    if allowed("resume"):
        row2.append(types.InlineKeyboardButton("▶️", callback_data="music_resume"))
    if allowed("seek"):
        row2.append(types.InlineKeyboardButton("+10s", callback_data="music_seek_10"))
    if row2:
        keyboard.row(*row2)

    # These two buttons belong to the playback card and are controlled from
    # the developer's Playback panel. /start buttons are stored separately.
    for name_key, url_key in (("PLAY_CREDIT_NAME", "PLAY_CREDIT_URL"),
                              ("PLAY_MUSIC_BUTTON_NAME", "PLAY_MUSIC_BUTTON_URL")):
        button_name = (setting_get(name_key) or "").strip()
        button_url = (setting_get(url_key) or "").strip()
        if button_name and button_url.startswith(("https://", "http://", "tg://")):
            keyboard.row(types.InlineKeyboardButton(button_name, url=button_url))

    keyboard.row(types.InlineKeyboardButton("❌", callback_data="music_close"))
    return keyboard

def _playback_caption(track: Track | None) -> str:
    if track is None:
        return "⏹️ انتهت قائمة التشغيل."
    title = html.escape(track.title or "غير معروف")
    duration = html.escape(duration_text(track.duration))
    return f"حبيب ياسر شغنالك: <b>{title}</b>\nمدة التشغيل: <b>{duration}</b>"

def _send_now(bot, chat_id: int, track: Track | None) -> None:
    image_id = setting_get("PLAY_IMAGE_FILE_ID")
    image_type = setting_get("PLAY_IMAGE_TYPE") or "photo"
    text = _playback_caption(track)
    markup = None if track is None else playback_controls(chat_id)
    kwargs = {"reply_markup": markup, "parse_mode": "HTML"}
    if image_id and track is not None:
        try:
            if image_type == "animation":
                bot.send_animation(chat_id, image_id, caption=text, **kwargs)
            else:
                bot.send_photo(chat_id, image_id, caption=text, **kwargs)
            return
        except Exception:
            log.exception("Failed to send playback image")
    bot.send_message(chat_id, text, **kwargs)


def _replace_voice_stream(calls: VoiceCallRunner, player: MusicPlayer, chat_id: int, path: str, start_seconds: int = 0) -> None:
    """Replace a currently playing stream and mark possible stale end events."""
    player.mark_stream_replaced(chat_id)
    try:
        _ensure_assistant_joined(_VOICE_BOT, calls, chat_id)
        calls.play(chat_id, path, start_seconds=start_seconds)
        player.finalize_replacement(chat_id)
    except Exception:
        # The replacement did not happen; remove the stale-event marker so a
        # later genuine StreamEnded event is handled normally.
        player.consume_stale_stream_end(chat_id, grace_seconds=0)
        raise

def _send_queue(bot, player: MusicPlayer, chat_id: int) -> None:
    current, queue = player.status(chat_id)
    lines = [f"🎵 الآن: {current.title}" if current else "⏹️ لا يوجد تشغيل حالياً."]
    if queue:
        lines.append("\n📋 القائمة:")
        lines.extend(f"{i}. {track.title}" for i, track in enumerate(queue, 1))
    bot.send_message(chat_id, "\n".join(lines))


_VOICE_BOT = None
_ASSISTANT_JOIN_LOCKS: dict[int, threading.Lock] = {}
_ASSISTANT_JOIN_LOCKS_GUARD = threading.Lock()


def _assistant_join_lock(chat_id: int) -> threading.Lock:
    with _ASSISTANT_JOIN_LOCKS_GUARD:
        return _ASSISTANT_JOIN_LOCKS.setdefault(int(chat_id), threading.Lock())


def _ensure_assistant_joined(bot, calls: VoiceCallRunner, chat_id: int) -> None:
    """Automatically bring the authorized assistant into this group.

    Only one join attempt is allowed per group at a time. The invite is
    one-use and short-lived, then revoked after the join attempt.
    """
    with _assistant_join_lock(chat_id):
        try:
            try:
                calls.ensure_assistant_member(chat_id, "")
                return
            except RuntimeError as exc:
                if str(exc) != "ASSISTANT_INVITE_REQUIRED":
                    raise

            invite = bot.create_chat_invite_link(
                chat_id,
                name="music-assistant",
                expire_date=datetime.now(timezone.utc) + timedelta(minutes=2),
                member_limit=1,
                creates_join_request=False,
            )
            link = getattr(invite, "invite_link", "") or ""
            if not link:
                raise RuntimeError("ASSISTANT_INVITE_CREATE_FAILED")
            try:
                calls.ensure_assistant_member(chat_id, link)
            finally:
                try:
                    bot.revoke_chat_invite_link(chat_id, link)
                except Exception:
                    log.debug("Could not revoke assistant invite for %s", chat_id, exc_info=True)
        except Exception as exc:
            log.exception("Automatic assistant join failed for %s", chat_id)
            raise RuntimeError(f"ASSISTANT_AUTO_JOIN_FAILED:{exc}") from exc


def _start_track(bot, calls: VoiceCallRunner, player: MusicPlayer, chat_id: int) -> Track | None:
    track = player.take_next(chat_id)
    if track is None:
        return None
    try:
        _ensure_assistant_joined(bot, calls, chat_id)
        calls.play(chat_id, track.path)
    except Exception:
        player.stop(chat_id)
        raise
    _send_now(bot, chat_id, track)
    return track


async def _stream_end(bot, calls: VoiceCallRunner, player: MusicPlayer, chat_id: int) -> None:
    try:
        if player.consume_stale_stream_end(chat_id):
            log.debug("Ignoring stale StreamEnded event after intentional stream replacement: %s", chat_id)
            return
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
    required_pending = {
        "admin_add":"admins.add", "admin_remove":"admins.remove",
        "broadcast":"broadcast.send", "sub_add":"subscriptions.add",
        "sub_remove":"subscriptions.remove", "edit_start_text":"user_panel.start_text",
        "edit_start_image":"user_panel.start_image", "edit_btn1_input":"user_panel.button1",
        "edit_btn2_input":"user_panel.button2", "play_source1":"playback_panel.source1",
        "play_source1_url":"playback_panel.source1", "play_source2":"playback_panel.source2",
        "play_source2_url":"playback_panel.source2", "play_set_image":"playback_panel.image",
        "user_ban":"users.ban", "user_unban":"users.unban",
    }
    required = required_pending.get(mode)
    if required and not admin_can(message.from_user.id, required):
        clear_pending(message.from_user.id); bot.reply_to(message,"🚫 ليست لديك صلاحية هذا الإجراء."); return True
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
    if mode in {"broadcast", "broadcast_users", "broadcast_all", "broadcast_channels"}:
        if not any([message.text, message.photo, message.animation, message.video, message.document, message.audio, message.voice, message.video_note]):
            bot.reply_to(message, "❌ أرسل المحتوى المطلوب إذاعته.")
            return True
        if mode in {"broadcast", "broadcast_users"}:
            targets = user_ids()
        elif mode == "broadcast_channels":
            targets = chat_ids_by_type(("channel",))
        else:
            targets = user_ids() + chat_ids_by_type(("group", "supergroup", "channel"))
        ok = fail = 0
        for target in dict.fromkeys(targets):
            try:
                bot.copy_message(target, message.chat.id, message.message_id)
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
    global _VOICE_BOT
    _VOICE_BOT = bot
    try:
        _BOT_ID = int(bot.get_me().id)
    except Exception:
        _BOT_ID = 0

    @bot.message_handler(content_types=["new_chat_members"])
    def bot_added_to_group_notice(message):
        """Notify the developer when this bot is added to a group."""
        if message.chat.type not in {"group", "supergroup"}:
            return
        members = getattr(message, "new_chat_members", None) or []
        if not any(int(getattr(member, "id", 0) or 0) == _BOT_ID for member in members):
            return
        inviter = getattr(message, "from_user", None)
        inviter_name = getattr(inviter, "first_name", "") or "غير معروف"
        inviter_username = getattr(inviter, "username", None)
        inviter_text = f"{inviter_name} (@{inviter_username})" if inviter_username else inviter_name
        title = getattr(message.chat, "title", "مجموعة") or "مجموعة"
        username = getattr(message.chat, "username", None)
        public_link = f"https://t.me/{username}" if username else "غير متاح (مجموعة خاصة)"
        text = (
            "🤖 <b>تمت إضافة البوت إلى مجموعة</b>\n\n"
            f"📌 الاسم: <b>{html.escape(title)}</b>\n"
            f"🆔 Chat ID: <code>{int(message.chat.id)}</code>\n"
            f"👤 بواسطة: <b>{html.escape(inviter_text)}</b>\n"
            f"🔗 الرابط: {html.escape(public_link)}"
        )
        try:
            bot.send_message(DEVELOPER_ID, text, parse_mode="HTML")
        except Exception:
            log.exception("Failed to notify developer about bot being added to group")

    @bot.message_handler(commands=["start"])
    def start_handler(message):
        handle_start(bot, message, bot_username)

    @bot.message_handler(commands=["admin", "panel", "dev"])
    def admin_handler(message):
        save_user(message.from_user); save_chat(message.chat)
        if message.from_user.id == DEVELOPER_ID:
            open_panel(bot, message)
            return
        if not admin_can(message.from_user.id):
            bot.reply_to(message, "❌ هذا الأمر مخصص للمطور والمشرفين فقط.")
            return
        bot.send_message(message.chat.id, admin_text(), reply_markup=developer_markup())

    @bot.message_handler(commands=["control", "voice", "اتصال", "لوحة_الاتصال"])
    def group_control_handler(message):
        save_user(message.from_user); save_chat(message.chat)
        if not require_group(bot, message):
            return
        if not group_control_allowed(bot, message.chat.id, message.from_user.id):
            bot.reply_to(message, "🚫 لوحة الاتصال خاصة بمشرفي هذه المجموعة.")
            return
        current = player.current(message.chat.id)
        if current is None:
            bot.reply_to(message, "🔊 لا توجد أغنية قيد التشغيل حالياً.")
            return
        text = _playback_caption(current)
        markup = playback_controls(message.chat.id, message.from_user.id, bot)
        image_id = setting_get("PLAY_IMAGE_FILE_ID")
        image_type = setting_get("PLAY_IMAGE_TYPE") or "photo"
        try:
            if image_id and image_type == "animation":
                bot.send_animation(message.chat.id, image_id, caption=text, reply_markup=markup, parse_mode="HTML")
            elif image_id:
                bot.send_photo(message.chat.id, image_id, caption=text, reply_markup=markup, parse_mode="HTML")
            else:
                bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")
        except Exception:
            log.exception("group control panel send failed")
            bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")

    @bot.message_handler(commands=["play", "p"])
    def play_handler(message):
        save_user(message.from_user); save_chat(message.chat)
        if not require_group(bot, message) or not user_can_play(message, "play", bot):
            if message.chat.type in {"group", "supergroup"} and not user_can_play(message, "play", bot):
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
                    _ensure_assistant_joined(bot, calls, message.chat.id)
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
        except Exception as exc:
            log.exception("play failed")
            if str(exc).startswith("ASSISTANT_AUTO_JOIN_FAILED:"):
                bot.send_message(message.chat.id, "❌ ما قدرت أضيف/أدخل المساعد تلقائيًا. تأكد أن البوت مشرف بالمجموعة وعنده صلاحية دعوة الأعضاء.")
            else:
                bot.send_message(message.chat.id, "❌ صار خطأ أثناء البحث أو التشغيل.")

    def _natural_query(text: str, words: tuple[str, ...]) -> str | None:
        raw = (text or "").strip()
        lowered = raw.casefold()
        for word in words:
            prefix = word.casefold() + " "
            if lowered.startswith(prefix):
                query = raw[len(word):].strip()
                return query or None
        return None

    def _play_query(bot, message, query: str) -> None:
        if not require_group(bot, message) or not user_can_play(message, "play", bot):
            if message.chat.type in {"group", "supergroup"} and not user_can_play(message, "play", bot):
                bot.reply_to(message, "🚫 ليست لديك صلاحية التشغيل.")
            return
        bot.send_chat_action(message.chat.id, "typing")
        status = bot.reply_to(message, "🔎 جاري البحث وتجهيز الأغنية...")
        try:
            info, path, job = download_audio(query)
            track = Track(
                title=info.get("title") or "Unknown",
                duration=int(info.get("duration") or 0),
                path=path,
                job=job,
                requester_id=message.from_user.id,
                source_url=info.get("webpage_url") or info.get("original_url") or "",
            )
            started, position, claimed = player.enqueue_or_claim(message.chat.id, track)
            if started and claimed is not None:
                try:
                    _ensure_assistant_joined(bot, calls, message.chat.id)
                    calls.play(message.chat.id, claimed.path)
                except Exception:
                    player.rollback_claim(message.chat.id, claimed)
                    raise
                _send_now(bot, message.chat.id, claimed)
            else:
                bot.send_message(message.chat.id, f"➕ تمت الإضافة: {track.title}\n📋 الترتيب: {position}")
            try:
                bot.delete_message(message.chat.id, status.message_id)
            except Exception:
                pass
        except RuntimeError as exc:
            bot.send_message(
                message.chat.id,
                "❌ قائمة التشغيل ممتلئة." if str(exc) == "QUEUE_FULL" else f"❌ فشل التشغيل: {exc}",
            )
        except Exception as exc:
            log.exception("natural play failed")
            if str(exc).startswith("ASSISTANT_AUTO_JOIN_FAILED:"):
                bot.send_message(message.chat.id, "❌ ما قدرت أضيف/أدخل المساعد تلقائيًا. تأكد أن البوت مشرف بالمجموعة وعنده صلاحية دعوة الأعضاء.")
            else:
                bot.send_message(message.chat.id, "❌ صار خطأ أثناء البحث أو التشغيل.")

    def _download_to_chat(bot, message, query: str) -> None:
        if not user_can_play(message, "download", bot):
            bot.reply_to(message, "🚫 ليست لديك صلاحية التنزيل.")
            return
        bot.send_chat_action(message.chat.id, "upload_audio")
        status = bot.reply_to(message, "⬇️ جاري البحث وتحميل الملف...")
        job = None
        try:
            info, path, job = download_audio(query)
            title = info.get("title") or "Audio"
            duration = int(info.get("duration") or 0)
            with open(path, "rb") as audio_file:
                bot.send_audio(
                    message.chat.id,
                    audio_file,
                    caption=f"🎵 {title}",
                    title=title,
                    duration=duration or None,
                )
            try:
                bot.delete_message(message.chat.id, status.message_id)
            except Exception:
                pass
        except Exception:
            log.exception("natural download failed")
            bot.send_message(message.chat.id, "❌ تعذر تحميل وإرسال الملف.")
        finally:
            cleanup_job(job)

    @bot.message_handler(func=lambda m: bool(
        getattr(m, "text", None)
        and _natural_query(m.text, ("شغل", "تشغيل", "تحميل"))
    ), content_types=["text"])
    def natural_voice_handler(message):
        query = _natural_query(message.text, ("شغل", "تشغيل", "تحميل"))
        if query:
            _play_query(bot, message, query)

    @bot.message_handler(func=lambda m: bool(
        getattr(m, "text", None)
        and _natural_query(m.text, ("نزل", "تنزيل", "يوت"))
    ), content_types=["text"])
    def natural_download_handler(message):
        query = _natural_query(message.text, ("نزل", "تنزيل", "يوت"))
        if query:
            _download_to_chat(bot, message, query)

    @bot.message_handler(commands=["skip", "next"])
    def skip_handler(message):
        if not require_group(bot, message) or not user_can_play(message, "skip", bot): return
        try:
            next_track = player.skip_and_take_next(message.chat.id)
            if next_track:
                _replace_voice_stream(calls, player, message.chat.id, next_track.path); _send_now(bot, message.chat.id, next_track)
            else:
                try: calls.leave(message.chat.id)
                except Exception: pass
                _send_now(bot, message.chat.id, None)
        except Exception:
            log.exception("skip failed"); bot.reply_to(message, "❌ تعذر التخطي.")

    @bot.message_handler(commands=["pause"])
    def pause_handler(message):
        if not require_group(bot, message) or not user_can_play(message, "pause", bot) or player.current(message.chat.id) is None: return
        try: calls.pause(message.chat.id); position = player.set_paused(message.chat.id, True); bot.reply_to(message, f"⏸️ تم الإيقاف المؤقت عند {duration_text(position)}.")
        except Exception: log.exception("pause failed"); bot.reply_to(message, "❌ تعذر الإيقاف المؤقت.")

    @bot.message_handler(commands=["resume", "continue"])
    def resume_handler(message):
        if not require_group(bot, message) or not user_can_play(message, "resume", bot) or player.current(message.chat.id) is None: return
        try: calls.resume(message.chat.id); position = player.set_paused(message.chat.id, False); bot.reply_to(message, f"▶️ تم الاستئناف من {duration_text(position)}.")
        except Exception: log.exception("resume failed"); bot.reply_to(message, "❌ تعذر الاستئناف.")

    @bot.message_handler(commands=["stop", "leave"])
    def stop_handler(message):
        if not require_group(bot, message) or not user_can_play(message, "stop", bot): return
        try: calls.leave(message.chat.id)
        except Exception: pass
        player.stop(message.chat.id); bot.reply_to(message, "⏹️ تم إيقاف التشغيل.")

    @bot.message_handler(commands=["volume"])
    def volume_handler(message):
        if not require_group(bot, message):
            return
        if not user_can_play(message, "volume", bot):
            bot.reply_to(message, "🚫 ليست لديك صلاحية التحكم بالصوت.")
            return
        parts=(message.text or "").split(maxsplit=1)
        if len(parts)<2 or not parts[1].strip().lstrip("-").isdigit():
            bot.reply_to(message, "🔊 استخدم: /volume 1-200")
            return
        value=max(1,min(200,int(parts[1].strip())))
        try:
            calls.volume(message.chat.id,value)
            bot.reply_to(message,f"🔊 تم ضبط الصوت: {value}")
        except Exception:
            log.exception("volume failed")
            bot.reply_to(message,"❌ تعذر تغيير الصوت.")

    @bot.message_handler(commands=["queue"])
    def queue_handler(message):
        if require_group(bot, message):
            if not user_can_play(message, "queue", bot):
                bot.reply_to(message, "🚫 ليست لديك صلاحية عرض القائمة.")
                return
            _send_queue(bot, player, message.chat.id)

    # Pending developer/admin input MUST be handled before the generic text
    # and media handlers. This prevents a waiting panel action from being
    # swallowed by another handler.
    @bot.message_handler(
        func=lambda m: bool(get_pending(m.from_user.id)),
        content_types=["text", "photo", "animation", "video", "document", "audio", "voice", "video_note"],
    )
    def pending_input_router(message):
        _handle_pending(bot, message)

    @bot.message_handler(content_types=["text", "photo", "animation", "video", "document", "audio", "voice", "video_note"])
    def message_router(message):
        save_user(message.from_user); save_chat(message.chat)
        if message.text and message.text.startswith("/"):
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
            if not admin_can(call.from_user.id, "admins.permissions"): alert(bot, call, "🚫 لا تملك الصلاحية.", True); return
            bot.edit_message_text("🔐 اختر المشرف:", call.message.chat.id, call.message.message_id, reply_markup=permissions_select_markup()); return
        if data.startswith("perm_user:"):
            if not admin_can(call.from_user.id, "admins.permissions"): alert(bot, call, "🚫 لا تملك الصلاحية.", True); return
            uid = int(data.split(":",1)[1])
            if uid == call.from_user.id or uid == DEVELOPER_ID: alert(bot, call, "🚫 لا يمكنك تعديل هذا الحساب.", True); return
            bot.edit_message_text(f"🔐 صلاحيات المشرف {uid}", call.message.chat.id, call.message.message_id, reply_markup=permission_markup(uid)); return
        if data.startswith("perm_toggle:"):
            if not admin_can(call.from_user.id, "admins.permissions"): alert(bot, call, "🚫 لا تملك الصلاحية.", True); return
            _, raw_uid, permission = data.split(":",2); uid=int(raw_uid)
            if uid == call.from_user.id or not is_admin(uid, DEVELOPER_ID) or uid == DEVELOPER_ID:
                alert(bot, call, "❌ هذا ليس مشرفاً قابلاً للتعديل.", True); return
            state=get_permission_state(uid, DEVELOPER_ID); set_permission(uid, permission, not state.get(permission, False))
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=permission_markup(uid)); alert(bot, call, "✅ تم تحديث الصلاحية."); return
        if data == "adm_list":
            if not admin_can(call.from_user.id,"admins.view"): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            rows=list_sudos(); text="👥 المشرفون:\n\n"+ ("\n".join(f"• {uid}" for uid, in rows) if rows else "لا يوجد مشرفون.")
            bot.edit_message_text(text,call.message.chat.id,call.message.message_id,reply_markup=admins_menu()); return
        if data == "adm_noop": alert(bot,call,"ℹ️ لا يوجد مشرفون."); return
        if data in {"adm_add","adm_remove"}:
            required = "admins.add" if data == "adm_add" else "admins.remove"
            if not admin_can(call.from_user.id, required): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            _set_pending_from_callback(call, "admin_add" if data=="adm_add" else "admin_remove", "✍️ أرسل Telegram ID:", bot); return
        if data == "adm_playback":
            if not admin_can(call.from_user.id,"settings.view"): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            bot.edit_message_text("🎛️ تخصيص لوحة التشغيل",call.message.chat.id,call.message.message_id,reply_markup=playback_markup()); return
        if data == "adm_subs":
            if not admin_can(call.from_user.id,"subscriptions.view"): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            bot.edit_message_text("📢 إدارة الاشتراك الإجباري",call.message.chat.id,call.message.message_id,reply_markup=subscriptions_markup()); return
        if data == "adm_broadcast":
            if not admin_can(call.from_user.id,"broadcast.send"): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            _set_pending_from_callback(call,"broadcast","📣 أرسل نص الإذاعة الآن:",bot); return
        if data == "adm_stats":
            if not admin_can(call.from_user.id,"stats.view"): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            u,c,a=counts(); alert(bot,call,f"👥 المستخدمون: {u}\n💬 المحادثات: {c}\n👨‍💻 المشرفون: {a}",True); return
        if data == "adm_users":
            if not admin_can(call.from_user.id,"users.view"): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            bot.edit_message_text("👥 إدارة المستخدمين",call.message.chat.id,call.message.message_id,reply_markup=users_markup()); return
        if data in {"user_ban","user_unban"}:
            required = "users.ban" if data == "user_ban" else "users.unban"
            if not admin_can(call.from_user.id,required): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            _set_pending_from_callback(call,data,"✍️ أرسل Telegram ID:",bot); return
        if data == "user_banned":
            if not admin_can(call.from_user.id,"users.banned"): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            ids=banned_ids(); alert(bot,call,"🚫 المحظورون:\n"+ ("\n".join(map(str,ids)) if ids else "لا يوجد."),True); return
        if data in {"sub_add","sub_remove"}:
            required = "subscriptions.add" if data == "sub_add" else "subscriptions.remove"
            if not admin_can(call.from_user.id,required): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            prompt="➕ أرسل: اسم القناة | @channel أو -100... | رابط القناة" if data=="sub_add" else "🗑️ أرسل @channel أو chat ID للحذف:"
            _set_pending_from_callback(call,data,prompt,bot); return
        if data == "sub_list":
            if not admin_can(call.from_user.id,"subscriptions.view"): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            rows=subscriptions(); text="📋 القنوات:\n\n"+ ("\n".join(f"• {title} — {target}" for _,title,target,_,_ in rows) if rows else "لا توجد قنوات.")
            bot.edit_message_text(text,call.message.chat.id,call.message.message_id,reply_markup=subscriptions_markup()); return
        if data == "sub_toggle":
            if not admin_can(call.from_user.id,"subscriptions.toggle"): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            current=setting_get("SUBS_ENABLED")=="ON"; setting_set("SUBS_ENABLED","OFF" if current else "ON"); alert(bot,call,"🔛 تم تبديل الاشتراك الإجباري."); return
        if data in {"play_source1","play_source1_url","play_source2","play_source2_url","play_set_image"}:
            required = {"play_source1":"playback_panel.source1","play_source1_url":"playback_panel.source1","play_source2":"playback_panel.source2","play_source2_url":"playback_panel.source2","play_set_image":"playback_panel.image"}[data]
            if not admin_can(call.from_user.id,required): alert(bot,call,"🚫 لا تملك الصلاحية.",True); return
            prompts={"play_source1":"📝 اسم المصدر الأول:","play_source1_url":"🔗 رابط المصدر الأول:","play_source2":"📝 اسم المصدر الثاني:","play_source2_url":"🔗 رابط المصدر الثاني:","play_set_image":"🖼️ أرسل صورة أو GIF لوحة التشغيل:"}
            _set_pending_from_callback(call,data,prompts[data],bot); return
        if data.startswith("music_"):
            action = data.split("_", 1)[1]
            if call.message.chat.type not in {"group", "supergroup"}:
                alert(bot, call, "🚫 لوحة الاتصال تعمل داخل المجموعة فقط.", True)
                return
            # The playback message belongs to this group. Its administrators
            # can control it even when they are not global sudos.
            if group_control_allowed(bot, call.message.chat.id, call.from_user.id):
                allowed = True
            elif call.from_user.id == DEVELOPER_ID:
                allowed = True
            elif is_admin(call.from_user.id, DEVELOPER_ID):
                if action == "stop":
                    allowed = admin_can(call.from_user.id, "playback.stop") and admin_can(call.from_user.id, "playback.leave")
                elif action == "play":
                    allowed = admin_can(call.from_user.id, "playback.play") and admin_can(call.from_user.id, "playback.join")
                elif action.startswith("seek"):
                    allowed = admin_can(call.from_user.id, "playback.seek")
                else:
                    allowed = admin_can(call.from_user.id, f"playback.{action}")
            else:
                allowed = not CONTROL_ADMINS_ONLY
            if not allowed:
                alert(bot, call, "🚫 ليست لديك صلاحية هذا الزر.", True)
                return
            try:
                chat_id = call.message.chat.id
                if data == "music_skip":
                    next_track = player.skip_and_take_next(chat_id)
                    if next_track:
                        _replace_voice_stream(calls, player, chat_id, next_track.path)
                        _send_now(bot, chat_id, next_track)
                    else:
                        try:
                            calls.leave(chat_id)
                        except Exception:
                            pass
                        _send_now(bot, chat_id, None)
                    alert(bot, call, "تم التخطي.")
                elif data == "music_stop":
                    try:
                        calls.leave(chat_id)
                    except Exception:
                        pass
                    player.stop(chat_id)
                    bot.send_message(chat_id, "⏹️ تم الإيقاف.")
                    alert(bot, call, "تم إنهاء التشغيل.")
                elif data == "music_pause":
                    calls.pause(chat_id)
                    position = player.set_paused(chat_id, True)
                    alert(bot, call, f"⏸️ متوقف عند {duration_text(position)}")
                elif data == "music_resume":
                    calls.resume(chat_id)
                    position = player.set_paused(chat_id, False)
                    alert(bot, call, f"▶️ استئناف من {duration_text(position)}")
                elif data in {"music_seek_-10", "music_seek_10"}:
                    delta = -10 if data.endswith("-10") else 10
                    before_position = player.playback_position(chat_id)
                    current = player.current(chat_id)
                    was_paused = False
                    if current is not None:
                        # Read the pause state without mutating the playback clock.
                        paused_state = getattr(player, "is_paused", None)
                        if callable(paused_state):
                            was_paused = bool(paused_state(chat_id))
                    result = player.seek(chat_id, delta)
                    if result is None:
                        alert(bot, call, "❌ لا توجد أغنية قيد التشغيل.", True)
                        return
                    track, target, was_paused = result
                    try:
                        _replace_voice_stream(calls, player, chat_id, track.path, start_seconds=target)
                        if was_paused:
                            calls.pause(chat_id)
                            player.set_position(chat_id, target, paused=True)
                        else:
                            player.set_position(chat_id, target, paused=False)
                    except Exception:
                        # Restore the last known logical state if the stream
                        # replacement failed. Do not leave the UI/state lying.
                        player.set_position(chat_id, before_position, paused=was_paused)
                        raise
                    icon = "⏪" if delta < 0 else "⏩"
                    alert(bot, call, f"{icon} {duration_text(target)} / {duration_text(track.duration)}")
                elif data == "music_queue":
                    _send_queue(bot, player, chat_id)
                    alert(bot, call, "تم.")
                elif data == "music_close":
                    try:
                        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
                    except Exception:
                        pass
                    alert(bot, call, "تم إغلاق أزرار التشغيل.")
            except Exception:
                log.exception("playback callback failed")
                alert(bot, call, "❌ تعذر التنفيذ.", True)
            return
        alert(bot,call,"ℹ️ الطلب غير معروف.")

    calls.set_stream_end_handler(lambda chat_id: _stream_end(bot, calls, player, chat_id))
