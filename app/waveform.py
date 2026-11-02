"""Small waveform display artifacts, independent of classification and take cutting."""

import json
from pathlib import Path

import numpy as np

from keeper.audio_io import load_audio


def waveform_peaks(audio: np.ndarray) -> list[float]:
    """Return exactly 800 bounded absolute-amplitude peaks covering the entire memo."""
    edges = np.linspace(0, len(audio), 801, dtype=int)
    return [float(np.clip(np.max(np.abs(audio[start:end])), 0, 1)) if end > start else 0.0
            for start, end in zip(edges, edges[1:])]


def write_peaks(source: Path, directory: Path) -> None:
    """Return None after saving a display-only waveform without running any classifier."""
    audio = load_audio(source, 16000)
    data = {"duration_s": len(audio) / 16000, "peaks": waveform_peaks(audio)}
    temporary = directory / "peaks.tmp"
    temporary.write_text(json.dumps(data) + "\n", encoding="utf-8")
    temporary.replace(directory / "peaks.json")
