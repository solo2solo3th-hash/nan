"""Short-lived, developer-only Telegram login flow for assistant accounts.

Login codes and 2FA passwords are held only in memory and are never persisted
or logged. Exported session strings are returned to the caller for encrypted
storage by assistant_accounts.py via AssistantPool.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded

from config import API_HASH, API_ID

@dataclass
class _Login:
    client: Client
    phone: str
    phone_code_hash: str
    created_at: float


_lock = threading.RLock()
_pending: dict[int, _Login] = {}
_TIMEOUT_SECONDS = 600


def _get(user_id: int) -> _Login:
    with _lock:
        item = _pending.get(int(user_id))
        if item is None:
            raise RuntimeError("ماكو عملية تسجيل دخول فعالة. ابدأ من جديد.")
        if time.monotonic() - item.created_at > _TIMEOUT_SECONDS:
            _pending.pop(int(user_id), None)
            try:
                item.client.disconnect()
            except Exception:
                pass
            raise RuntimeError("انتهت مهلة تسجيل الدخول. ابدأ من جديد.")
        return item


def begin_login(user_id: int, phone: str) -> None:
    user_id = int(user_id)
    phone = phone.strip()
    if not phone or len(phone) > 32 or not phone.startswith("+"):
        raise ValueError("أرسل رقم الهاتف بصيغة دولية، مثال: +9647XXXXXXXXX")
    cancel_login(user_id)
    client = Client(
        f"assistant-login-{user_id}",
        api_id=API_ID,
        api_hash=API_HASH,
        in_memory=True,
    )
    try:
        client.connect()
        sent = client.send_code(phone)
        item = _Login(client, phone, sent.phone_code_hash, time.monotonic())
        with _lock:
            _pending[user_id] = item
    except Exception:
        try:
            client.disconnect()
        except Exception:
            pass
        raise


def complete_login(user_id: int, code: str | None = None, password: str | None = None) -> str | None:
    item = _get(user_id)
    try:
        if code is not None:
            try:
                item.client.sign_in(
                    phone_number=item.phone,
                    phone_code_hash=item.phone_code_hash,
                    phone_code=code.strip().replace(" ", ""),
                )
            except SessionPasswordNeeded:
                return None
        elif password is not None:
            item.client.check_password(password)
        else:
            raise ValueError("رمز التحقق مطلوب.")
        session_string = item.client.export_session_string()
        cancel_login(int(user_id))
        return session_string
    except SessionPasswordNeeded:
        return None


def cancel_login(user_id: int) -> None:
    with _lock:
        item = _pending.pop(int(user_id), None)
    if item is not None:
        try:
            item.client.disconnect()
        except Exception:
            pass
