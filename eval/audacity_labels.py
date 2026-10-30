"""Reading Audacity's tab-separated interval and point labels."""

from math import isfinite
from pathlib import Path


def read_audacity_labels(path: str | Path) -> list[tuple[float, float, str]]:
    """Return Audacity intervals with legacy music labels mapped and cmd: text preserved."""
    labels: list[tuple[float, float, str]] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        fields = line.split("\t", maxsplit=2)
        if len(fields) != 3:
            raise ValueError(f"Invalid Audacity label at line {line_number}: expected start, end, text")
        start, end = float(fields[0]), float(fields[1])
        if not isfinite(start) or not isfinite(end) or not 0 <= start <= end:
            raise ValueError(f"Invalid Audacity interval at line {line_number}")
        label = "music" if fields[2] in ("singing", "instrument") else fields[2]
        labels.append((start, end, label))
    return labels
