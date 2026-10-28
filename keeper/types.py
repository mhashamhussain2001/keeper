"""Serializable records passed between pipeline stages."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal


FrameLabel = Literal["singing", "speech", "instrument", "silence"]
Section = Literal["intro", "verse", "pre-chorus", "chorus", "bridge", "hook", "outro", "unsorted"]


@dataclass
class Region:
    """A continuous interval with one activity label."""

    start: float
    end: float
    kind: FrameLabel
    mean_scores: dict[str, float]

    def to_dict(self) -> dict:
        """Return a JSON-serializable region dictionary."""
        return asdict(self)


@dataclass
class CommandLabel:
    """The interpretation of a spoken region."""

    is_command: bool
    section: Section
    variant: str | None
    note: str | None
    confidence: float

    def to_dict(self) -> dict:
        """Return a JSON-serializable command dictionary."""
        return asdict(self)


@dataclass
class Take:
    """A non-destructive reference to a performance in the source memo."""

    id: str
    section: str
    variant: str
    start: float
    end: float
    source_file: str
    note: str | None
    clip_path: str | None

    def to_dict(self) -> dict:
        """Return a JSON-serializable take dictionary."""
        return asdict(self)


@dataclass
class SongMap:
    """Takes organized by song section."""

    source_file: str
    sections: dict[str, list[Take]]

    def to_dict(self) -> dict:
        """Return a JSON-serializable song map dictionary."""
        return asdict(self)

    def to_json(self, path: str | Path) -> None:
        """Return None after writing this song map as UTF-8 JSON."""
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, allow_nan=False), encoding="utf-8")

    @classmethod
    def from_json(cls, path: str | Path) -> SongMap:
        """Return a song map reconstructed from a UTF-8 JSON file."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(source_file=data["source_file"], sections={
            section: [Take(**take) for take in takes]
            for section, takes in data["sections"].items()
        })
