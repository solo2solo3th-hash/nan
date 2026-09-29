"""Isolated Pyrogram + PyTgCalls runtime.

All voice-chat operations are serialized onto the MTProto event-loop thread.
TeleBot handlers never touch PyTgCalls directly, which avoids cross-thread
state corruption.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from concurrent.futures import Future
from typing import Any, Awaitable, Callable

from pyrogram import Client

# PyTgCalls 2.3.3 is paired with PyrogramMod 2.4.1 in requirements.txt.
# PyrogramMod provides the Pyrogram-compatible ``pyrogram`` module and
# the exception names expected by this PyTgCalls release.
from pytgcalls import PyTgCalls

from config import API_HASH, API_ID, CALLS_READY_TIMEOUT, SESSION_STRING

log = logging.getLogger(__name__)


class VoiceCallRunner:
    def __init__(self) -> None:
        self.assistant: Client | None = None
        self.calls: PyTgCalls | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self._stop = threading.Event()
        self._stream_end_handler: Callable[[int], Awaitable[None] | None] | None = None
        self._state_lock = threading.RLock()
        self._start_lock = threading.Lock()

    def set_stream_end_handler(self, handler: Callable[[int], Awaitable[None] | None]) -> None:
        self._stream_end_handler = handler

    def _runtime_alive(self) -> bool:
        loop = self.loop
        thread = self.thread
        return bool(loop is not None and not loop.is_closed() and loop.is_running() and self.calls is not None and self.ready.is_set() and thread is not None and thread.is_alive())

    def start(self) -> None:
        # Never reuse a stale/closed event loop.
        with self._start_lock:
            if self._runtime_alive():
                return
            if self.thread and self.thread.is_alive():
                if not self.ready.wait(CALLS_READY_TIMEOUT):
                    raise RuntimeError("Voice runtime is already starting but did not become ready")
                self._raise_if_failed()
                if not self._runtime_alive():
                    raise RuntimeError("Voice runtime stopped immediately after startup")
                return
            with self._state_lock:
                self.error = None
                self.ready.clear()
                self._stop.clear()
                self.loop = None
                self.calls = None
                self.assistant = None
                self.thread = threading.Thread(target=self._worker, name="mtproto-calls", daemon=True)
                thread = self.thread
            thread.start()
        if not self.ready.wait(CALLS_READY_TIMEOUT):
            raise RuntimeError(f"MTProto/PyTgCalls did not become ready within {CALLS_READY_TIMEOUT}s")
        self._raise_if_failed()
        if not self._runtime_alive():
            raise RuntimeError("Voice runtime stopped after becoming ready")

    def _ensure_ready(self) -> None:
        if self._runtime_alive():
            return
        self.start()
        if not self._runtime_alive():
            self._raise_if_failed()
            raise RuntimeError("Voice runtime is not ready (event loop is closed or stopped)")

    def _raise_if_failed(self) -> None:
        if self.error is not None:
            raise RuntimeError(f"MTProto/PyTgCalls startup failed: {self.error}") from self.error

    async def _maybe_await(self, value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

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
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.assistant = Client(
                "assistant",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=SESSION_STRING,
                in_memory=True,
            )
            self.calls = PyTgCalls(self.assistant)
            self._register_stream_end()
            result = self.calls.start()
            self.loop.run_until_complete(self._maybe_await(result))
            if not self.assistant.is_connected:
                raise RuntimeError("Pyrogram assistant is not connected")
            self.ready.set()
            self.loop.run_forever()
        except BaseException as exc:
            self.error = exc
            self.ready.set()
            log.exception("Voice runtime failed")
        finally:
            # Shutdown must happen on the worker's own loop, and cleanup must
            # tolerate a loop that has already stopped after an exception.
            loop = self.loop
            try:
                if self.calls is not None and loop is not None and not loop.is_closed():
                    result = self.calls.stop()
                    if inspect.isawaitable(result):
                        loop.run_until_complete(result)
            except BaseException:
                log.exception("PyTgCalls stop failed")
            try:
                if (self.assistant is not None and self.assistant.is_connected
                        and loop is not None and not loop.is_closed()):
                    result = self.assistant.stop()
                    if inspect.isawaitable(result):
                        loop.run_until_complete(result)
            except BaseException:
                log.exception("Pyrogram stop failed")
            finally:
                if loop is not None and not loop.is_closed():
                    try:
                        loop.close()
                    except BaseException:
                        log.exception("Event loop close failed")
                asyncio.set_event_loop(None)
                with self._state_lock:
                    # Do not leave a closed loop marked as usable.
                    self.loop = None
                    self.calls = None
                    self.assistant = None
                    self.ready.clear()

    def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Execute a PyTgCalls method on its owning event-loop thread."""
        self._ensure_ready()
        loop = self.loop
        calls = self.calls
        thread = self.thread
        if (loop is None or loop.is_closed() or not loop.is_running()
                or calls is None or not self.ready.is_set()
                or thread is None or not thread.is_alive()):
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

        try:
            loop.call_soon_threadsafe(runner)
        except RuntimeError as exc:
            if "closed" in str(exc).lower():
                raise RuntimeError("Voice runtime event loop is closed") from exc
            raise
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
        fn = getattr(self.calls, method)
        return fn(*args, **kwargs)

    async def acall(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Await a PyTgCalls method when already inside the calls event loop."""
        if self.calls is None:
            raise RuntimeError("PyTgCalls is unavailable")
        value = self._direct_call(method, *args, **kwargs)
        if inspect.isawaitable(value):
            return await value
        return value

    async def aplay(self, chat_id: int, stream: Any) -> Any:
        return await self.acall("play", int(chat_id), stream)

    def play(self, chat_id: int, stream: Any) -> Any:
        return self.call("play", int(chat_id), stream)

    def pause(self, chat_id: int) -> Any:
        return self.call("pause", int(chat_id))

    def resume(self, chat_id: int) -> Any:
        return self.call("resume", int(chat_id))

    def leave(self, chat_id: int) -> Any:
        return self.call("leave_call", int(chat_id))

    def volume(self, chat_id: int, value: int) -> Any:
        return self.call("change_volume_call", int(chat_id), int(value))

    def stop(self) -> None:
        self._stop.set()
        loop = self.loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if self.thread and self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=15)
