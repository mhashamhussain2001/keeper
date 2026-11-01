"""Bounded upload copying and decoded audio validation."""

from pathlib import Path
from typing import BinaryIO

import numpy as np


MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_AUDIO_SECONDS = 600.0
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aif", ".aiff"}


def save_upload(stream: BinaryIO, destination: Path) -> None:
    """Return None after saving at most 50 MiB, raising ValueError for a larger upload."""
    total = 0
    with destination.open("xb") as target:
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise ValueError("Audio upload must be at most 50 MB (50 MiB)")
            target.write(chunk)


def validate_audio(source: Path) -> float:
    """Return decoded duration after rejecting empty, invalid, or over-ten-minute audio."""
    if source.stat().st_size == 0:
        raise ValueError("Audio file is empty")
    import librosa

    try:
        # Bound decoding even for highly compressed uploads with misleading metadata.
        audio, _ = librosa.load(str(source), sr=16000, mono=True, dtype=np.float32,
                                duration=MAX_AUDIO_SECONDS + 1.0)
    except Exception as exc:
        raise ValueError("Cannot decode this audio file; upload valid audio") from exc
    if not len(audio) or not np.isfinite(audio).all():
        raise ValueError("Audio must contain nonempty, finite samples")
    duration = len(audio) / 16000
    if duration > MAX_AUDIO_SECONDS:
        raise ValueError("Audio must be at most 10 minutes long")
    return duration
