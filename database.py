"""Thread-safe SQLite persistence layer."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from config import DB_PATH

PERMISSION_GROUPS = {
    "playback": {
        "play": "🎵 تشغيل",
        "pause": "⏸️ إيقاف مؤقت",
        "resume": "▶️ استئناف",
        "seek": "⏪⏩ تقديم/ترجيع 10 ثواني",
        "skip": "⏭️ تخطي",
        "stop": "⏹️ إيقاف",
        "queue": "📋 القائمة",
        "download": "📥 تنزيل للشات",
        "join": "🔊 دخول الاتصال",
        "leave": "🚪 مغادرة الاتصال",
        "volume": "🔊 التحكم بالصوت",
    },
    "users": {
        "view": "👥 عرض المستخدمين",
        "ban": "🚫 حظر",
        "unban": "✅ فك الحظر",
        "banned": "📋 عرض المحظورين",
    },
    "admins": {
        "view": "📋 عرض المشرفين",
        "add": "➕ إضافة مشرف",
        "remove": "➖ حذف مشرف",
        "permissions": "🔐 تعديل حقوق المشرفين",
    },
    "broadcast": {
        "send": "📣 إرسال الإذاعة",
        "users": "👤 إذاعة للأعضاء",
        "all": "🌐 إذاعة للكل",
        "channels": "📢 إذاعة للقنوات",
    },
    "subscriptions": {
        "view": "📋 عرض الاشتراك",
        "add": "➕ إضافة اشتراك",
        "remove": "➖ حذف اشتراك",
        "toggle": "🔛 تشغيل/إيقاف الإجباري",
    },
    "stats": {
        "view": "📊 عرض الإحصائيات",
    },
    "user_panel": {
        "view": "👤 فتح لوحة العضو",
        "start_text": "📝 تعديل نص /start",
        "start_image": "🖼️ تعديل صورة /start",
        "button1": "🔘 تعديل الزر الأول",
        "button2": "🔘 تعديل الزر الثاني",
        "links": "🔗 إدارة روابط وأزرار /start",
    },
    "playback_panel": {
        "credit": "✍️ تعديل كتابة لوحة التشغيل",
        "music_button": "🎵 تعديل زر الموسيقى",
        "image": "🖼️ تعديل صورة لوحة التشغيل",
        "source1": "1️⃣ تعديل المصدر الأول",
        "source2": "2️⃣ تعديل المصدر الثاني",
    },
    "settings": {
        "view": "⚙️ فتح إعدادات البوت",
    },
    "sources": {
        "youtube": "▶️ مصدر YouTube",
        "spotify": "🟢 مصدر Spotify/metadata",
        "soundcloud": "🟠 مصدر SoundCloud",
        "audius": "🔵 مصدر Audius",
        "jamendo": "🟣 مصدر Jamendo",
        "bandcamp": "🟤 مصدر Bandcamp",
        "audiomack": "🟡 مصدر Audiomack",
        "mixcloud": "🟪 مصدر Mixcloud",
        "internet_archive": "🗄️ مصدر Internet Archive",
        "vimeo": "🔷 مصدر Vimeo",
        "dailymotion": "🔴 مصدر Dailymotion",
    },
    "channels": {
        "view": "📢 عرض القنوات",
        "manage": "🛠️ إدارة القنوات",
    },
    "social": {
        "manage": "🌐 إدارة الروابط الاجتماعية",
    },
}

PERMISSIONS = {
    f"{group}.{key}": label
    for group, items in PERMISSION_GROUPS.items()
    for key, label in items.items()
}

# Names used by older modules are kept as compatibility aliases. New code
# should always use the granular keys above.
LEGACY_PERMISSION_ALIASES = {
    "playback": "playback.play",
    "users": "users.view",
    "admins": "admins.view",
    "broadcast": "broadcast.send",
    "subscriptions": "subscriptions.view",
    "stats": "stats.view",
    "user_panel": "user_panel.view",
    "settings": "settings.view",
    "channels": "channels.view",
    "social": "social.manage",
}


def db() -> sqlite3.Connection:
    path = Path(str(DB_PATH)).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db() -> None:
    with db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                chat_type TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS banned (user_id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                target TEXT NOT NULL UNIQUE,
                url TEXT NOT NULL,
                is_telegram INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS sudos (
                user_id INTEGER PRIMARY KEY,
                added_by INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_permissions (
                user_id INTEGER NOT NULL,
                permission TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, permission)
            );
            CREATE TABLE IF NOT EXISTS pending_inputs (
                user_id INTEGER PRIMARY KEY,
                mode TEXT NOT NULL,
                chat_id INTEGER,
                message_id INTEGER,
                created_at INTEGER NOT NULL
            );
            """
        )
        defaults = {
            "SOURCE1_NAME": "المصدر الأول", "SOURCE1_URL": "",
            "SOURCE2_NAME": "المصدر الثاني", "SOURCE2_URL": "",
            "ADD_NAME": "", "ADD_URL": "",
            "PLAY_CREDIT_NAME": "", "PLAY_CREDIT_URL": "",
            "PLAY_MUSIC_BUTTON_NAME": "", "PLAY_MUSIC_BUTTON_URL": "",
            "PLAY_BUTTON1_NAME": "", "PLAY_BUTTON1_URL": "",
            "PLAY_BUTTON2_NAME": "", "PLAY_BUTTON2_URL": "",
            "PLAY_IMAGE_FILE_ID": "", "PLAY_IMAGE_TYPE": "photo",
            "SUBS_ENABLED": "ON",
            "START_TEXT": "هذا البوت خاص بتشغيل الأغاني والفيديوهات",
            "START_IMAGE_FILE_ID": "", "START_IMAGE_TYPE": "photo",
            "CUSTOM_BTN1_NAME": "زر أول", "CUSTOM_BTN1_URL": "",
            "CUSTOM_BTN2_NAME": "زر ثاني", "CUSTOM_BTN2_URL": "",
        }
        con.executemany("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", defaults.items())
        # Remove legacy/example values from earlier panel versions without touching
        # values the owner has already customized.
        legacy_values = {
            # Values from pre-refactor builds. They are cleared only when the
            # owner has not replaced them, so existing custom settings survive.
            "SOURCE1_NAME": "SG SOURCE",
            "SOURCE2_NAME": "Source Qatar",
            "ADD_NAME": "ADD",
            "PLAY_CREDIT_NAME": "MIKEY",
        }
        for key, value in legacy_values.items():
            con.execute("UPDATE settings SET value='' WHERE key=? AND value=?", (key, value))



