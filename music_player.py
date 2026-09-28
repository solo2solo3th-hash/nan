"""Pure per-chat queue/state engine for Telegram voice-chat playback."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from time import monotonic
from typing import Callable

from downloader import cleanup_job


@dataclass(slots=True)
class Track:
    title: str
    duration: int
    path: str
    job: str
    requester_id: int
    source_url: str = ""


class MusicPlayer:
    """Queue state only; the actual voice call is handled by calls.py."""
    def __init__(self, max_queue_size: int = 20) -> None:
        self.max_queue_size = max(1, int(max_queue_size))
        self._lock = RLock()
        self._queues: dict[int, list[Track]] = {}
        self._current: dict[int, Track] = {}
        self._position: dict[int, float] = {}
        self._started_at: dict[int, float | None] = {}
        self._paused: dict[int, bool] = {}
        # Timestamp of an intentional stream replacement (skip/seek).
        # Used to ignore a stale StreamEnded event from the old stream.
        self._replacement_at: dict[int, float] = {}
        # Current files must not be deleted while PyTgCalls is still reading them.
        # Skip/seek replacements defer cleanup until the replacement stream starts.
        self._deferred_cleanup: dict[int, list[str]] = {}

    def current(self, chat_id: int) -> Track | None:
        with self._lock:
            return self._current.get(int(chat_id))

    def queue(self, chat_id: int) -> list[Track]:
        with self._lock:
            return list(self._queues.get(int(chat_id), []))

    def status(self, chat_id: int) -> tuple[Track | None, list[Track]]:
        return self.current(chat_id), self.queue(chat_id)

    def add(self, chat_id: int, track: Track) -> int:
        chat_id = int(chat_id)
        if not Path(track.path).is_file():
            raise FileNotFoundError(track.path)
        with self._lock:
            queue = self._queues.setdefault(chat_id, [])
            if len(queue) >= self.max_queue_size:
                raise RuntimeError("QUEUE_FULL")
            queue.append(track)
            return len(queue)

    def take_next(self, chat_id: int) -> Track | None:
        chat_id = int(chat_id)
        with self._lock:
            old = self._current.pop(chat_id, None)
            queue = self._queues.setdefault(chat_id, [])
            if old:
                cleanup_job(old.job)
            self._position.pop(chat_id, None)
            self._started_at.pop(chat_id, None)
            self._paused.pop(chat_id, None)
            self._replacement_at.pop(chat_id, None)
            while queue:
                track = queue.pop(0)
                if Path(track.path).is_file():
                    self._current[chat_id] = track
                    self._position[chat_id] = 0.0
                    self._started_at[chat_id] = monotonic()
                    self._paused[chat_id] = False
                    return track
                cleanup_job(track.job)
            self._queues.pop(chat_id, None)
            return None

    def finish_and_take_next(self, chat_id: int) -> Track | None:
        return self.take_next(chat_id)

    def skip_and_take_next(self, chat_id: int) -> Track | None:
        """Advance without deleting the old file until the new stream starts."""
        chat_id = int(chat_id)
        with self._lock:
            old = self._current.pop(chat_id, None)
            queue = self._queues.setdefault(chat_id, [])
            self._position.pop(chat_id, None)
            self._started_at.pop(chat_id, None)
            self._paused.pop(chat_id, None)
            self._replacement_at.pop(chat_id, None)
            while queue:
                track = queue.pop(0)
                if Path(track.path).is_file():
                    if old:
                        self._deferred_cleanup.setdefault(chat_id, []).append(old.job)
                    self._current[chat_id] = track
                    self._position[chat_id] = 0.0
                    self._started_at[chat_id] = monotonic()
                    self._paused[chat_id] = False
                    return track
                cleanup_job(track.job)
            if old:
                self._deferred_cleanup.setdefault(chat_id, []).append(old.job)
            self._queues.pop(chat_id, None)
            return None

    def finalize_replacement(self, chat_id: int) -> None:
        chat_id = int(chat_id)
        with self._lock:
            jobs = self._deferred_cleanup.pop(chat_id, [])
        for job in jobs:
            cleanup_job(job)

    def stop(self, chat_id: int) -> None:
        chat_id = int(chat_id)
        with self._lock:
            current = self._current.pop(chat_id, None)
            queue = self._queues.pop(chat_id, [])
            self._position.pop(chat_id, None)
            self._started_at.pop(chat_id, None)
            self._paused.pop(chat_id, None)
            self._replacement_at.pop(chat_id, None)
            deferred = self._deferred_cleanup.pop(chat_id, [])
        if current:
            cleanup_job(current.job)
        for track in queue:
            cleanup_job(track.job)
        for job in deferred:
            cleanup_job(job)

    def clear_queue(self, chat_id: int) -> None:
        chat_id = int(chat_id)
        with self._lock:
            queue = self._queues.pop(chat_id, [])
        for track in queue:
            cleanup_job(track.job)

    def has_current(self, chat_id: int) -> bool:
        return self.current(chat_id) is not None

    def enqueue_or_claim(self, chat_id: int, track: Track) -> tuple[bool, int, Track | None]:
        """Atomically enqueue a track and claim it as current when idle.

        Returns (started, position, claimed_track). ``claimed_track`` is only
        populated when this call should be sent to PyTgCalls immediately.
        """
        chat_id = int(chat_id)
        if not Path(track.path).is_file():
            raise FileNotFoundError(track.path)
        with self._lock:
            current = self._current.get(chat_id)
            queue = self._queues.setdefault(chat_id, [])
            if current is None and not queue:
                self._current[chat_id] = track
                self._position[chat_id] = 0.0
                self._started_at[chat_id] = monotonic()
                self._paused[chat_id] = False
                return True, 0, track
            if len(queue) >= self.max_queue_size:
                raise RuntimeError("QUEUE_FULL")
            queue.append(track)
            return False, len(queue), None

    def rollback_claim(self, chat_id: int, track: Track) -> None:
        chat_id = int(chat_id)
        with self._lock:
            current = self._current.get(chat_id)
            if current is track:
                self._current.pop(chat_id, None)
                self._position.pop(chat_id, None)
                self._started_at.pop(chat_id, None)
                self._paused.pop(chat_id, None)
                self._replacement_at.pop(chat_id, None)
        cleanup_job(track.job)

    def mark_stream_replaced(self, chat_id: int) -> None:
        """Mark an intentional PyTgCalls stream replacement.

        PyTgCalls may emit StreamEnded for the old stream after a skip/seek
        replacement. The callback can use this short marker to avoid treating
        that stale event as the end of the newly selected track.
        """
        with self._lock:
            self._replacement_at[int(chat_id)] = monotonic()

    def consume_stale_stream_end(self, chat_id: int, grace_seconds: float = 1.5) -> bool:
        """Return True only when a recent replacement makes this end event stale.

        A genuine end near the track duration is never suppressed by this
        helper. The marker is consumed only for a clearly early end event.
        """
        chat_id = int(chat_id)
        with self._lock:
            replaced = self._replacement_at.get(chat_id)
            track = self._current.get(chat_id)
            if replaced is None or track is None:
                return False
            if monotonic() - replaced > float(grace_seconds):
                self._replacement_at.pop(chat_id, None)
                return False
            position = float(self._position.get(chat_id, 0.0))
            started = self._started_at.get(chat_id)
            if started is not None and not self._paused.get(chat_id, False):
                position += monotonic() - started
            duration = max(0, int(track.duration))
            if duration <= 0 or position >= max(0, duration - 3):
                self._replacement_at.pop(chat_id, None)
                return False
            self._replacement_at.pop(chat_id, None)
            return True

    def playback_position(self, chat_id: int) -> int:
        with self._lock:
            track = self._current.get(int(chat_id))
            if track is None:
                return 0
            position = self._position.get(int(chat_id), 0.0)
            started = self._started_at.get(int(chat_id))
            if started is not None and not self._paused.get(int(chat_id), False):
                position += monotonic() - started
            return max(0, min(int(position), max(0, int(track.duration))))

    def is_paused(self, chat_id: int) -> bool:
        with self._lock:
            return bool(self._paused.get(int(chat_id), False))

    def set_paused(self, chat_id: int, paused: bool) -> int:
        chat_id = int(chat_id)
        with self._lock:
            current = self._current.get(chat_id)
            if current is None:
                return 0
            position = float(self._position.get(chat_id, 0.0))
            started = self._started_at.get(chat_id)
            if started is not None and not self._paused.get(chat_id, False):
                position += monotonic() - started
            position = max(0.0, min(position, float(max(0, current.duration))))
            self._position[chat_id] = position
            self._paused[chat_id] = bool(paused)
            self._started_at[chat_id] = None if paused else monotonic()
            return int(position)

    def set_position(self, chat_id: int, position: int | float, paused: bool | None = None) -> int:
        """Set logical playback position after an external voice operation.

        This is used to roll state back when a seek/restart fails, so the
        player state never claims a position that PyTgCalls did not reach.
        """
        chat_id = int(chat_id)
        with self._lock:
            current = self._current.get(chat_id)
            if current is None:
                return 0
            target = max(0.0, min(float(position), float(max(0, int(current.duration)))))
            self._position[chat_id] = target
            if paused is not None:
                self._paused[chat_id] = bool(paused)
            self._started_at[chat_id] = None if self._paused.get(chat_id, False) else monotonic()
            return int(target)

    def seek(self, chat_id: int, delta: int) -> tuple[Track, int, bool] | None:
        chat_id = int(chat_id)
        with self._lock:
            current = self._current.get(chat_id)
            if current is None:
                return None
            position = float(self._position.get(chat_id, 0.0))
            started = self._started_at.get(chat_id)
            was_paused = self._paused.get(chat_id, False)
            if started is not None and not was_paused:
                position += monotonic() - started
            target = max(0, min(int(position) + int(delta), max(0, int(current.duration))))
            self._position[chat_id] = float(target)
            self._started_at[chat_id] = None if was_paused else monotonic()
            return current, target, was_paused


# Backward-compatible API from the original source.
class PlayerService(MusicPlayer):
    def __init__(self, calls, max_queue_size: int = 20) -> None:
        super().__init__(max_queue_size)
        self.calls = calls

    def start(self):
        return self.calls.start()

    def play(self, chat_id: int, stream):
        return self.calls.play(chat_id, stream)

    def stop(self, chat_id: int):
        result = None
        try:
            result = self.calls.leave(chat_id)
        finally:
            super().stop(chat_id)
        return result

    def start_next(self, chat_id: int) -> Track | None:
        track = self.take_next(chat_id)
        if track is None:
            try:
                self.calls.leave(chat_id)
            except Exception:
                pass
            return None
        try:
            self.calls.play(chat_id, track.path)
            return track
        except Exception:
            super().stop(chat_id)
            raise

    def add_and_play_if_idle(self, chat_id: int, track: Track) -> tuple[bool, int | None]:
        idle = not self.has_current(chat_id)
        position = self.add(chat_id, track)
        if idle:
            started = self.start_next(chat_id)
            return True, None if started is None else 0
        return False, position

    def skip(self, chat_id: int) -> Track | None:
        try:
            self.calls.leave(chat_id)
        except Exception:
            pass
        return self.start_next(chat_id)

    def on_finished(self, chat_id: int) -> Track | None:
        return self.start_next(chat_id)
