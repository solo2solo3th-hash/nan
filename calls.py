"""Isolated Pyrogram + PyTgCalls runtime.

All voice-chat operations are serialized onto one MTProto event-loop thread.
A fatal Telegram authorization-key duplication error is latched: this process
will not repeatedly reconnect with a session that Telegram has invalidated.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Awaitable, Callable

from pyrogram import Client
import pyrogram.errors as pyrogram_errors

# Compatibility aliases used by some PyTgCalls/Pyrogram combinations.
if not hasattr(pyrogram_errors, "GroupcallForbidden") and hasattr(pyrogram_errors, "GroupCallForbidden"):
    pyrogram_errors.GroupcallForbidden = pyrogram_errors.GroupCallForbidden
if not hasattr(pyrogram_errors, "GroupcallInvalid") and hasattr(pyrogram_errors, "GroupCallInvalid"):
    pyrogram_errors.GroupcallInvalid = pyrogram_errors.GroupCallInvalid

from pytgcalls import PyTgCalls
from config import API_HASH, API_ID, CALLS_READY_TIMEOUT, SESSION_STRING

log = logging.getLogger(__name__)


def _is_auth_key_duplicated(exc: BaseException) -> bool:
    """Recognize Telegram's 406 AUTH_KEY_DUPLICATED failure."""
    text = f"{type(exc).__name__}: {exc}".upper()
    return "AUTH_KEY_DUPLICATED" in text or (
        "406" in text and "CONCURRENT USAGE" in text and "AUTHORIZATION" in text
    )


