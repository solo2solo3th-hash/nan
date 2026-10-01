"""Railway entry point; heavy functionality lives in dedicated modules."""
from __future__ import annotations
import logging
import threading
import time
import telebot
# RAILWAY_UPDATE_TEST_2026
from calls import VoiceCallRunner
from config import API_HASH, API_ID, BOT_LONG_POLLING_TIMEOUT, BOT_POLLING_TIMEOUT, DEVELOPER_ID, LOG_LEVEL, MAX_QUEUE_SIZE, SESSION_STRING, TOKEN
from database import init_db, is_admin
from bot_handlers import admin_can, register_handlers, require_group, user_can_play
from music_player import MusicPlayer, PlayerService, Track

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("music-bot")

bot = telebot.TeleBot(TOKEN, parse_mode=None)
voice = VoiceCallRunner()
player = MusicPlayer(MAX_QUEUE_SIZE)
assistant = None
calls = None
MT_READY = voice.ready
MT_ERROR = None


def get_bot_username() -> str:
    me = bot.get_me()
    return me.username or "bot"


def setup_stream_end_handler() -> None:
    """Compatibility hook; actual registration is performed by register_handlers."""
    return None


def mtproto_worker() -> None:
    """Compatibility entry point that starts the isolated voice runtime."""
    global assistant, calls, MT_ERROR
    try:
        voice.start()
        assistant = voice.assistant
        calls = voice.calls
    except Exception as exc:
        MT_ERROR = exc
        MT_READY.set()
        log.exception("MTProto/PyTgCalls startup failed")


def setup_handlers(bot_username: str) -> None:
    register_handlers(bot, bot_username, voice, player)


def main() -> None:
    init_db()
    bot_username = get_bot_username()
    setup_handlers(bot_username)
    worker = threading.Thread(target=mtproto_worker, name="mtproto", daemon=True)
    worker.start()
    if not MT_READY.wait(timeout=95):
        raise RuntimeError("MTProto/PyTgCalls did not become ready.")
    if MT_ERROR is not None:
        raise RuntimeError(f"MTProto startup failed: {MT_ERROR}") from MT_ERROR
    log.info("Bot polling started")
    try:
        bot.infinity_polling(skip_pending=True, timeout=BOT_POLLING_TIMEOUT, long_polling_timeout=BOT_LONG_POLLING_TIMEOUT, allowed_updates=None)
    finally:
        voice.stop()


if __name__ == "__main__":
    main()
