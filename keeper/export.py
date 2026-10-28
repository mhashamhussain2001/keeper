"""Non-destructive PCM clip export with edge fades."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import soundfile as sf

from keeper.config import MILLISECONDS_PER_SECOND, KeeperConfig
from keeper.types import SongMap, Take


def export_clips(y: np.ndarray, sr: int, songmap: SongMap, out_dir: str | Path, fade_ms: float) -> SongMap:
    """Return a copied song map with paths to faded 16-bit WAV clips written outside the source folder."""
    if y.ndim != 1 or y.dtype != np.float32 or not np.isfinite(y).all():
        raise ValueError("Audio must be a finite mono float32 array")
    if sr != KeeperConfig().sample_rate or not np.isfinite(fade_ms) or fade_ms < 0:
        raise ValueError("Expected 16000 Hz and a nonnegative finite fade duration")
    destination = Path(out_dir).resolve()
    source_paths = {Path(songmap.source_file).resolve()}
    source_paths.update(Path(take.source_file).resolve() for takes in songmap.sections.values() for take in takes)
    if any(destination == source.parent for source in source_paths):
        raise ValueError("Clips must not be written into the source file's folder")
    used_names: set[str] = set()
    for takes in songmap.sections.values():
        for take in takes:
            if not take.id or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in take.id):
                raise ValueError("Take IDs must be nonempty lowercase filename slugs")
            if take.id in used_names:
                raise ValueError("Take IDs must be unique")
            used_names.add(take.id)
            if not (0 <= take.start < take.end <= len(y) / sr):
                raise ValueError("Take boundaries must lie inside the source audio")
            if round(take.end * sr) <= round(take.start * sr):
                raise ValueError("A take must contain at least one audio sample")
    destination.mkdir(parents=True, exist_ok=True)
    sections: dict[str, list[Take]] = {}
    for section, takes in songmap.sections.items():
        sections[section] = []
        for take in takes:
            clip = y[round(take.start * sr):round(take.end * sr)].copy()
            fade_length = min(round(fade_ms * sr / MILLISECONDS_PER_SECOND), len(clip))
            if fade_length:
                ramp = np.linspace(0.0, 1.0, fade_length, dtype=np.float32)
                clip[:fade_length] *= ramp
                clip[-fade_length:] *= ramp[::-1]
            clip_path = destination / f"{take.id}.wav"
            if clip_path in source_paths:
                raise ValueError("A clip cannot overwrite a source file")
            sf.write(clip_path, clip, sr, subtype="PCM_16")
            sections[section].append(replace(take, clip_path=str(clip_path)))
    return SongMap(songmap.source_file, sections)
