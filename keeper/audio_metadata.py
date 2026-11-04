"""Inspect native recording metadata without decoding or changing working audio."""

import json
import subprocess
from pathlib import Path

import soundfile as sf


def audio_format(source: Path) -> tuple[int, int]:
    """Return the original sample rate and channel count, including ffmpeg-supported uploads."""
    try:
        info = sf.info(source)
        return info.samplerate, info.channels
    except sf.LibsndfileError:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=sample_rate,channels", "-of", "json", str(source)],
            capture_output=True, check=True, timeout=30,
        )
        stream = json.loads(result.stdout)["streams"][0]
        return int(stream["sample_rate"]), int(stream["channels"])
