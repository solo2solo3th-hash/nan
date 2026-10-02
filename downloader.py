"""Audio acquisition with a 10-source fallback matrix."""
from __future__ import annotations

import base64
import binascii
import re
import shutil
import stat
import subprocess
import uuid
from html import unescape
from pathlib import Path
from urllib.parse import quote_plus, urlparse, unquote
from urllib.request import Request, urlopen

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

# yt-dlp extractor-backed sources. Availability varies by site and region;
# this list is a fallback pool, not a guarantee that every site is reachable.
SOURCE_HOSTS = {
    "youtube": ("youtube.com", "youtu.be", "music.youtube.com"),
    "soundcloud": ("soundcloud.com", "m.soundcloud.com"),
    "audius": ("audius.co",),
    "jamendo": ("jamendo.com", "www.jamendo.com"),
    "bandcamp": ("bandcamp.com",),
    "audiomack": ("audiomack.com", "www.audiomack.com"),
    "mixcloud": ("mixcloud.com", "www.mixcloud.com"),
    "internet_archive": ("archive.org", "www.archive.org"),
    "vimeo": ("vimeo.com", "www.vimeo.com", "player.vimeo.com"),
    "dailymotion": ("dailymotion.com", "www.dailymotion.com", "dai.ly"),
    "tiktok": ("tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"),
    "facebook": ("facebook.com", "www.facebook.com", "fb.watch", "m.facebook.com"),
    "instagram": ("instagram.com", "www.instagram.com"),
    "reddit": ("reddit.com", "www.reddit.com", "v.redd.it", "redd.it"),
    "twitch": ("twitch.tv", "www.twitch.tv", "clips.twitch.tv"),
    "twitter": ("twitter.com", "www.twitter.com", "x.com", "www.x.com"),
    "bilibili": ("bilibili.com", "www.bilibili.com", "b23.tv"),
    "vk": ("vk.com", "www.vk.com", "vk.ru"),
    "hearthisat": ("hearthis.at", "www.hearthis.at"),
    "niconico": ("nicovideo.jp", "www.nicovideo.jp", "nico.ms"),
    "odysee": ("odysee.com", "www.odysee.com"),
}

# Keep the project's configured priority, then append additional extractors so
# an older config.py containing only ten sources still gains the expanded pool.
_EXTRA_SOURCE_ORDER = (
    "tiktok", "facebook", "instagram", "reddit", "twitch", "twitter",
    "bilibili", "vk", "hearthisat", "niconico", "odysee",
)
_ACTIVE_SOURCE_ORDER = tuple(dict.fromkeys(
    [name for name in AUDIO_SOURCE_ORDER if name in SOURCE_HOSTS]
    + [name for name in _EXTRA_SOURCE_ORDER if name not in AUDIO_SOURCE_ORDER]
))

SEARCH_DOMAINS = {
    source: hosts[0].removeprefix("www.")
    for source, hosts in SOURCE_HOSTS.items()
}

# yt-dlp may leave audio in any of these formats before FFmpeg normalization.
AUDIO_EXTENSIONS = {
    ".mp3",
    ".m4a",
    ".webm",
    ".opus",
    ".ogg",
    ".oga",
    ".aac",
    ".wav",
    ".flac",
    ".mp4",
    ".mkv",
}


def cleanup_job(job: str | Path | None) -> None:
    if job:
        shutil.rmtree(str(job), ignore_errors=True)


def _decode_cookie_secret() -> str | None:
    raw = YOUTUBE_COOKIES.strip()
    if not raw and YOUTUBE_COOKIES_B64.strip():
        try:
            raw = base64.b64decode(
                YOUTUBE_COOKIES_B64.strip(), validate=True
            ).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise RuntimeError(
                "YOUTUBE_COOKIES_B64 is not valid base64 UTF-8"
            ) from exc

    if not raw:
        return None

    raw = raw.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n")
    first_nonempty = next(
        (line.strip() for line in raw.splitlines() if line.strip()), ""
    )

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
    return {
        "youtube": f"player-client=web,default;po_token=web+{YOUTUBE_PO_TOKEN}"
    }


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
    # IMPORTANT:
    # Do not force FFmpegExtractAudio here. yt-dlp can successfully download
    # m4a/webm/opus/etc. while the old postprocessor leaves no .mp3 file.
    # We normalize the resulting file explicitly in _finish().
    options = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "outtmpl": str(job / "%(extractor)s-%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "retries": DOWNLOAD_RETRIES,
        "fragment_retries": DOWNLOAD_RETRIES,
        "socket_timeout": SOCKET_TIMEOUT,
        "restrictfilenames": True,
        "overwrites": False,
        "js_runtimes": {"node": {}},
    }

    if source == "youtube":
        options["extractor_args"] = _youtube_extractor_args()

    return options


def _web_search_candidate(source: str, query: str) -> str | None:
    domain = SEARCH_DOMAINS.get(source)
    if not domain:
        return None

    url = (
        "https://html.duckduckgo.com/html/?q="
        + quote_plus(f"site:{domain} {query}")
    )
    req = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; TelegramMusicBot/1.0)"
            )
        },
    )

    try:
        # Keep discovery bounded: a slow search engine must not stall the bot.
        with urlopen(req, timeout=min(max(int(SOCKET_TIMEOUT), 1), 5)) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except Exception:
        return None

    for pattern in (
        # DuckDuckGo's current redirect parameter is "uddg"; keep the
        # legacy spelling as a compatibility fallback.
        r'uddg=([^"&]+)',
        r'nuddg=([^"&]+)',
        r'class="result__a"[^>]+href="([^"]+)"',
    ):
        for match in re.findall(pattern, html, flags=re.IGNORECASE):
            candidate = unescape(unquote(match))

            if candidate.startswith("//"):
                candidate = "https:" + candidate

            if (
                candidate.startswith("http")
                and _source_for_url(candidate) == source
            ):
                return candidate

    return None


