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

from pyrogram import Client, enums

# PyTgCalls 2.3.3 is paired with PyrogramMod 2.4.1 in requirements.txt.
# PyrogramMod provides the Pyrogram-compatible ``pyrogram`` module and
# the exception names expected by this PyTgCalls release.
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream

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

    def set_stream_end_handler(self, handler: Callable[[int], Awaitable[None] | None]) -> None:
        self._stream_end_handler = handler

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            if not self.ready.wait(CALLS_READY_TIMEOUT):
                raise RuntimeError("Voice runtime is already starting but did not become ready")
            self._raise_if_failed()
            return
        self.thread = threading.Thread(target=self._worker, name="mtproto-calls", daemon=True)
        self.thread.start()
        if not self.ready.wait(CALLS_READY_TIMEOUT):
            raise RuntimeError(f"MTProto/PyTgCalls did not become ready within {CALLS_READY_TIMEOUT}s")
        self._raise_if_failed()

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
            try:
                if self.calls is not None:
                    result = self.calls.stop()
                    self.loop.run_until_complete(self._maybe_await(result))
            except BaseException:
                log.exception("PyTgCalls stop failed")
            try:
                if self.assistant is not None and self.assistant.is_connected:
                    result = self.assistant.stop()
                    self.loop.run_until_complete(self._maybe_await(result))
            except BaseException:
                log.exception("Pyrogram stop failed")
            if self.loop is not None:
                self.loop.close()
                asyncio.set_event_loop(None)

    def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Execute a PyTgCalls method on its owning event-loop thread."""
        if self.loop is None or self.calls is None or not self.ready.is_set():
            raise RuntimeError("Voice runtime is not ready")
        if threading.current_thread() is self.thread:
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
                value = self._direct_call(method, *args, **kwargs)
                if inspect.isawaitable(value):
                    task = asyncio.ensure_future(value)
                    task.add_done_callback(lambda t: self._transfer_result(t, future))
                else:
                    future.set_result(value)
            except BaseException as exc:
                future.set_exception(exc)

        self.loop.call_soon_threadsafe(runner)
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

    def play(self, chat_id: int, stream: Any, start_seconds: int = 0) -> Any:
        """Start a local/remote stream, optionally from an offset.

        PyTgCalls does not expose a portable seek method in this release.
        Seeking is therefore implemented by restarting the same source with
        FFmpeg's ``-ss`` input offset. This keeps the call on the same
        PyTgCalls event loop and avoids private/internal APIs.
        """
        offset = max(0, int(start_seconds or 0))
        if offset:
            stream = MediaStream(
                stream,
                video_flags=MediaStream.Flags.IGNORE,
                ffmpeg_parameters=f"-ss {offset}",
            )
        return self.call("play", int(chat_id), stream)

    async def _assistant_member_status(self, chat_id: int) -> Any:
        if self.assistant is None:
            raise RuntimeError("Pyrogram assistant is unavailable")
        return await self.assistant.get_chat_member(int(chat_id), "me")

    async def _ensure_assistant_member(self, chat_id: int, invite_link: str) -> bool:
        """Ensure the user-account assistant is a member of this chat.

        The assistant cannot add itself. The Bot API side creates a temporary
        invite link, then this already-authorized user session joins through it.
        Telegram still requires the bot to be an administrator able to invite
        users; this code never attempts to bypass Telegram permissions.
        """
        if self.assistant is None:
            raise RuntimeError("Pyrogram assistant is unavailable")
        try:
            member = await self._assistant_member_status(chat_id)
            if member.status in {
                enums.ChatMemberStatus.MEMBER,
                enums.ChatMemberStatus.ADMINISTRATOR,
                enums.ChatMemberStatus.OWNER,
            }:
                return True
        except Exception:
            pass

        if not invite_link:
            raise RuntimeError("ASSISTANT_INVITE_REQUIRED")

        await self.assistant.join_chat(invite_link)
        member = await self._assistant_member_status(chat_id)
        if member.status not in {
            enums.ChatMemberStatus.MEMBER,
            enums.ChatMemberStatus.ADMINISTRATOR,
            enums.ChatMemberStatus.OWNER,
        }:
            raise RuntimeError("ASSISTANT_JOIN_FAILED")
        return True

    def ensure_assistant_member(self, chat_id: int, invite_link: str) -> bool:
        """Synchronously ensure the assistant has joined the target group."""
        if self.loop is None or self.assistant is None or not self.ready.is_set():
            raise RuntimeError("Voice runtime is not ready")
        if threading.current_thread() is self.thread:
            raise RuntimeError("ensure_assistant_member cannot run on the calls event-loop thread")
        future = asyncio.run_coroutine_threadsafe(
            self._ensure_assistant_member(int(chat_id), invite_link), self.loop
        )
        return bool(future.result(timeout=45))

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
