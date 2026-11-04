"""Inspect original recordings and open native-rate audio without pipeline resampling."""

import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

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


@contextmanager
def open_original(source: Path) -> Iterator[sf.SoundFile]:
    """Yield seekable native-rate audio, using existing ffmpeg for formats libsndfile cannot read."""
    try:
        audio = sf.SoundFile(source)
    except sf.LibsndfileError:
        with TemporaryDirectory(prefix="keeper-native-") as temporary:
            decoded = Path(temporary) / "original.wav"
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-i", str(source), "-map", "0:a:0",
                 "-vn", "-c:a", "pcm_f32le", str(decoded)],
                capture_output=True, check=True, timeout=120,
            )
            with sf.SoundFile(decoded) as audio:
                yield audio
    else:
        with audio:
            yield audio
