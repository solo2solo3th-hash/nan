"""Five-slot assistant pool for the Telegram music bot.

Each configured assistant runs in an isolated runtime so groups can use
different accounts concurrently. Session strings are read exclusively from
Railway environment variables and are never displayed or logged.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Awaitable, Callable

from calls import VoiceCallRunner
from config import SESSION_STRING
from database import setting_get, setting_set


class AssistantPool:
    """Runner-compatible facade that routes each chat to its owning assistant."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[int, str] = {}
        seen_sessions: set[str] = set()
        for slot in range(1, 6):
            value = os.getenv(f"ASSISTANT_SESSION_{slot}", "").strip()
            if slot == 1 and not value:
                value = SESSION_STRING.strip()
            if not value:
                continue
            # One authorization key must never be run by two Pyrogram clients.
            if value in seen_sessions:
                raise ValueError(
                    f"جلسة تيليجرام مكررة في إعدادات المساعدين (الخانة {slot}). "
                    "خصص جلسة مختلفة لكل حساب."
                )
            seen_sessions.add(value)
            self._sessions[slot] = value

        requested = os.getenv("ACTIVE_ASSISTANT", "1").strip()
        try:
            active = int(requested)
        except ValueError:
            active = 1
        self._selected = active if active in self._sessions else min(self._sessions, default=1)
        self._runners = {
            slot: VoiceCallRunner(session_string=session)
            for slot, session in self._sessions.items()
        }
        # Active playback ownership is temporary; group assignment is persistent.
        self._chat_slots: dict[int, int] = {}
        self._chat_assignments: dict[int, int] = {}
        self._persistent_settings_loaded = False
        self._stream_end_handler: Callable[[int], Awaitable[None] | None] | None = None

    def _load_persistent_settings(self) -> None:
        # main.py initializes SQLite after importing modules, so load settings
        # lazily at start rather than querying tables during module import.
        if self._persistent_settings_loaded:
            return
        stored_selected = setting_get("ACTIVE_ASSISTANT")
        if stored_selected:
            try:
                stored_slot = int(stored_selected)
                if stored_slot in self._sessions:
                    self._selected = stored_slot
            except (TypeError, ValueError):
                pass
        try:
            raw_assignments = json.loads(setting_get("ASSISTANT_CHAT_ASSIGNMENTS") or "{}")
            if isinstance(raw_assignments, dict):
                valid_assignments: dict[int, int] = {}
                for raw_chat_id, raw_slot in raw_assignments.items():
                    try:
                        chat_id = int(raw_chat_id)
                        slot = int(raw_slot)
                    except (TypeError, ValueError):
                        continue
                    if slot in self._runners:
                        valid_assignments[chat_id] = slot
                self._chat_assignments = valid_assignments
        except (TypeError, ValueError, json.JSONDecodeError):
            self._chat_assignments = {}
        self._persistent_settings_loaded = True

    @property
    def selected_slot(self) -> int:
        return self._selected

    @property
    def assistant(self):
        return self._selected_runner().assistant

    @property
    def calls(self):
        return self._selected_runner().calls

    def _selected_runner(self) -> VoiceCallRunner:
        runner = self._runners.get(self._selected)
        if runner is None:
            raise RuntimeError(
                f"جلسة المساعد رقم {self._selected} غير مضبوطة. "
                "أضف ASSISTANT_SESSION_<رقم> في متغيرات Railway."
            )
        return runner

    def _runner_for_chat(self, chat_id: int) -> VoiceCallRunner:
        chat_id = int(chat_id)
        slot = self._chat_slots.get(
            chat_id, self._chat_assignments.get(chat_id, self._selected)
        )
        runner = self._runners.get(slot)
        if runner is None:
            raise RuntimeError(f"جلسة المساعد رقم {slot} غير مضبوطة.")
        return runner

    def set_stream_end_handler(self, handler):
        self._stream_end_handler = handler
        for runner in self._runners.values():
            runner.set_stream_end_handler(handler)

    def slots_status(self) -> list[dict[str, Any]]:
        rows = []
        for slot in range(1, 6):
            runner = self._runners.get(slot)
            rows.append({
                "slot": slot,
                "configured": runner is not None,
                "selected": slot == self._selected,
                "running": bool(runner and runner._runtime_alive()),
            })
        return rows

    def select(self, slot: int) -> None:
        slot = int(slot)
        with self._lock:
            if slot not in self._runners:
                raise ValueError(f"المساعد {slot} غير مضبوط في متغيرات Railway.")
            self._selected = slot
            setting_set("ACTIVE_ASSISTANT", str(slot))
            # Per-group runners may be active simultaneously; changing the
            # default must not interrupt music already playing in other chats.

    def assign_chat(self, chat_id: int, slot: int) -> None:
        chat_id, slot = int(chat_id), int(slot)
        with self._lock:
            if slot not in self._runners:
                raise ValueError(f"المساعد {slot} غير مضبوط في متغيرات Railway.")
            self._chat_assignments[chat_id] = slot
            setting_set(
                "ASSISTANT_CHAT_ASSIGNMENTS",
                json.dumps(self._chat_assignments, separators=(",", ":")),
            )

    def unassign_chat(self, chat_id: int) -> None:
        chat_id = int(chat_id)
        with self._lock:
            self._chat_assignments.pop(chat_id, None)
            setting_set(
                "ASSISTANT_CHAT_ASSIGNMENTS",
                json.dumps(self._chat_assignments, separators=(",", ":")),
            )

    def assigned_slot(self, chat_id: int) -> int | None:
        return self._chat_assignments.get(int(chat_id))

    def start(self) -> None:
        self._load_persistent_settings()
        # Each configured assistant gets an isolated runtime so different
        # groups can use different accounts concurrently. A broken secondary
        # account must not take down otherwise-working assistants.
        failures: dict[int, Exception] = {}
        for slot, runner in self._runners.items():
            try:
                runner.start()
            except Exception as exc:
                failures[slot] = exc
        selected_runner = self._runners.get(self._selected)
        if selected_runner is None:
            raise RuntimeError("لا يوجد مساعد افتراضي مضبوط.")
        if self._selected in failures:
            raise RuntimeError(
                f"تعذر تشغيل المساعد الافتراضي {self._selected}: "
                f"{failures[self._selected]}"
            ) from failures[self._selected]
        for slot, exc in failures.items():
            if slot != self._selected:
                import logging
                logging.getLogger(__name__).warning(
                    "Assistant slot %s failed to start (%s); other assistants remain available.",
                    slot, type(exc).__name__,
                )

    def stop(self) -> None:
        for runner in self._runners.values():
            try:
                runner.stop()
            except Exception:
                pass

    def play(self, chat_id: int, stream: Any):
        chat_id = int(chat_id)
        with self._lock:
            slot = self._chat_slots.get(
                chat_id, self._chat_assignments.get(chat_id, self._selected)
            )
            runner = self._runners.get(slot)
            if runner is None:
                raise RuntimeError(f"جلسة المساعد رقم {slot} غير مضبوطة.")
            result = runner.play(chat_id, stream)
            self._chat_slots[chat_id] = slot
            return result

    async def aplay(self, chat_id: int, stream: Any):
        chat_id = int(chat_id)
        with self._lock:
            runner = self._runner_for_chat(chat_id)
            result = await runner.aplay(chat_id, stream)
            self._chat_slots[chat_id] = self._slot_for_runner(runner)
            return result

    def _slot_for_runner(self, runner: VoiceCallRunner) -> int:
        for slot, candidate in self._runners.items():
            if candidate is runner:
                return slot
        raise RuntimeError("Assistant runner is not registered in this pool.")

    async def acall(self, method: str, *args: Any, **kwargs: Any):
        chat_id = int(args[0]) if args and isinstance(args[0], (int, str)) else None
        if method == "leave_call" and chat_id is not None:
            return self.leave(chat_id)
        runner = self._runner_for_chat(chat_id) if chat_id is not None else self._selected_runner()
        return await runner.acall(method, *args, **kwargs)

    def pause(self, chat_id: int):
        return self._runner_for_chat(chat_id).pause(chat_id)

    def resume(self, chat_id: int):
        return self._runner_for_chat(chat_id).resume(chat_id)

    def seek(self, chat_id: int, delta_seconds: int, duration=None):
        return self._runner_for_chat(chat_id).seek(chat_id, delta_seconds, duration)

    def leave(self, chat_id: int):
        chat_id = int(chat_id)
        try:
            return self._runner_for_chat(chat_id).leave(chat_id)
        finally:
            self._chat_slots.pop(chat_id, None)

    def assistant_blocked(self, chat_id: int) -> bool:
        return self._runner_for_chat(chat_id).assistant_blocked(chat_id)

    def assistant_present(self, chat_id: int) -> bool:
        return self._runner_for_chat(chat_id).assistant_present(chat_id)

    def volume(self, chat_id: int, value: int):
        return self._runner_for_chat(chat_id).volume(chat_id, value)

    def __getattr__(self, name: str):
        # Preserve compatibility for any existing runner methods not explicitly
        # routed above; chat-aware methods should be added explicitly as needed.
        return getattr(self._selected_runner(), name)