def _search_candidates(query: str):
    """Yield candidates lazily so one slow website cannot delay all downloads."""
    native = {"youtube", "soundcloud"}

    # Try native yt-dlp search extractors first; these are the most useful
    # general-purpose music search sources.
    for source in _ACTIVE_SOURCE_ORDER:
        if source == "youtube":
            yield source, f"ytsearch1:{query}"
        elif source == "soundcloud":
            yield source, f"scsearch1:{query}"

    # Discover other sources only after native sources have failed.
    # Yield lazily: do not wait on every search domain before trying a download.
    for source in _ACTIVE_SOURCE_ORDER:
        if source in native or source not in SOURCE_HOSTS:
            continue
        found = _web_search_candidate(source, query)
        if found:
            yield source, found


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
        info = next(
            (item for item in info["entries"] if item),
            info,
        )

    if info:
        info["_source"] = source

    return info or {}


def _audio_files(job: Path) -> list[Path]:
    files = []

    for path in job.iterdir():
        if not path.is_file():
            continue
        if path.name == "youtube_cookies.txt":
            continue
        if path.suffix.lower() in AUDIO_EXTENSIONS:
            try:
                if path.stat().st_size > 0:
                    files.append(path)
            except OSError:
                continue

    return files


def _run_ffmpeg_to_mp3(source: Path, target: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "FFmpeg is not installed or is not available in PATH"
        )

    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-vn",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        "192k",
        "-id3v2_version",
        "3",
        str(target),
    ]

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(SOCKET_TIMEOUT * 2, 120),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("FFmpeg conversion timed out") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not execute FFmpeg: {exc}") from exc

    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        if len(detail) > 1200:
            detail = detail[-1200:]
        raise RuntimeError(
            "FFmpeg failed to convert audio"
            + (f": {detail}" if detail else "")
        )

    if not target.exists() or target.stat().st_size <= 0:
        raise RuntimeError("FFmpeg completed without producing an MP3 file")


def _finish(job: Path, info: dict):
    audio_files = _audio_files(job)

    if not audio_files:
        raise RuntimeError(
            "source completed without producing a usable audio file"
        )

    source_file = max(
        audio_files,
        key=lambda p: p.stat().st_mtime,
    )

    size_mb = source_file.stat().st_size / (1024 * 1024)
    if size_mb > MAX_DOWNLOAD_MB:
        raise RuntimeError(
            f"Downloaded file exceeds MAX_DOWNLOAD_MB={MAX_DOWNLOAD_MB}"
        )

    # Keep the public return contract as MP3 so existing bot_handlers.py code
    # does not need to change.
    if source_file.suffix.lower() == ".mp3":
        mp3_file = source_file
    else:
        mp3_file = job / f"{source_file.stem}.mp3"
        _run_ffmpeg_to_mp3(source_file, mp3_file)

    if not mp3_file.exists() or mp3_file.stat().st_size <= 0:
        raise RuntimeError("MP3 output is missing or empty")

    final_size_mb = mp3_file.stat().st_size / (1024 * 1024)
    if final_size_mb > MAX_DOWNLOAD_MB:
        raise RuntimeError(
            f"Final MP3 exceeds MAX_DOWNLOAD_MB={MAX_DOWNLOAD_MB}"
        )

    return info, str(mp3_file.resolve()), str(job)


def _clear_job_files(job: Path) -> None:
    for path in job.iterdir():
        if path.is_file():
            path.unlink(missing_ok=True)


def download_audio(query: str):
    query = query.strip()
    if not query:
        raise ValueError("query cannot be empty")

    job = Path(DOWNLOAD_DIR) / uuid.uuid4().hex
    job.mkdir(parents=True, exist_ok=False)

    direct_source = _source_for_url(query)

    if _is_url(query) and not direct_source:
        # Unknown-but-valid URLs may still be supported by a yt-dlp extractor.
        try:
            return _finish(job, _download_one("generic", query, job))
        except Exception as exc:
            cleanup_job(job)
            raise RuntimeError(f"URL source failed: {exc}") from exc

    if direct_source:
        try:
            return _finish(
                job,
                _download_one(direct_source, query, job),
            )
        except Exception as exc:
            cleanup_job(job)
            raise RuntimeError(
                f"{direct_source} failed: {exc}"
            ) from exc

    errors = []

    try:
        for source, target in _search_candidates(query):
            if source not in _ACTIVE_SOURCE_ORDER:
                continue

            try:
                return _finish(
                    job,
                    _download_one(source, target, job),
                )
            except Exception as exc:
                errors.append(f"{source}: {exc}")
                _clear_job_files(job)

        raise RuntimeError(
            "No searchable source succeeded. "
            + " | ".join(errors[-10:])
            if errors
            else "No searchable source is configured"
        )
    finally:
        # Do not delete the job when a valid MP3 was returned.
        if not any(
            p.is_file()
            and p.suffix.lower() == ".mp3"
            and p.stat().st_size > 0
            for p in job.iterdir()
        ):
            cleanup_job(job)
