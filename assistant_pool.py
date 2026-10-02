"""Five-slot assistant pool for the Telegram music bot.

Only the selected assistant is started. Session strings are read exclusively
from Railway environment variables and are never displayed or logged.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Awaitable, Callable

from calls import VoiceCallRunner
from config import API_HASH, API_ID, SESSION_STRING


class AssistantPool:
    """Runner-compatible facade that routes each chat to its owning assistant."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[int, str] = {}
        for slot in range(1, 6):
            value = os.getenv(f"ASSISTANT_SESSION_{slot}", "").strip()
            if slot == 1 and not value:
                value = SESSION_STRING.strip()
            if value:
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
        self._chat_slots: dict[int, int] = {}
        self._stream_end_handler: Callable[[int], Awaitable[None] | None] | None = None

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
        slot = self._chat_slots.get(int(chat_id), self._selected)
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
            if slot == self._selected:
                return
            active_chats = [
                chat_id for chat_id, owner_slot in self._chat_slots.items()
                if owner_slot == self._selected
            ]
            if active_chats:
                raise RuntimeError(
                    "لا يمكن تبديل المساعد أثناء وجود تشغيل مرتبط بالمساعد الحالي. "
                    "أنهوا التشغيل في المجموعات أولاً ثم حاولوا مجدداً."
                )
            old_runner = self._runners.get(self._selected)
            if old_runner is not None:
                old_runner.stop()
            self._selected = slot
            self._selected_runner().start()

    def start(self) -> None:
        self._selected_runner().start()

    def stop(self) -> None:
        for runner in self._runners.values():
            try:
                runner.stop()
            except Exception:
                pass

    def play(self, chat_id: int, stream: Any):
        chat_id = int(chat_id)
        with self._lock:
            slot = self._chat_slots.get(chat_id, self._selected)
            runner = self._runners.get(slot)
            if runner is None:
                raise RuntimeError(f"جلسة المساعد رقم {slot} غير مضبوطة.")
            result = runner.play(chat_id, stream)
            self._chat_slots[chat_id] = slot
            return result

    async def aplay(self, chat_id: int, stream: Any):
        chat_id = int(chat_id)
        runner = self._runner_for_chat(chat_id)
        await runner.aplay(chat_id, stream)

    async def acall(self, method: str, *args: Any, **kwargs: Any):
        chat_id = int(args[0]) if args and isinstance(args[0], (int, str)) else None
        runner = self._runner_for_chat(chat_id) if chat_id is not None else self._selected_runner()
        result = runner.call(method, *args, **kwargs)
        if hasattr(result, "__await__"):
            return await result
        return result

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
