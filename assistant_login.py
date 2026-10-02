"""Short-lived developer-only Telegram login flow.

All synchronous Pyrogram operations run on one dedicated worker thread so
TeleBot handler threads cannot move a client between event loops. Credentials
remain in memory only and are never logged or persisted.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

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
_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="assistant-login")


def _run(fn, *args):
    return _worker.submit(fn, *args).result()


def _cancel_login(user_id: int) -> None:
    with _lock:
        item = _pending.pop(int(user_id), None)
    if item is not None:
        try:
            item.client.disconnect()
        except Exception:
            pass


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


def _begin_login(user_id: int, phone: str) -> None:
    user_id = int(user_id)
    phone = phone.strip()
    if not phone or len(phone) > 32 or not phone.startswith("+"):
        raise ValueError("أرسل رقم الهاتف بصيغة دولية، مثال: +9647XXXXXXXXX")
    _cancel_login(user_id)
    client = Client(
        f"assistant-login-{user_id}",
        api_id=API_ID,
        api_hash=API_HASH,
        in_memory=True,
    )
    try:
        client.connect()
        sent = client.send_code(phone)
        with _lock:
            _pending[user_id] = _Login(
                client, phone, sent.phone_code_hash, time.monotonic()
            )
    except Exception:
        try:
            client.disconnect()
        except Exception:
            pass
        raise


def begin_login(user_id: int, phone: str) -> None:
    _run(_begin_login, user_id, phone)


def _complete_login(
    user_id: int, code: str | None = None, password: str | None = None
) -> str | None:
    item = _get(user_id)
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
    _cancel_login(int(user_id))
    return session_string


def complete_login(
    user_id: int, code: str | None = None, password: str | None = None
) -> str | None:
    return _run(_complete_login, user_id, code, password)


def cancel_login(user_id: int) -> None:
    _run(_cancel_login, user_id)
