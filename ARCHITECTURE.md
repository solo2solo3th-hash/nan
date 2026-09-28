# Architecture / verification notes

## Runtime separation
1. `main.py` starts the bot and voice runtime.
2. `bot_handlers.py` owns Telegram Bot API handlers and playback callbacks.
3. `calls.py` owns Pyrogram + PyTgCalls on one MTProto event-loop thread.
4. `music_player.py` owns per-chat queue, current track, playback position, pause state, and file lifecycle.
5. `downloader.py` owns yt-dlp/FFmpeg acquisition and the 10-source fallback/direct-URL matrix.
6. `database.py` owns SQLite and granular permissions.

## Seek design
PyTgCalls 2.3.3 accepts FFmpeg parameters through `MediaStream`. The seek operation therefore restarts the current local file with an FFmpeg input offset rather than calling a private PyTgCalls seek API. The project tracks the current logical position independently so `-10s`, `+10s`, pause, and resume remain consistent.

## Source design
The configured source order is ten services. URL input is routed to the matching yt-dlp extractor. Search-by-text currently uses native `ytsearch1:` and `scsearch1:` expressions; other sources are not falsely advertised as text-search providers.


## Automatic assistant membership
The Bot API creates a short-lived, one-use invite only when the assistant is not already a member. A per-chat lock prevents duplicate invite attempts, and the invite is revoked after the join attempt. Telegram permissions remain authoritative.
