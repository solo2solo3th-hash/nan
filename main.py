
"""Railway entry point; heavy functionality lives in dedicated modules."""
from __future__ import annotations

import logging
import threading
import time
from urllib.parse import urlparse

import telebot

from assistant_pool import AssistantPool
from developer_panel import configure_assistant_pool
from config import (
    BOT_LONG_POLLING_TIMEOUT,
    BOT_POLLING_TIMEOUT,
    LOG_LEVEL,
    MAX_QUEUE_SIZE,
    TOKEN,
)
from database import init_db, auto_message_claim_due
from bot_handlers import register_handlers
from music_player import MusicPlayer

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("music-bot")

bot = telebot.TeleBot(TOKEN, parse_mode=None)
voice = AssistantPool()
voice.set_bot(bot)
configure_assistant_pool(voice)
player = MusicPlayer(MAX_QUEUE_SIZE)


def _auto_target(target: str) -> str:
    value = str(target or "").strip()
    if value.lstrip("-").isdigit() or value.startswith("@"):
        return value
    parsed = urlparse(value if "://" in value else "https://" + value)
    host = (parsed.netloc or "").lower()
    path = parsed.path.strip("/")
    if host in {"t.me", "telegram.me"} and path:
        first = path.split("/", 1)[0]
        if first.startswith("+"):
            return value
        return "@" + first.lstrip("@")
    return value


def _automatic_message_worker() -> None:
    while True:
        try:
            now = int(time.time())
            for row in auto_message_claim_due(now):
                item_id, target, message_text = int(row[0]), row[1], row[2]
                try:
                    bot.send_message(_auto_target(target), message_text)
                except Exception:
                    log.exception("Automatic message #%s failed for target %s", item_id, target)
        except Exception:
            log.exception("Automatic message scheduler failed")
        time.sleep(5)


def get_bot_username() -> str:
    me = bot.get_me()
    return me.username or "bot"


def setup_handlers(bot_username: str) -> None:
    register_handlers(bot, bot_username, voice, player)


def main() -> None:
    init_db()
    bot_username = get_bot_username()
    setup_handlers(bot_username)
    threading.Thread(target=_automatic_message_worker, name="automatic-messages", daemon=True).start()

    try:
        try:
            voice.start()
            log.info("Voice runtime started successfully")
        except Exception:
            log.exception(
                "Voice runtime failed to start. "
                "Bot polling will continue, but music playback "
                "will remain unavailable until the assistant session is fixed."
            )

        log.info("Bot polling started")
        bot.infinity_polling(
            skip_pending=True,
            timeout=BOT_POLLING_TIMEOUT,
            long_polling_timeout=BOT_LONG_POLLING_TIMEOUT,
            allowed_updates=None,
        )
    finally:
        voice.stop()


if __name__ == "__main__":
    main()
