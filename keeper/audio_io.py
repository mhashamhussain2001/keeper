"""Audio loading and non-overlapping frame energy measurements."""

from math import ceil
from pathlib import Path
import subprocess
import io
from time import perf_counter

import numpy as np

from keeper.config import DB_AMPLITUDE_FACTOR, RMS_EPSILON, KeeperConfig


def warm_librosa() -> float:
    """Return seconds spent importing and priming the unchanged librosa loader with in-memory silence."""
    started = perf_counter()
    import librosa
    import soundfile as sf

    stream = io.BytesIO()
    sf.write(stream, np.zeros(4410, dtype=np.float32), 44100, format="WAV", subtype="PCM_16")
    stream.seek(0)
    librosa.load(stream, sr=16000, mono=True, dtype=np.float32)
    return perf_counter() - started


def load_audio(path: str | Path, sr: int, use_ffmpeg: bool | None = None,
               max_duration_s: float | None = None) -> np.ndarray:
    """Return mono float32 audio resampled to the requested 16000 Hz rate."""
    if sr != KeeperConfig().sample_rate:
        raise ValueError("Keeper audio must use 16000 Hz")
    fast = KeeperConfig().fast_audio_loading if use_ffmpeg is None else use_ffmpeg
    if max_duration_s is not None and (not np.isfinite(max_duration_s) or max_duration_s <= 0):
        raise ValueError("max_duration_s must be positive and finite")
    if fast:
        command = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr)]
        if max_duration_s is not None:
            command += ["-t", str(max_duration_s)]
        command += ["-f", "f32le", "pipe:1"]
        result = subprocess.run(command, capture_output=True, check=True, timeout=120)
        return np.frombuffer(result.stdout, dtype="<f4").astype(np.float32, copy=True)
    import librosa

    options = {} if max_duration_s is None else {"duration": max_duration_s}
    audio, _ = librosa.load(str(path), sr=sr, mono=True, dtype=np.float32, **options)
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
