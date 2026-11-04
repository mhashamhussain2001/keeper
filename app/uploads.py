"""Bounded upload copying and metadata-only audio validation."""

from pathlib import Path
from typing import BinaryIO
import json
import math
import shutil
import subprocess
import warnings

from app.policy import Policy, policy


AUDIO_EXTENSIONS = {".m4a", ".mp4", ".aac", ".caf", ".mp3", ".wav", ".aif", ".aiff", ".flac", ".ogg", ".opus", ".webm"}


def save_upload(stream: BinaryIO, destination: Path, limits: Policy | None = None) -> None:
    """Return None after copying a bounded upload, removing any incomplete file on failure."""
    limits = limits or policy()
    created = False
    try:
        total = 0
        with destination.open("xb") as target:
            created = True
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > limits.max_upload_bytes:
                    raise ValueError(limits.limits_message)
                target.write(chunk)
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise


def validate_audio(source: Path, limits: Policy | None = None) -> float:
    """Return finite audio duration from container metadata, removing invalid or over-limit uploads."""
    limits = limits or policy()
    try:
        if not 0 < source.stat().st_size <= limits.max_upload_bytes:
            raise ValueError(limits.limits_message)
        probe = shutil.which("ffprobe")
        if probe:
            result = subprocess.run([probe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                                     "stream=duration,sample_rate,channels:format=duration", "-of", "json", str(source)],
                                    capture_output=True, check=True, timeout=30)
            metadata = json.loads(result.stdout)
            stream = metadata["streams"][0]
            if int(stream["sample_rate"]) <= 0 or int(stream["channels"]) <= 0:
                raise ValueError("Invalid audio stream")
            durations = [float(value) for value in [stream.get("duration"), metadata.get("format", {}).get("duration")]
                         if value not in (None, "N/A")]
            duration = max(durations)
        else:
            import librosa

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                duration = float(librosa.get_duration(path=source))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Invalid audio duration")
        if duration > limits.max_audio_minutes * 60:
            raise OverflowError(limits.limits_message)
        return float(duration)
    except Exception as exc:
        source.unlink(missing_ok=True)
        if isinstance(exc, OverflowError) or (isinstance(exc, ValueError) and str(exc) == limits.limits_message):
            raise ValueError(limits.limits_message) from None
        raise ValueError("Cannot read this audio file; upload valid audio. " + limits.limits_message) from None
