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
    """Load and validate a Netscape cookies.txt value from Railway variables."""
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

    # Railway variables may contain literal escaped line endings when copied
    # from a shell or dashboard. Convert those without changing cookie values.
    raw = (
        raw.replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\\r", "\n")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )
    lines = raw.splitlines()
    first_nonempty = next(
        (line.strip() for line in lines if line.strip()), ""
    )

    if first_nonempty not in _NETSCAPE_COOKIE_HEADERS:
        raise RuntimeError(
            "YouTube cookies are not in Netscape format. Set YOUTUBE_COOKIES "
            "to the complete cookies.txt export, including its header."
        )

    cookie_count = 0
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        fields = line.split("\t")
        if len(fields) != 7:
            raise RuntimeError(
                "Invalid YouTube cookies.txt format at line "
                f"{line_number}: each cookie must be one line with 7 "
                "TAB-separated fields. Re-export the file in Netscape "
                "cookies.txt format; do not paste one field per line."
            )

        domain, include_subdomains, path, secure, expiry, name, value = fields
        if (
            not domain.strip()
            or include_subdomains not in {"TRUE", "FALSE"}
            or not path.startswith("/")
            or secure not in {"TRUE", "FALSE"}
            or not expiry.isdigit()
            or not name
        ):
            raise RuntimeError(
                "Invalid YouTube cookies.txt entry at line "
                f"{line_number}. Re-export the complete Netscape cookies.txt "
                "file without editing or reformatting it."
            )
        cookie_count += 1

    if cookie_count == 0:
        raise RuntimeError(
            "YOUTUBE_COOKIES contains a header but no cookie entries."
        )

    return "\n".join(lines).rstrip("\n") + "\n"

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
    # The bgutil provider generates fresh, video-bound PO tokens at runtime.
    # mweb uses those tokens; tv and web_safari remain fallback clients.
    return {
        "youtube": "player-client=mweb,tv,web_safari"
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


def _search_engine_candidates(engine: str, source: str, query: str) -> list[str]:
    """Find playable pages for one source using a general web search engine."""
    domain = SEARCH_DOMAINS.get(source)
    if not domain:
        return []

    search_text = f"site:{domain} {query}"
    if engine == "duckduckgo":
        url = "https://html.duckduckgo.com/html/?q=" + quote_plus(search_text)
    elif engine == "bing":
        url = "https://www.bing.com/search?q=" + quote_plus(search_text)
    else:
        return []

    req = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )

    try:
        # Bound each search request so a blocked search engine cannot stall the bot.
        with urlopen(req, timeout=min(max(int(SOCKET_TIMEOUT), 1), 4)) as response:
            html = response.read(1_500_000).decode("utf-8", errors="ignore")
    except Exception:
        return []

    found = []
    seen = set()
    for raw_href in re.findall(
        r"""href\s*=\s*["']([^"']+)["']""",
        html,
        flags=re.IGNORECASE,
    ):
        candidate = unescape(raw_href).replace("&amp;", "&")
        if candidate.startswith("//"):
            candidate = "https:" + candidate

        try:
            parsed = urlparse(candidate)
            params = dict(
                item.split("=", 1) for item in parsed.query.split("&") if "=" in item
            )
            if engine == "duckduckgo" and params.get("uddg"):
                candidate = unquote(params["uddg"])
            elif engine == "bing" and params.get("u", "").startswith("a1"):
                encoded = params["u"][2:]
                padding = "=" * (-len(encoded) % 4)
                decoded = base64.urlsafe_b64decode(encoded + padding).decode(
                    "utf-8", errors="ignore"
                )
                if decoded.startswith(("http://", "https://")):
                    candidate = decoded
        except Exception:
            pass

        candidate = unquote(candidate).split("#", 1)[0]
        if (
            candidate.startswith(("http://", "https://"))
            and _source_for_url(candidate) == source
            and candidate not in seen
        ):
            seen.add(candidate)
            found.append(candidate)
            if len(found) >= 3:
                break

    return found


def _web_search_candidates(source: str, query: str) -> list[str]:
    """Try more than one search engine and return several source-specific pages."""
    results = []
    seen = set()
    for engine in ("duckduckgo", "bing"):
        for candidate in _search_engine_candidates(engine, source, query):
            if candidate not in seen:
                seen.add(candidate)
                results.append(candidate)
        if len(results) >= 5:
            break
    return results


def _search_candidates(query: str):
    """Search every configured source, not only YouTube and SoundCloud."""
    # Native search extractors are faster and more accurate where yt-dlp
    # provides them. Other sources are discovered through domain-restricted
    # web searches, then handed to that site's normal yt-dlp extractor.
    for source in _ACTIVE_SOURCE_ORDER:
        if source == "youtube":
            yield source, f"ytsearch1:{query}"
        elif source == "soundcloud":
            yield source, f"scsearch1:{query}"

    # Include web-search fallbacks for YouTube and SoundCloud too. This matters
    # when their native search endpoints are blocked but public result pages load.
    for source in _ACTIVE_SOURCE_ORDER:
        for candidate in _web_search_candidates(source, query):
            yield source, candidate

def _download_one(source: str, target: str, job: Path):
    import yt_dlp

    options = _source_options(job, source)

    if source == "youtube":
        cookie_path = _write_youtube_cookies(job)
        if cookie_path:
            options["cookiefile"] = cookie_path

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(target, download=True)
    except Exception as first_error:
        # YouTube can challenge the default web client on hosted server IPs.
        # Retry once with alternate public player clients before moving to the
        # next source. Never reuse a web PO token with a different client.
        if source != "youtube":
            raise
        fallback_options = _source_options(job, source)
        cookie_path = _write_youtube_cookies(job)
        if cookie_path:
            fallback_options["cookiefile"] = cookie_path
        fallback_options["extractor_args"] = {
            "youtube": "player_client=tv,web_safari"
        }
        try:
            with yt_dlp.YoutubeDL(fallback_options) as ydl:
                info = ydl.extract_info(target, download=True)
        except Exception as fallback_error:
            raise RuntimeError(
                "YouTube default and alternate player clients both failed. "
                f"default: {first_error}; alternate: {fallback_error}"
            ) from fallback_error

    # yt-dlp can report success while exposing the final output path only
    # through filepath/requested_downloads. Recover that file into the job
    # directory before _finish() scans for usable audio.
    if info:
        candidates = []
        for key in ("filepath", "_filename"):
            value = info.get(key)
            if value:
                candidates.append(Path(str(value)))
        for item in info.get("requested_downloads") or []:
            if isinstance(item, dict):
                value = item.get("filepath") or item.get("_filename")
                if value:
                    candidates.append(Path(str(value)))
        for candidate in candidates:
            try:
                if candidate.is_file() and candidate.stat().st_size > 0:
                    destination = job / candidate.name
                    if candidate.resolve() != destination.resolve():
                        shutil.copy2(candidate, destination)
                    break
            except (OSError, RuntimeError):
                continue

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


def _run_ffmpeg_to_mp3(
    source: Path,
    target: Path,
    bitrate_kbps: int = 192,
) -> None:
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
        f"{max(32, min(192, int(bitrate_kbps)))}k",
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
            # Allow slower Railway instances enough time for large audio files.
            timeout=max(SOCKET_TIMEOUT * 4, 300),
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

    # Telegram Bot API cloud uploads commonly reject audio around 50 MB.
    # Keep a conservative 45 MiB ceiling even if MAX_DOWNLOAD_MB is configured
    # higher, so files returned by this downloader can be sent with send_audio.
    # MAX_DOWNLOAD_MB remains an additional user-configured ceiling.
    telegram_safe_mb = 45
    effective_max_mb = min(MAX_DOWNLOAD_MB, telegram_safe_mb)
    max_size_bytes = effective_max_mb * 1024 * 1024
    target_size_bytes = int(max_size_bytes * 0.92)
    source_size_bytes = source_file.stat().st_size
    duration = max(0, int(info.get("duration") or 0))

    # bot_handlers.py accepts both MP3 and M4A. Keep either format when it
    # already fits the upload limit, avoiding needless FFmpeg work and timeouts.
    if (
        source_file.suffix.lower() in {".mp3", ".m4a"}
        and source_size_bytes <= target_size_bytes
    ):
        mp3_file = source_file
    else:
        mp3_file = job / f"{source_file.stem}-normalized.mp3"
        bitrate_kbps = 192
        if duration > 0:
            # Estimate a bitrate that fits within the target size. Keep a
            # reasonable floor; the final size check below remains authoritative.
            estimated = int((target_size_bytes * 8 / duration / 1000) * 0.90)
            bitrate_kbps = max(32, min(192, estimated))
        _run_ffmpeg_to_mp3(source_file, mp3_file, bitrate_kbps=bitrate_kbps)

    if not mp3_file.exists() or mp3_file.stat().st_size <= 0:
        raise RuntimeError("MP3 output is missing or empty")

    final_size_bytes = mp3_file.stat().st_size
    if final_size_bytes > max_size_bytes:
        raise RuntimeError(
            f"Final MP3 exceeds the effective upload limit of {effective_max_mb} MiB "
            f"(MAX_DOWNLOAD_MB={MAX_DOWNLOAD_MB}) even after compression. "
            "Try a shorter track."
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
            + " | ".join(errors[-25:])
            if errors
            else "No searchable source is configured"
        )
    finally:
        # Do not delete the job when a valid MP3 was returned.
        if not any(
            p.is_file()
            and p.suffix.lower() in {".mp3", ".m4a"}
            and p.stat().st_size > 0
            for p in job.iterdir()
        ):
            cleanup_job(job)


VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".webm", ".mkv"}
TELEGRAM_VIDEO_LIMIT_MB = 45
VIDEO_SOURCE_LIMIT_MB = 400


