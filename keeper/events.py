"""Timestamped observations of pipeline work, independent of processing decisions."""

from collections.abc import Callable
from threading import RLock
from time import perf_counter
from typing import Any


EventCallback = Callable[[dict[str, Any]], None]


class EventReporter:
    """Serialize callbacks so concurrent completions retain monotonic event times."""

    def __init__(self, callback: EventCallback | None = None) -> None:
        """Return None after initializing an optional observation sink and clock."""
        self.callback = callback
        self.started = perf_counter()
        self.lock = RLock()

    def emit(self, event_type: str, **data: Any) -> None:
        """Return None after delivering one timestamped observation, when a sink exists."""
        if self.callback is not None:
            with self.lock:
                self.callback({"t": perf_counter() - self.started, "type": event_type, "data": data})
