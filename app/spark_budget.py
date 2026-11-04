"""Bound web speech-command costs without changing CLI parsing or audio thresholds."""

from math import ceil
from pathlib import Path
from typing import Any

import numpy as np

from keeper.labels import MuseSparkLabelParser
from keeper.types import CommandLabel, Region


def web_spark_budget(duration_s: float) -> int:
    """Return forty calls through ten minutes, then four per extra minute up to 120."""
    return min(120, 40 + 4 * ceil(max(0.0, duration_s - 600.0) / 60.0))


class WebLabelParser(MuseSparkLabelParser):
    """Read a chronological prefix and retain a completed memo when its speech budget runs out."""

    def __init__(self, cache_path: Path, duration_s: float, **options: Any) -> None:
        """Return None after preparing a web-only cap and skipped-region accounting."""
        super().__init__(cache_path, max_spark_calls=web_spark_budget(duration_s), **options)
        self.allowed: set[tuple[float, float]] = set()
        self.skipped_regions: set[tuple[float, float]] = set()

    def preflight(self, clips: list[tuple[np.ndarray, int, Region]]) -> None:
        """Return None after selecting the earliest speech regions without rejecting the whole memo."""
        ordered = sorted(clips, key=lambda clip: (clip[2].start, clip[2].end))
        self.allowed = {(region.start, region.end) for _, _, region in ordered[:self.max_spark_calls]}
        self.skipped_regions = {(region.start, region.end) for _, _, region in ordered[self.max_spark_calls:]}

    def parse(self, audio: np.ndarray, sr: int, region: Region) -> CommandLabel:
        """Return the existing parser result or a non-command when the web cost cap excludes this region."""
        identity = (region.start, region.end)
        if identity in self.allowed:
            try:
                return super().parse(audio, sr, region)
            except RuntimeError as exc:
                if not str(exc).startswith("Spark cost guard:"):
                    raise
        with self.lock:
            self.skipped_regions.add(identity)
        return CommandLabel(False, "unsorted", None, None, 0.0, intent="none")
