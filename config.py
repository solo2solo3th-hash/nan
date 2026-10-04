"""Central configuration for the Railway Telegram music bot."""
from __future__ import annotations

import os
from pathlib import Path


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable is missing: {name}")
    return value


def _int_env(name: str, default: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        if default is None:
            return int(_required(name))
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got: {raw!r}") from exc


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


TOKEN = _required("TOKEN")
API_ID = _int_env("API_ID")
API_HASH = _required("API_HASH")
DEVELOPER_ID = _int_env("DEVELOPER_ID")
# Keep legacy variable names working, but allow the five-slot assistant pool
# to start when one or more ASSISTANT_SESSION_<n> variables are configured.
PYROGRAM_SESSION_STRING = (
    os.getenv("PYROGRAM_SESSION_STRING", "").strip()
    or os.getenv("SESSION_STRING", "").strip()
)
SESSION_STRING = PYROGRAM_SESSION_STRING

# A session may also be loaded from the encrypted SQLite store after init_db().
# AssistantPool.start() reports a clear error if no environment or stored session exists.

DATA_DIR = Path(os.getenv("DATA_DIR", "/data")).expanduser()
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", str(DATA_DIR / "downloads"))).expanduser()
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "bot.db"))).expanduser()

CONTROL_ADMINS_ONLY = _bool_env("CONTROL_ADMINS_ONLY", False)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
BOT_POLLING_TIMEOUT = _int_env("BOT_POLLING_TIMEOUT", 30)
BOT_LONG_POLLING_TIMEOUT = _int_env("BOT_LONG_POLLING_TIMEOUT", 30)
MAX_QUEUE_SIZE = max(1, _int_env("MAX_QUEUE_SIZE", 20))
MAX_DOWNLOAD_MB = max(1, _int_env("MAX_DOWNLOAD_MB", 100))
DOWNLOAD_RETRIES = max(1, _int_env("DOWNLOAD_RETRIES", 3))
SOCKET_TIMEOUT = max(5, _int_env("SOCKET_TIMEOUT", 30))
CALLS_READY_TIMEOUT = max(10, _int_env("CALLS_READY_TIMEOUT", 90))

# Optional YouTube authentication secret.
# Keep the existing Railway variable name. Do not put its value in GitHub.
YOUTUBE_COOKIES = os.getenv("YOUTUBE_COOKIES", "")
YOUTUBE_COOKIES_B64 = os.getenv("YOUTUBE_COOKIES_B64", "")
YOUTUBE_PO_TOKEN = os.getenv("YOUTUBE_PO_TOKEN", "").strip()

# Ten supported audio-source targets. Direct URLs are routed to their exact
# extractor; text search uses the sources with native search prefixes.
AUDIO_SOURCE_ORDER = tuple(
    item.strip().lower()
    for item in os.getenv(
        "AUDIO_SOURCE_ORDER",
        "youtube,soundcloud,audius,jamendo,bandcamp,audiomack,mixcloud,internet_archive,vimeo,dailymotion",
    ).split(",")
    if item.strip()
)

SUPPORTED_AUDIO_SOURCES = (
    "youtube", "soundcloud", "audius", "jamendo", "bandcamp",
    "audiomack", "mixcloud", "internet_archive", "vimeo", "dailymotion",
)

# Dependency versions used by the project.
PYTG_CALLS_VERSION = "2.3.3"
PYROGRAMMOD_VERSION = "2.4.1"
PYTELEBOT_VERSION = "4.37.0"
YTDLP_VERSION = "2026.8.19"

for directory in (DATA_DIR, DOWNLOAD_DIR, DB_PATH.parent):
    directory.mkdir(parents=True, exist_ok=True)
