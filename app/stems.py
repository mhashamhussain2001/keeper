"""Build bounded native-quality stems archives independently of preview generation."""

import io
import re
from tempfile import SpooledTemporaryFile
from typing import Any, BinaryIO
from zipfile import ZIP_STORED, ZipFile

import numpy as np
import soundfile as sf


MAX_STEMS_BYTES = 500 * 1024 * 1024
FADE_SECONDS = 0.005


def safe_filename(value: str) -> str:
    """Return a portable filename component without paths, control characters, or reserved names."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", value).strip(" .")[:150].rstrip(" .") or "Untitled"
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        name = "_" + name
    return name


def ordered_takes(songmap: dict[str, Any], names: dict[str, str], canvas: dict[str, Any],
                  include_rejected: bool) -> list[dict[str, Any]]:
    """Return unique named takes in canvas time/lane order, then unplaced takes in source order."""
    takes = []
    for section, rows in songmap["sections"].items():
        for index, row in enumerate(rows, 1):
            if section == "rejected" and not include_rejected:
                continue
            label = "Idea" if section == "unsorted" else "Take" if section == "unlabeled" else section[:1].upper() + section[1:]
            takes.append({**row, "section": section, "name": names.get(row["id"], row.get("custom_name") or f"{label} {index}")})
    placements: dict[str, tuple[float, int, int]] = {}
    for index, clip in sorted(enumerate(canvas.get("clips", [])), key=lambda pair: (pair[1]["start"], pair[1]["lane"], pair[0])):
        placements.setdefault(clip["take_id"], (clip["start"], clip["lane"], index))
    return sorted(takes, key=lambda take: (0, *placements[take["id"]]) if take["id"] in placements else (1, take["start"], take["end"], take["id"]))


def build_stems(audio: sf.SoundFile, takes: list[dict[str, Any]]) -> BinaryIO:
    """Return a rewound temporary ZIP of faded PCM16 stems, rejecting totals above 500 MiB."""
    entries = []
    lines = [f"Keeper song canvas — {audio.samplerate} Hz, {audio.channels} channel(s)", ""]
    for index, take in enumerate(takes, 1):
        begin, end = round(take["start"] * audio.samplerate), round(take["end"] * audio.samplerate)
        if not 0 <= begin < end <= len(audio):
            raise ValueError("Take boundaries lie outside the original recording")
        filename = f"{index:02d} {safe_filename(take['section'])} {safe_filename(take['name'])}.wav"
        if take["section"] == "rejected":
            filename = "rejected/" + filename
        entries.append((filename, begin, end))
        lines.extend([filename, f"Name: {take['name']}", f"Section: {take['section']}",
                      f"Original: {float(take['start']):.6f} – {float(take['end']):.6f} seconds"])
        if take.get("heard"):
            lines.append(f"Heard: {take['heard']}")
        lines.append("")
    songmap = "\n".join(lines).encode("utf-8")
    # PCM payload plus conservative WAV and ZIP header/name overhead, before any cuts/allocation.
    estimated = len(songmap) + 1024 + sum((end - begin) * audio.channels * 2 + 1024 + len(name.encode("utf-8")) * 2 for name, begin, end in entries)
    if estimated > MAX_STEMS_BYTES:
        raise OverflowError("Stems export exceeds 500 MB; export fewer or shorter takes")
    archive = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    try:
        with ZipFile(archive, "w", compression=ZIP_STORED) as zipped:
            for filename, begin, end in entries:
                # One take in memory at a time; the complete archive spills to disk.
                audio.seek(begin)
                samples = audio.read(end - begin, dtype="float32", always_2d=True)
                fade = min(round(FADE_SECONDS * audio.samplerate), len(samples))
                if fade:
                    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)[:, None]
                    samples[:fade] *= ramp
                    samples[-fade:] *= ramp[::-1]
                wav = io.BytesIO()
                sf.write(wav, samples, audio.samplerate, format="WAV", subtype="PCM_16")
                zipped.writestr(filename, wav.getvalue())
            zipped.writestr("song_canvas.txt", songmap)
        if archive.tell() > MAX_STEMS_BYTES:
            raise OverflowError("Stems export exceeds 500 MB")
        archive.seek(0)
        return archive
    except BaseException:
        archive.close()
        raise
