"""Bounded, thread-safe status publishing with coalesced mounted-volume writes."""

from collections.abc import Callable
from pathlib import Path
from threading import RLock, Timer
from time import monotonic, sleep
from typing import Any

from app.storage import write_status


class EventStatus:
    """Keep every accepted observation in memory until the next bounded status write."""

    def __init__(self, directory: Path, commit: Callable[[], None] | None = None,
                 **metadata: Any) -> None:
        """Return None after initializing an event buffer and optional one-second publisher."""
        self.directory = directory
        self.commit = commit
        self.metadata = metadata
        self.events: list[dict[str, Any]] = []
        self.lock = RLock()
        self.last_write = float("-inf")
        self.timer: Timer | None = None
        self.state = "running"
        self.stage = "loading audio"
        self.error: str | None = None
        self.closed = False

    def event(self, event: dict[str, Any]) -> None:
        """Return None after accepting an event, reserving a terminal slot at the 400-event cap."""
        with self.lock:
            if self.closed:
                return
            if len(self.events) < 399:
                self.events.append(event)
            elif event["type"] in ("done", "failed"):
                self.events[399:] = [event]
            self._schedule()

    def progress(self, stage: str) -> None:
        """Return None after coalescing a real pipeline stage into the pending publication."""
        with self.lock:
            self.stage = stage
            self._schedule()

    def _schedule(self) -> None:
        """Return None after writing immediately or scheduling one coalesced publication."""
        delay = max(0.0, self.last_write + (1.0 if self.commit else 0.0) - monotonic())
        if not delay:
            self._flush()
        elif self.timer is None:
            self.timer = Timer(delay, self._flush)
            self.timer.daemon = True
            self.timer.start()

    def _flush(self) -> None:
        """Return None after atomically publishing the buffered status under the append lock."""
        with self.lock:
            self.timer = None
            if self.closed:
                return
            write_status(self.directory, self.state, self.stage, self.error,
                         events=list(self.events), **self.metadata)
            if self.commit is not None:
                self.commit()
            self.last_write = monotonic()

    def finish(self, state: str, error: str | None = None) -> None:
        """Return None after synchronously flushing terminal state within the same write budget."""
        with self.lock:
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None
            self.state = self.stage = state
            self.error = error
            delay = max(0.0, self.last_write + (1.0 if self.commit else 0.0) - monotonic())
            if delay:
                sleep(delay)
            self._flush()
            self.closed = True
