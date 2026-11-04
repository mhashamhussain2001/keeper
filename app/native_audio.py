"""Inspect original recordings and open native-rate audio without pipeline resampling."""

import subprocess
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

import soundfile as sf
from keeper.audio_metadata import audio_format


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