class VoiceCallRunner:
    def __init__(self, session_string: str | None = None) -> None:
        # Keep each runner bound to its own Telegram account session.
        self.session_string = (session_string if session_string is not None else SESSION_STRING).strip()
        if not self.session_string:
            raise ValueError("Assistant session string is empty; configure a session before starting.")
        self.assistant: Client | None = None
        self.calls: PyTgCalls | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self._fatal_auth_error: BaseException | None = None
        self._stop = threading.Event()
        self._stream_end_handler: Callable[[int], Awaitable[None] | None] | None = None
        self._state_lock = threading.RLock()
        self._start_lock = threading.Lock()
        self._playback_state: dict[int, dict[str, Any]] = {}
        self._seek_tempfiles: dict[int, str] = {}

    def set_stream_end_handler(self, handler: Callable[[int], Awaitable[None] | None]) -> None:
        self._stream_end_handler = handler

    def _runtime_alive(self) -> bool:
        loop, thread = self.loop, self.thread
        return bool(
            loop is not None and not loop.is_closed() and loop.is_running()
            and self.calls is not None and self.ready.is_set()
            and thread is not None and thread.is_alive()
        )

    def start(self) -> None:
        with self._start_lock:
            if self._fatal_auth_error is not None:
                raise RuntimeError(
                    "Telegram invalidated this assistant session (406 AUTH_KEY_DUPLICATED). "
                    "Stop every other process using this SESSION_STRING, then restart the "
                    "Railway service. Do not retry this session in a loop."
                ) from self._fatal_auth_error
            if self._runtime_alive():
                return
            if self.thread and self.thread.is_alive():
                # A thread that is alive but not ready is still starting/stopping.
                wait_event = self.ready
            else:
                with self._state_lock:
                    self.error = None
                    self.ready.clear()
                    self._stop.clear()
                    self.loop = None
                    self.calls = None
                    self.assistant = None
                    self.thread = threading.Thread(
                        target=self._worker, name="mtproto-calls", daemon=True
                    )
                    thread = self.thread
                thread.start()
                wait_event = self.ready

        if not wait_event.wait(CALLS_READY_TIMEOUT):
            raise RuntimeError(
                f"MTProto/PyTgCalls did not become ready within {CALLS_READY_TIMEOUT}s"
            )
        self._raise_if_failed()
        if not self._runtime_alive():
            raise RuntimeError("Voice runtime stopped after startup")

    def _ensure_ready(self) -> None:
        if self._runtime_alive():
            return
        self.start()
        if not self._runtime_alive():
            self._raise_if_failed()
            raise RuntimeError("Voice runtime is not ready (event loop is closed or stopped)")

    def _raise_if_failed(self) -> None:
        if self._fatal_auth_error is not None:
            raise RuntimeError(
                "Telegram session was invalidated by AUTH_KEY_DUPLICATED. "
                "Stop duplicate session users and restart the service."
            ) from self._fatal_auth_error
        if self.error is not None:
            raise RuntimeError(f"MTProto/PyTgCalls startup failed: {self.error}") from self.error

    async def _maybe_await(self, value: Any) -> Any:
        return await value if inspect.isawaitable(value) else value

    def _register_stream_end(self) -> None:
        if self.calls is None or self._stream_end_handler is None:
            return
        from pytgcalls import filters as tg_filters
        from pytgcalls.types import StreamEnded

        handler = self._stream_end_handler

        @self.calls.on_update(tg_filters.stream_end())
        async def _on_stream_end(_: PyTgCalls, update: StreamEnded):
            result = handler(int(update.chat_id))
            if inspect.isawaitable(result):
                await result

    def _worker(self) -> None:
        loop = asyncio.new_event_loop()
        self.loop = loop
        asyncio.set_event_loop(loop)
        startup_succeeded = False
        try:
            self.assistant = Client(
                "assistant",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=self.session_string,
                in_memory=True,
            )
            self.calls = PyTgCalls(self.assistant)
            self._register_stream_end()
            result = self.calls.start()
            loop.run_until_complete(self._maybe_await(result))
            if not self.assistant.is_connected:
                raise RuntimeError("Pyrogram assistant is not connected")
            startup_succeeded = True
            self.ready.set()
            log.info("Pyrogram/PyTgCalls runtime is ready")
            loop.run_forever()
        except BaseException as exc:
            self.error = exc
            if _is_auth_key_duplicated(exc):
                self._fatal_auth_error = exc
                log.critical(
                    "Telegram returned 406 AUTH_KEY_DUPLICATED. The assistant "
                    "SESSION_STRING is being used concurrently or was invalidated. "
                    "Stopping this runtime; automatic reconnect is disabled."
                )
            else:
                log.exception("Voice runtime failed")
        finally:
            # Stop PyTgCalls only if this installed version supports stop().
            try:
                calls = self.calls
                stop_method = getattr(calls, "stop", None) if calls is not None else None
                if callable(stop_method) and not loop.is_closed():
                    result = stop_method()
                    if inspect.isawaitable(result):
                        loop.run_until_complete(result)
            except BaseException:
                log.exception("PyTgCalls cleanup failed")

            # Stop the assistant on its owning event-loop thread.
            try:
                assistant = self.assistant
                if (
                    assistant is not None
                    and assistant.is_connected
                    and not loop.is_closed()
                ):
                    result = assistant.stop()
                    if inspect.isawaitable(result):
                        loop.run_until_complete(result)
            except BaseException:
                log.exception("Pyrogram stop failed")

            try:
                if not loop.is_closed():
                    loop.close()
            except BaseException:
                log.exception("Event loop close failed")

            asyncio.set_event_loop(None)
            with self._state_lock:
                self.loop = None
                self.calls = None
                self.assistant = None
                # On startup failure, keep the event set so start() wakes and
                # raises the actual error instead of hanging until timeout.
                if (
                    startup_succeeded
                    and self.error is None
                    and self._fatal_auth_error is None
                ):
                    self.ready.clear()
                else:
                    self.ready.set()

    def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Execute a PyTgCalls method on its owning event-loop thread."""
        self._ensure_ready()
        loop, calls, thread = self.loop, self.calls, self.thread
        if (
            loop is None or loop.is_closed() or not loop.is_running()
            or calls is None or not self.ready.is_set()
            or thread is None or not thread.is_alive()
        ):
            raise RuntimeError("Voice runtime is not ready (event loop is closed or stopped)")
        if threading.current_thread() is thread:
            value = self._direct_call(method, *args, **kwargs)
            if inspect.isawaitable(value):
                raise RuntimeError(
                    f"Async PyTgCalls method {method!r} was called from its event-loop thread; "
                    "use the async bridge instead"
                )
            return value

        future: Future[Any] = Future()

        def runner() -> None:
            try:
                if loop.is_closed():
                    raise RuntimeError("Voice runtime event loop is closed")
                value = self._direct_call(method, *args, **kwargs)
                if inspect.isawaitable(value):
                    task = asyncio.ensure_future(value)
                    task.add_done_callback(lambda t: self._transfer_result(t, future))
                else:
                    future.set_result(value)
            except BaseException as exc:
                future.set_exception(exc)

        loop.call_soon_threadsafe(runner)
        return future.result(timeout=60)

    @staticmethod
    def _transfer_result(task: asyncio.Future[Any], future: Future[Any]) -> None:
        try:
            future.set_result(task.result())
        except BaseException as exc:
            future.set_exception(exc)

    def _direct_call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if self.calls is None:
            raise RuntimeError("PyTgCalls is unavailable")
        return getattr(self.calls, method)(*args, **kwargs)

    async def acall(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if self.calls is None:
            raise RuntimeError("PyTgCalls is unavailable")
        return await self._maybe_await(self._direct_call(method, *args, **kwargs))

    async def aplay(self, chat_id: int, stream: Any) -> Any:
        chat_id = int(chat_id)
        # Join first; PyTgCalls cannot stream into a voice chat the account
        # has not joined. This async path must not call the sync bridge.
        await self.aensure_assistant_in_chat(chat_id)
        result = await self.acall("play", chat_id, stream)
        if isinstance(stream, (str, os.PathLike)):
            self._cleanup_seek_file(chat_id)
            self._playback_state[chat_id] = {
                "path": str(Path(stream).resolve()),
                "offset": 0.0,
                "started_at": time.monotonic(),
                "paused": False,
                "paused_at": None,
            }
        return result

    async def aassistant_status(self, chat_id: int) -> str | None:
        assistant = self.assistant
        if assistant is None:
            return None
        try:
            me = await assistant.get_me()
            member = await assistant.get_chat_member(int(chat_id), me.id)
            return str(getattr(member, "status", "")).lower() or None
        except Exception:
            return None

    def assistant_status(self, chat_id: int) -> str | None:
        self._ensure_ready()
        loop = self.loop
        if loop is None or loop.is_closed() or not loop.is_running():
            return None
        future = asyncio.run_coroutine_threadsafe(self.aassistant_status(int(chat_id)), loop)
        try:
            return future.result(timeout=20)
        except Exception:
            future.cancel()
            return None

    async def aensure_assistant_in_chat(self, chat_id: int) -> bool:
        assistant = self.assistant
        if assistant is None:
            raise RuntimeError("Assistant session is not available")
        chat_id = int(chat_id)
        me = await assistant.get_me()
        try:
            member = await assistant.get_chat_member(chat_id, me.id)
            status = str(getattr(member, "status", "")).lower()
            if status not in {"left", "kicked", "banned"}:
                if status != "restricted" or bool(getattr(member, "is_member", False)):
                    return True
        except Exception as exc:
            log.info(
                "Assistant membership lookup failed for %s; attempting auto-join: %s",
                chat_id,
                exc,
            )

        chat = await assistant.get_chat(chat_id)
        username = getattr(chat, "username", None)
        invite_link = getattr(chat, "invite_link", None)
        join_target = f"@{username}" if username else invite_link
        if not join_target:
            raise RuntimeError(
                "Assistant is not a member of this chat and Telegram did not provide "
                "a public username or invite link for automatic joining"
            )
        await assistant.join_chat(join_target)
        return True

    def ensure_assistant_in_chat(self, chat_id: int) -> bool:
        self._ensure_ready()
        loop = self.loop
        if loop is None or loop.is_closed() or not loop.is_running():
            raise RuntimeError("Voice runtime is not ready (event loop is closed or stopped)")
        future = asyncio.run_coroutine_threadsafe(
            self.aensure_assistant_in_chat(int(chat_id)), loop
        )
        try:
            return bool(future.result(timeout=30))
        except Exception:
            future.cancel()
            raise

    def assistant_blocked(self, chat_id: int) -> bool:
        return self.assistant_status(chat_id) in {"banned", "kicked"}

    async def aassistant_present(self, chat_id: int) -> bool:
        return (await self.aassistant_status(chat_id)) not in {
            None, "left", "kicked", "banned"
        }

    def assistant_present(self, chat_id: int) -> bool:
        self._ensure_ready()
        loop = self.loop
        if loop is None or loop.is_closed() or not loop.is_running():
            return False
        future = asyncio.run_coroutine_threadsafe(
            self.aassistant_present(int(chat_id)), loop
        )
        try:
            return bool(future.result(timeout=20))
        except Exception:
            future.cancel()
            return False

    def _cleanup_seek_file(self, chat_id: int) -> None:
        old = self._seek_tempfiles.pop(int(chat_id), None)
        if old:
            try:
                os.unlink(old)
            except OSError:
                pass

    def play(self, chat_id: int, stream: Any) -> Any:
        chat_id = int(chat_id)
        self.ensure_assistant_in_chat(chat_id)
        result = self.call("play", chat_id, stream)
        if isinstance(stream, (str, os.PathLike)):
            self._cleanup_seek_file(chat_id)
            self._playback_state[chat_id] = {
                "path": str(Path(stream).resolve()),
                "offset": 0.0,
                "started_at": time.monotonic(),
                "paused": False,
                "paused_at": None,
            }
        return result

    def pause(self, chat_id: int) -> Any:
        chat_id = int(chat_id)
        result = self.call("pause", chat_id)
        state = self._playback_state.get(chat_id)
        if state and not state.get("paused"):
            state["offset"] = self._current_position(state)
            state["paused"] = True
            state["paused_at"] = time.monotonic()
        return result

    def resume(self, chat_id: int) -> Any:
        chat_id = int(chat_id)
        result = self.call("resume", chat_id)
        state = self._playback_state.get(chat_id)
        if state and state.get("paused"):
            state["started_at"] = time.monotonic()
            state["paused"] = False
            state["paused_at"] = None
        return result

    @staticmethod
    def _current_position(state: dict[str, Any]) -> float:
        offset = float(state.get("offset", 0.0))
        if state.get("paused"):
            return offset
        started = state.get("started_at")
        return offset + (max(0.0, time.monotonic() - started) if started else 0.0)

    def seek(
        self,
        chat_id: int,
        delta_seconds: int,
        duration: int | float | None = None,
    ) -> float:
        chat_id = int(chat_id)
        state = self._playback_state.get(chat_id)
        if not state or not state.get("path"):
            raise RuntimeError("لا توجد أغنية قابلة للتقديم أو الترجيع حالياً")
        original = Path(state["path"])
        if not original.is_file():
            raise RuntimeError("ملف الأغنية الحالية غير موجود")
        current = self._current_position(state)
        limit = max(0.0, float(duration or 0))
        target = max(0.0, current + int(delta_seconds))
        if limit > 0:
            target = min(target, max(0.0, limit - 1.0))
        if abs(target - current) < 0.5:
            return target
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("FFmpeg غير مثبت أو غير موجود في PATH")
        fd, temp_path = tempfile.mkstemp(
            prefix=f"tgseek_{chat_id}_",
            suffix=".mp3",
        )
        os.close(fd)
        command = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{target:.3f}", "-i", str(original), "-vn",
            "-codec:a", "libmp3lame", "-b:a", "192k", temp_path,
        ]
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if (
                proc.returncode != 0
                or not os.path.isfile(temp_path)
                or os.path.getsize(temp_path) <= 0
            ):
                detail = (proc.stderr or "").strip()[-800:]
                raise RuntimeError(
                    "FFmpeg فشل في تجهيز موضع التشغيل"
                    + (f": {detail}" if detail else "")
                )
            was_paused = bool(state.get("paused"))
            self.call("play", chat_id, temp_path)
            old_temp = self._seek_tempfiles.get(chat_id)
            self._seek_tempfiles[chat_id] = temp_path
            if old_temp and old_temp != temp_path:
                try:
                    os.unlink(old_temp)
                except OSError:
                    pass
            self._playback_state[chat_id] = {
                "path": str(original),
                "offset": target,
                "started_at": time.monotonic(),
                "paused": False,
                "paused_at": None,
            }
            if was_paused:
                self.call("pause", chat_id)
                self._playback_state[chat_id]["paused"] = True
                self._playback_state[chat_id]["offset"] = target
                self._playback_state[chat_id]["paused_at"] = time.monotonic()
            return target
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

    def leave(self, chat_id: int) -> Any:
        chat_id = int(chat_id)
        result = self.call("leave_call", chat_id)
        self._playback_state.pop(chat_id, None)
        self._cleanup_seek_file(chat_id)
        return result

    def volume(self, chat_id: int, value: int) -> Any:
        return self.call("change_volume_call", int(chat_id), int(value))

    def stop(self) -> None:
        self._stop.set()
        loop = self.loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        thread = self.thread
        if thread and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=15)
