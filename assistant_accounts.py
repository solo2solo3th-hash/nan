"""Encrypted persistence for dynamically added Telegram assistant sessions.

A valid ASSISTANT_ENCRYPTION_KEY is preferred. If it is missing/malformed,
derive a stable Fernet key from the persistent Telegram bot TOKEN so setup can
happen automatically without storing a second secret in the database.

Never store login codes or 2FA passwords here.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os

from cryptography.fernet import Fernet, InvalidToken

from database import setting_get, setting_set

_STORAGE_KEY = "ASSISTANT_SESSIONS_ENCRYPTED"
_KEY_DERIVATION_CONTEXT = b"nan/assistant-session-encryption/v1"


def _fernet() -> Fernet:
    configured = os.getenv("ASSISTANT_ENCRYPTION_KEY", "").strip()
    if configured:
        try:
            return Fernet(configured.encode("ascii"))
        except (ValueError, UnicodeEncodeError):
            # Fall back to the deterministic TOKEN-derived key. Existing data
            # is usable only if it was encrypted with that same fallback key;
            # load_sessions() fails safely otherwise, and callers must not
            # overwrite the stored ciphertext.
            pass

    # Automatic, repeatable fallback: derive a separate key from the existing
    # persistent bot token. The token itself is never logged or stored here.
    bot_token = os.getenv("TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError(
            "تعذر إنشاء مفتاح التشفير تلقائياً: متغير TOKEN غير مضبوط في Railway."
        )
    digest = hmac.new(
        bot_token.encode("utf-8"),
        _KEY_DERIVATION_CONTEXT,
        hashlib.sha256,
    ).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def validate_encryption_key() -> None:
    _fernet()


def load_sessions() -> dict[int, str]:
    encrypted = setting_get(_STORAGE_KEY)
    if not encrypted:
        return {}
    try:
        payload = _fernet().decrypt(encrypted.encode("ascii"))
        raw = json.loads(payload.decode("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("invalid session-store shape")
        result: dict[int, str] = {}
        for slot, session in raw.items():
            try:
                slot_id = int(slot)
            except (TypeError, ValueError):
                continue
            if 1 <= slot_id <= 5 and isinstance(session, str) and session.strip():
                result[slot_id] = session.strip()
        return result
    except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(
            "تعذر فك تشفير حسابات المساعدين. قد يكون مفتاح التشفير تغيّر؛ "
            "لا تحذف قاعدة البيانات أو تستبدل المفتاح عشوائياً."
        ) from exc


def save_sessions(sessions: dict[int, str]) -> None:
    clean = {
        str(int(slot)): str(session).strip()
        for slot, session in sessions.items()
        if 1 <= int(slot) <= 5 and str(session).strip()
    }
    payload = json.dumps(clean, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    encrypted = _fernet().encrypt(payload).decode("ascii")
    setting_set(_STORAGE_KEY, encrypted)


def delete_sessions() -> None:
    setting_set(_STORAGE_KEY, "")
