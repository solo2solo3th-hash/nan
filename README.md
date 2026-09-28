# Telegram Music Bot — Railway modular build

This build keeps the original public compatibility points but splits the application into independent modules:

- `main.py` — bootstrap only.
- `calls.py` — Pyrogram + PyTgCalls runtime, isolated to one event-loop thread.
- `music_player.py` — per-chat queue/state and file cleanup.
- `developer_panel.py` — developer-only admin management and permissions.
- `admin_panel.py` — common admin UI.
- `member_panel.py` — `/start` and member-facing settings.
- `bot_handlers.py` — Telegram command/callback wiring.
- `database.py` — SQLite persistence.
- `downloader.py` — yt-dlp + FFmpeg acquisition.
- `subscriptions.py` — mandatory channel membership.
- `config.py` — environment variables and validation.

## Railway

Use a single long-running service. Railway detects the root `Dockerfile` automatically. The bot does not need a public HTTP port for polling.

Attach a Railway Volume at `/data` if you want SQLite data and downloaded files to survive deployments/restarts. Railway documents that non-volume filesystem storage is ephemeral.

## Required variables

`TOKEN`, `API_ID`, `API_HASH`, `DEVELOPER_ID`, `PYROGRAM_SESSION_STRING`.

Generate the Pyrogram session string from a trusted local environment; never commit it to Git or paste it into public chats.

## First deployment checklist

1. Create the Railway service from the repository.
2. Add the variables from `.env.example` in Railway Variables.
3. Attach a Volume mounted at `/data`.
4. Deploy.
5. Confirm the logs show the MTProto/PyTgCalls runtime became ready and bot polling started.
6. Make sure the bot is an administrator in the target group with permission to invite users; the assistant is invited automatically on first playback.
7. Test `/play`, `/pause`, `/resume`, `/skip`, `/queue`, `/stop`.

## Important operational note

A voice-chat music bot depends on Telegram MTProto credentials and an authorized assistant account. Code compilation alone cannot prove that a real Telegram account can join a particular voice chat. The final live check must be performed after deployment in a real group.

## Dependency / image choices

- Python 3.11 is used deliberately because the pinned `TgCrypto==1.2.5` release provides CPython 3.11 Linux wheels but not CPython 3.12 wheels; this avoids compiling TgCrypto during the Railway build.
- PyTgCalls remains pinned to `2.3.3` rather than moving to the 3.x line, because this code targets the 2.x API (`play`, `pause`, `resume`, `leave_call`) and no major migration was required.
- Node.js 22 is included for yt-dlp's current YouTube EJS runtime path. yt-dlp's current guidance lists Node 22 as a supported runtime and `yt-dlp[default]` provides the EJS package.
- FFmpeg is installed as a system binary; both `ffmpeg` and `ffprobe` are expected to be present in the image.

## Verification status

The source tree passes Python compilation, AST parsing, local SQLite permission tests, queue/cleanup tests, downloader configuration tests, required-environment validation, and a local-import dependency/cycle audit. A Docker image build was not executed in this development environment because the Docker CLI is not installed here; Railway must perform the final Docker build.

The real Telegram voice path is intentionally not marked as live-verified until the deployed assistant session is tested in an actual group voice chat.

## Natural Arabic commands

Inside groups, `تشغيل <اسم الأغنية>` and `شغل <اسم الأغنية>` download the audio and play it in the group's voice chat.
`يوت <اسم الأغنية>`, `نزل <اسم الأغنية>`, and `تنزيل <اسم الأغنية>` download the audio and send it to the current group/channel as an audio file; they do not join or play voice chat.

## Audio sources

The downloader has a 10-source matrix: YouTube, SoundCloud, Audius, Jamendo, Bandcamp, Audiomack, Mixcloud, Internet Archive, Vimeo, and Dailymotion. Direct URLs are routed to the matching yt-dlp extractor. Text search uses native search prefixes for YouTube and SoundCloud; the other sources are supported by direct source URLs unless a dedicated search API is configured.

yt-dlp notes that supported extractors can break as websites change, so support is verified at runtime rather than assumed permanent.

## Group administrator playback control

Group owners/administrators can control the playback of their own group without being added as global bot sudos. The check is performed against Telegram's live `get_chat_member` status for the current group.

Use `/control` (or `/voice`) inside the group to open the current playback control card. This does not grant access to the developer panel or global bot administration.


## Automatic assistant joining

The bot can automatically invite the Pyrogram assistant into a group when playback is requested. The bot must be an administrator in that group with permission to invite/add members. It creates a short-lived, one-use invite link and the already-authorized assistant account joins through that link. No manual assistant addition is needed after the bot has the required group permission. Telegram permissions and the assistant account's privacy/security settings remain authoritative.