def _fit_video_for_telegram(source: Path, output: Path, duration: int) -> Path:
    """Keep original quality when possible; otherwise encode to a Telegram-safe MP4."""
    target_bytes = TELEGRAM_VIDEO_LIMIT_MB * 1024 * 1024
    if source.stat().st_size <= target_bytes and source.suffix.lower() == ".mp4":
        return source

    # Preserve the source resolution. Allocate the remaining size to video after audio
    # and container overhead; long videos automatically use a lower bitrate.
    if duration <= 0:
        raise RuntimeError("Could not determine video duration to safely prepare upload")
    audio_kbps = 96
    target_kbps = int((target_bytes * 8 / duration / 1000) * 0.90)
    video_kbps = max(24, target_kbps - audio_kbps)
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", f"{video_kbps}k", "-maxrate", f"{video_kbps}k",
        "-bufsize", f"{video_kbps * 2}k",
        "-c:a", "aac", "-b:a", f"{audio_kbps}k",
        "-movflags", "+faststart",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not output.exists() or output.stat().st_size <= 0:
        detail = (result.stderr or "").strip()[-1200:]
        raise RuntimeError("Could not prepare video for Telegram upload" + (f": {detail}" if detail else ""))
    if output.stat().st_size > target_bytes:
        raise RuntimeError("Video is still too large for Telegram after compression; try a shorter video")
    return output