def setting_get(key: str) -> Optional[str]:
    with db() as con:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def setting_set(key: str, value: str) -> None:
    with db() as con:
        con.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def save_user(user: Any) -> None:
    if user is None:
        return
    with db() as con:
        con.execute(
            """INSERT INTO users(user_id,first_name,username,created_at) VALUES(?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET first_name=excluded.first_name, username=excluded.username""",
            (int(user.id), getattr(user, "first_name", "") or "", getattr(user, "username", "") or "", int(time.time())),
        )


def save_chat(chat: Any) -> None:
    if chat is None:
        return
    with db() as con:
        con.execute(
            """INSERT INTO chats(chat_id,title,chat_type,created_at) VALUES(?,?,?,?)
               ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, chat_type=excluded.chat_type""",
            (int(chat.id), getattr(chat, "title", "") or "", getattr(chat, "type", "") or "", int(time.time())),
        )


def is_banned(user_id: int) -> bool:
    with db() as con:
        return con.execute("SELECT 1 FROM banned WHERE user_id=?", (int(user_id),)).fetchone() is not None


def ban_user(user_id: int) -> None:
    with db() as con:
        con.execute("INSERT OR IGNORE INTO banned(user_id) VALUES(?)", (int(user_id),))


def unban_user(user_id: int) -> None:
    with db() as con:
        con.execute("DELETE FROM banned WHERE user_id=?", (int(user_id),))


def banned_ids() -> list[int]:
    with db() as con:
        return [int(r[0]) for r in con.execute("SELECT user_id FROM banned ORDER BY user_id").fetchall()]


def subscriptions() -> list[tuple]:
    with db() as con:
        return con.execute("SELECT id,title,target,url,is_telegram FROM subscriptions ORDER BY id").fetchall()


def classify_target(target: str) -> bool:
    value = target.strip()
    return value.startswith("@") or value.lstrip("-").isdigit()


def add_subscription(title: str, target: str, url: str, is_telegram: Optional[bool] = None) -> None:
    if is_telegram is None:
        is_telegram = classify_target(target)
    with db() as con:
        con.execute(
            """INSERT INTO subscriptions(title,target,url,is_telegram) VALUES(?,?,?,?)
               ON CONFLICT(target) DO UPDATE SET title=excluded.title,url=excluded.url,is_telegram=excluded.is_telegram""",
            (title.strip(), target.strip(), url.strip(), int(is_telegram)),
        )


def delete_subscription(target: str) -> None:
    with db() as con:
        con.execute("DELETE FROM subscriptions WHERE target=?", (target.strip(),))


def is_admin(user_id: int, developer_id: int) -> bool:
    if int(user_id) == int(developer_id):
        return True
    with db() as con:
        return con.execute("SELECT 1 FROM sudos WHERE user_id=?", (int(user_id),)).fetchone() is not None


