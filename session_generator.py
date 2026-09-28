"""Local-only helper to create PYROGRAM_SESSION_STRING.

Set API_ID and API_HASH in your local shell, run this file, complete Telegram
login/2FA, copy the printed session string to Railway Variables, then delete
or keep this helper out of the deployed repository if you prefer.
"""
from __future__ import annotations
import os
from pyrogram import Client

api_id = int(os.environ["API_ID"])
api_hash = os.environ["API_HASH"]

app = Client("session_generator", api_id=api_id, api_hash=api_hash)
app.start()
try:
    print("\nPYROGRAM_SESSION_STRING=" + app.export_session_string())
finally:
    app.stop()
