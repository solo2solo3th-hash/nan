"""Encrypted persistence for dynamically added Telegram assistant sessions.

The encryption key must be configured as ASSISTANT_ENCRYPTION_KEY in Railway.
Never store login codes or 2FA passwords here.
"""
from __future__ import annotations

import json
import os
from cryptography.fernet import Fernet, InvalidToken

from database import setting_get, setting_set

_STORAGE_KEY = "ASSISTANT_SESSIONS_ENCRYPTED"


def _fernet() -> Fernet:
    key = os.getenv("ASSISTANT_ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "اضبط ASSISTANT_ENCRYPTION_KEY في Railway قبل إضافة حسابات من لوحة المطور."
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError(
            "ASSISTANT_ENCRYPTION_KEY غير صالح. أنشئ مفتاح Fernet صالحاً واحفظه في Railway."
        ) from exc


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
            "تعذر فك تشفير حسابات المساعدين. تأكد أن ASSISTANT_ENCRYPTION_KEY لم يتغير."
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
