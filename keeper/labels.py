"""Offline command fixtures and a deliberately unimplemented Muse Spark adapter."""

import json
import os
from pathlib import Path
from typing import Protocol

import numpy as np

from keeper.config import MOCK_LABEL_ROUND_S
from keeper.types import CommandLabel, Region


class LabelParser(Protocol):
    """Interface for parsing speech-region audio into structured commands."""

    def parse(self, audio: np.ndarray, sr: int, region: Region) -> CommandLabel:
        """Return the structured command, if any, spoken in this speech-region audio."""
        ...


class MockLabelParser:
    """Resolve deterministic command fixtures by rounded speech start time."""

    def __init__(self, path: str | Path) -> None:
        """Return None after loading the JSON command mapping."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.labels = {float(key): CommandLabel(**value) for key, value in data.items()}

    def parse(self, audio: np.ndarray, sr: int, region: Region) -> CommandLabel:
        """Return the fixture at the nearest half-second or a non-command label."""
        if region.kind != "speech":
            raise ValueError("Only speech regions may be parsed")
        key = round(region.start / MOCK_LABEL_ROUND_S) * MOCK_LABEL_ROUND_S
        return self.labels.get(key, CommandLabel(False, "unsorted", None, None, 0.0))


class MuseSparkLabelParser:
    """Stub for sending speech-region audio and requesting JSON with CommandLabel fields."""

    def __init__(self) -> None:
        """Return None after reading META_API_KEY from the environment without contacting a service."""
        self.api_key = os.environ.get("META_API_KEY", "")

    def parse(self, audio: np.ndarray, sr: int, region: Region) -> CommandLabel:
        """Return no result until speech audio input and JSON command extraction are verified."""
        raise NotImplementedError("Muse Spark audio input not verified yet")
