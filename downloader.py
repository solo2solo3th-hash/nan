"""Audio acquisition with a 10-source fallback matrix."""
from __future__ import annotations

import base64
import binascii
import re
import shutil
import stat
import uuid
from pathlib import Path
from urllib.parse import urlparse

from config import (
    AUDIO_SOURCE_ORDER,
    DOWNLOAD_DIR,
    DOWNLOAD_RETRIES,
    MAX_DOWNLOAD_MB,
    SOCKET_TIMEOUT,
    YOUTUBE_COOKIES,
    YOUTUBE_COOKIES_B64,
    YOUTUBE_PO_TOKEN,
)

_NETSCAPE_COOKIE_HEADERS = ("# HTTP Cookie File", "# Netscape HTTP Cookie File")

# yt-dlp has current extractors for these services. Not every extractor supports
# text search; URL routing is therefore explicit and reliable.
SOURCE_HOSTS = {
    "youtube": ("youtube.com", "youtu.be", "music.youtube.com"),
    "soundcloud": ("soundcloud.com", "m.soundcloud.com"),
    "audius": ("audius.co", "audius.co"),
    "jamendo": ("jamendo.com", "www.jamendo.com"),
    "bandcamp": ("bandcamp.com",),
    "audiomack": ("audiomack.com", "www.audiomack.com"),
    "mixcloud": ("mixcloud.com", "www.mixcloud.com"),
    "internet_archive": ("archive.org", "www.archive.org"),
    "vimeo": ("vimeo.com", "www.vimeo.com", "player.vimeo.com"),
    "dailymotion": ("dailymotion.com", "www.dailymotion.com", "dai.ly"),
}


def cleanup_job(job: str | Path | None) -> None:
    if job:
        shutil.rmtree(str(job), ignore_errors=True)


def _decode_cookie_secret() -> str | None:
    raw = YOUTUBE_COOKIES.strip()
    if not raw and YOUTUBE_COOKIES_B64.strip():
        try:
            raw = base64.b64decode(YOUTUBE_COOKIES_B64.strip(), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise RuntimeError("YOUTUBE_COOKIES_B64 is not valid base64 UTF-8") from exc
    if not raw:
        return None
    raw = raw.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n")
    first_nonempty = next((line.strip() for line in raw.splitlines() if line.strip()), "")
    if first_nonempty not in _NETSCAPE_COOKIE_HEADERS:
        raise RuntimeError(
            "YOUTUBE_COOKIES must be a Mozilla/Netscape cookies.txt file "
            "starting with '# Netscape HTTP Cookie File' or '# HTTP Cookie File'"
        )
    return raw.rstrip("\n") + "\n"


def _write_youtube_cookies(job: Path) -> str | None:
    cookies = _decode_cookie_secret()
    if not cookies:
        return None
    cookie_path = job / "youtube_cookies.txt"
    cookie_path.write_text(cookies, encoding="utf-8", newline="\n")
    try:
        cookie_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return str(cookie_path)


def _youtube_extractor_args() -> dict[str, str]:
    if not YOUTUBE_PO_TOKEN:
        return {}
    return {"youtube": f"player-client=web,default;po_token=web+{YOUTUBE_PO_TOKEN}"}


def _is_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def _source_for_url(value: str) -> str | None:
    if not _is_url(value):
        return None
    host = urlparse(value).netloc.lower().split(":", 1)[0]
    for source, hosts in SOURCE_HOSTS.items():
        if any(host == item or host.endswith("." + item) for item in hosts):
            return source
    return None


def _source_options(job: Path, source: str) -> dict:
    options = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "outtmpl": str(job / "%(_source_id)s-%(id)s.%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "quiet": True,
        "no_warnings": True,
        "retries": DOWNLOAD_RETRIES,
        "fragment_retries": DOWNLOAD_RETRIES,
        "socket_timeout": SOCKET_TIMEOUT,
        "restrictfilenames": True,
        "overwrites": False,
        "js_runtimes": {"node": {}},
        "postprocessor_args": {"FFmpegExtractAudio": ["-id3v2_version", "3"]},
    }
    if source == "youtube":
        options["extractor_args"] = _youtube_extractor_args()
    return options


def _search_candidates(query: str) -> list[tuple[str, str]]:
    """Return source-specific search expressions for sources with search support."""
    candidates = []
    for source in AUDIO_SOURCE_ORDER:
        if source == "youtube":
            candidates.append((source, f"ytsearch1:{query}"))
        elif source == "soundcloud":
            candidates.append((source, f"scsearch1:{query}"))
        # Audius/Jamendo have dedicated API handling in older builds; direct
        # URLs remain supported through the generic yt-dlp extractor below.
    return candidates


def _download_one(source: str, target: str, job: Path):
    import yt_dlp

    options = _source_options(job, source)
    if source == "youtube":
        cookie_path = _write_youtube_cookies(job)
        if cookie_path:
            options["cookiefile"] = cookie_path

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(target, download=True)

    if info and info.get("entries"):
        info = next((item for item in info["entries"] if item), info)
    if info:
        info["_source"] = source
    return info or {}


def _finish(job: Path, info: dict):
    mp3_files = list(job.glob("*.mp3"))
    if not mp3_files:
        raise RuntimeError("source completed without producing an MP3 file")
    mp3 = max(mp3_files, key=lambda p: p.stat().st_mtime)
    size_mb = mp3.stat().st_size / (1024 * 1024)
    if size_mb > MAX_DOWNLOAD_MB:
        raise RuntimeError(f"Downloaded file exceeds MAX_DOWNLOAD_MB={MAX_DOWNLOAD_MB}")
    return info, str(mp3.resolve()), str(job)


def download_audio(query: str):
    query = query.strip()
    if not query:
        raise ValueError("query cannot be empty")

    job = Path(DOWNLOAD_DIR) / uuid.uuid4().hex
    job.mkdir(parents=True, exist_ok=False)

    # Direct links: route to the exact source instead of guessing.
    direct_source = _source_for_url(query)
    if direct_source:
        try:
            return _finish(job, _download_one(direct_source, query, job))
        except Exception as exc:
            cleanup_job(job)
            raise RuntimeError(f"{direct_source} failed: {exc}") from exc

    errors = []
    try:
        # Text search currently has reliable native search prefixes for YouTube
        # and SoundCloud. These are tried in the configured order.
        for source, target in _search_candidates(query):
            if source not in AUDIO_SOURCE_ORDER:
                continue
            try:
                return _finish(job, _download_one(source, target, job))
            except Exception as exc:
                errors.append(f"{source}: {exc}")
                for path in job.iterdir():
                    if path.is_file():
                        path.unlink(missing_ok=True)

        raise RuntimeError(
            "No searchable source succeeded. " + " | ".join(errors[-3:])
            if errors else "No searchable source is configured"
        )
    finally:
        # On success the job is returned to the caller for cleanup. On failure
        # it is removed here.
        if not any(job.glob("*.mp3")):
            cleanup_job(job)