def download_video(url: str):
    """Download the best available video, then fit it to Telegram's cloud upload limit."""
    import yt_dlp

    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("A valid http(s) video URL is required")

    job = Path(DOWNLOAD_DIR) / ("video_" + uuid.uuid4().hex)
    job.mkdir(parents=True, exist_ok=False)
    source_limit_bytes = VIDEO_SOURCE_LIMIT_MB * 1024 * 1024
    options = {
        # Prefer the highest available video stream plus best audio; fall back to
        # a site's single-file format where separate streams are not offered.
        "format": "bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "outtmpl": str(job / "%(extractor)s-%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "retries": DOWNLOAD_RETRIES,
        "fragment_retries": DOWNLOAD_RETRIES,
        "socket_timeout": SOCKET_TIMEOUT,
        "restrictfilenames": True,
        "overwrites": False,
        "max_filesize": source_limit_bytes,
        "js_runtimes": {"node": {}},
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)

        if info and info.get("entries"):
            info = next((entry for entry in info["entries"] if entry), info)
        candidates = [
            path for path in job.iterdir()
            if path.is_file()
            and path.suffix.lower() in VIDEO_EXTENSIONS
            and path.stat().st_size > 0
            and not path.stem.endswith("-telegram")
        ]
        if not candidates:
            raise RuntimeError("Video download completed without producing a usable video file")
        source = max(candidates, key=lambda item: item.stat().st_size)
        if source.stat().st_size > source_limit_bytes:
            raise RuntimeError(
                f"Original video exceeds the safe processing limit ({VIDEO_SOURCE_LIMIT_MB} MiB)"
            )

        duration = max(0, int((info or {}).get("duration") or 0))
        output = job / f"{source.stem}-telegram.mp4"
        if source.stat().st_size <= TELEGRAM_VIDEO_LIMIT_MB * 1024 * 1024 and source.suffix.lower() == ".mp4":
            final_path = source
        else:
            final_path = _fit_video_for_telegram(source, output, duration)

        if final_path.stat().st_size > TELEGRAM_VIDEO_LIMIT_MB * 1024 * 1024:
            raise RuntimeError(
                f"Prepared video exceeds Telegram's safe upload limit ({TELEGRAM_VIDEO_LIMIT_MB} MiB)"
            )
        return info or {}, str(final_path.resolve()), str(job)
    except Exception as exc:
        cleanup_job(job)
        raise RuntimeError(f"Video download failed: {exc}") from exc
