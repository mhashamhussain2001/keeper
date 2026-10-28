"""Audio loading and non-overlapping frame energy measurements."""

from math import ceil
from pathlib import Path

import numpy as np

from keeper.config import DB_AMPLITUDE_FACTOR, RMS_EPSILON, KeeperConfig


def load_audio(path: str | Path, sr: int) -> np.ndarray:
    """Return mono float32 audio resampled to the requested 16000 Hz rate."""
    if sr != KeeperConfig().sample_rate:
        raise ValueError("Keeper audio must use 16000 Hz")
    import librosa

    audio, _ = librosa.load(str(path), sr=sr, mono=True, dtype=np.float32)
    return np.ascontiguousarray(audio, dtype=np.float32)


def rms_db_per_frame(y: np.ndarray, sr: int, hop_s: float) -> np.ndarray:
    """Return dBFS RMS values for consecutive frames, including a partial final frame."""
    if y.ndim != 1 or y.dtype != np.float32:
        raise ValueError("Audio must be a mono float32 array")
    if sr != KeeperConfig().sample_rate or not np.isfinite(hop_s) or hop_s * sr < 1:
        raise ValueError("Expected 16000 Hz and a positive hop spanning at least one sample")
    frame_count = ceil(len(y) / (hop_s * sr))
    result = np.empty(frame_count, dtype=np.float64)
    for index in range(frame_count):
        frame = y[int(index * hop_s * sr):int((index + 1) * hop_s * sr)]
        rms = np.sqrt(np.mean(np.square(frame, dtype=np.float64)))
        result[index] = DB_AMPLITUDE_FACTOR * np.log10(rms + RMS_EPSILON)
    return result
