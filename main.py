
"""Railway entry point; heavy functionality lives in dedicated modules."""
from __future__ import annotations

import logging

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
from database import init_db
from bot_handlers import register_handlers
from music_player import MusicPlayer

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("music-bot")

bot = telebot.TeleBot(TOKEN, parse_mode=None)
voice = AssistantPool()
configure_assistant_pool(voice)
player = MusicPlayer(MAX_QUEUE_SIZE)


def get_bot_username() -> str:
    me = bot.get_me()
    return me.username or "bot"


def setup_handlers(bot_username: str) -> None:
    register_handlers(bot, bot_username, voice, player)


def main() -> None:
    init_db()
    bot_username = get_bot_username()
    setup_handlers(bot_username)

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
