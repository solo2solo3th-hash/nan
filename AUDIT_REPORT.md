# Audit Report — Group Admin + Auto Assistant + Developer Notification

## Latest change
- Added a service-message handler for `new_chat_members`.
- When the bot itself is added to a group/supergroup, the developer receives a private notification.
- Notification includes group title, chat ID, inviter name/username, and public link when available.
- The notification is only sent when the newly added member matches the bot's own ID.

## Verification
- `python -m compileall -q .` — PASS.
- Existing group-admin playback controls preserved.
- Existing automatic assistant join path preserved.
- No secrets were added to source files.

## Runtime limitation
Telegram must deliver the `new_chat_members` service update to the bot. The developer must also be able to receive private messages from the bot (for example, having started the bot previously), otherwise Telegram may reject the private notification.