def add_sudo(user_id: int, added_by: int) -> None:
    with db() as con:
        con.execute(
            """INSERT INTO sudos(user_id,added_by,created_at) VALUES(?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET added_by=excluded.added_by""",
            (int(user_id), int(added_by), int(time.time())),
        )
        con.executemany(
            "INSERT OR IGNORE INTO admin_permissions(user_id,permission,enabled) VALUES(?,?,0)",
            [(int(user_id), key) for key in PERMISSIONS],
        )


def remove_sudo(user_id: int) -> None:
    with db() as con:
        con.execute("DELETE FROM sudos WHERE user_id=?", (int(user_id),))
        con.execute("DELETE FROM admin_permissions WHERE user_id=?", (int(user_id),))


def list_sudos() -> list[tuple]:
    with db() as con:
        return con.execute("SELECT user_id FROM sudos ORDER BY user_id").fetchall()


def _canonical_permission(permission: str) -> str | None:
    if permission in PERMISSIONS:
        return permission
    return LEGACY_PERMISSION_ALIASES.get(permission)


def set_permission(user_id: int, permission: str, enabled: bool) -> None:
    key = _canonical_permission(permission)
    if key is None:
        raise ValueError(f"Unknown permission: {permission}")
    with db() as con:
        con.execute(
            """INSERT INTO admin_permissions(user_id,permission,enabled) VALUES(?,?,?)
               ON CONFLICT(user_id,permission) DO UPDATE SET enabled=excluded.enabled""",
            (int(user_id), key, int(bool(enabled))),
        )


def set_all_permissions(user_id: int, enabled: bool) -> None:
    with db() as con:
        con.executemany(
            """INSERT INTO admin_permissions(user_id,permission,enabled) VALUES(?,?,?)
               ON CONFLICT(user_id,permission) DO UPDATE SET enabled=excluded.enabled""",
            [(int(user_id), key, int(bool(enabled))) for key in PERMISSIONS],
        )


def get_permission_state(user_id: int, developer_id: int) -> dict[str, bool]:
    if int(user_id) == int(developer_id):
        return {key: True for key in PERMISSIONS}
    state = {key: False for key in PERMISSIONS}
    with db() as con:
        rows = con.execute(
            "SELECT permission,enabled FROM admin_permissions WHERE user_id=?",
            (int(user_id),),
        ).fetchall()
    for permission, enabled in rows:
        key = _canonical_permission(permission)
        if key in state:
            state[key] = bool(enabled)
    return state


def has_permission(user_id: int, permission: str, developer_id: int) -> bool:
    key = _canonical_permission(permission)
    if key is None:
        return False
    if int(user_id) == int(developer_id):
        return True
    if not is_admin(user_id, developer_id):
        return False
    with db() as con:
        row = con.execute(
            "SELECT enabled FROM admin_permissions WHERE user_id=? AND permission=?",
            (int(user_id), key),
        ).fetchone()
    return bool(row and row[0])

def get_permission_groups_state(user_id: int, developer_id: int) -> dict[str, dict[str, bool]]:
    state = get_permission_state(user_id, developer_id)
    return {
        group: {f"{group}.{key}": state.get(f"{group}.{key}", False) for key in items}
        for group, items in PERMISSION_GROUPS.items()
    }

def set_pending(user_id: int, mode: str, chat_id: Optional[int], message_id: Optional[int]) -> None:
    with db() as con:
        con.execute(
            """INSERT INTO pending_inputs(user_id,mode,chat_id,message_id,created_at) VALUES(?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode,chat_id=excluded.chat_id,message_id=excluded.message_id,created_at=excluded.created_at""",
            (int(user_id), mode, chat_id, message_id, int(time.time())),
        )


def get_pending(user_id: int) -> Optional[tuple]:
    with db() as con:
        return con.execute("SELECT mode,chat_id,message_id,created_at FROM pending_inputs WHERE user_id=?", (int(user_id),)).fetchone()


def clear_pending(user_id: int) -> None:
    with db() as con:
        con.execute("DELETE FROM pending_inputs WHERE user_id=?", (int(user_id),))


def counts() -> tuple[int, int, int]:
    with db() as con:
        users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        chats = con.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
        admins = con.execute("SELECT COUNT(*) FROM sudos").fetchone()[0]
    return int(users), int(chats), int(admins)



def chat_ids_by_type(chat_types: tuple[str, ...] | list[str]) -> list[int]:
    placeholders=",".join("?" for _ in chat_types)
    if not placeholders:
        return []
    with db() as con:
        rows=con.execute(f"SELECT chat_id FROM chats WHERE chat_type IN ({placeholders}) ORDER BY chat_id", tuple(chat_types)).fetchall()
    return [int(r[0]) for r in rows]

def user_ids() -> list[int]:
    with db() as con:
        return [int(r[0]) for r in con.execute("SELECT user_id FROM users").fetchall()]
