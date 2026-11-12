"""Snap take edges to the real start and end of sound using fine-grained loudness."""

from dataclasses import replace

import numpy as np

from keeper.audio_io import rms_db_per_frame
from keeper.config import KeeperConfig
from keeper.types import CommandLabel, Take

FINE_HOP_S = 0.02
QUIET_RUN_S = 0.15  # A dip shorter than this is a gap between notes, not the end of the sound.


def _first_sound(db: np.ndarray, gate_db: float) -> int | None:
    """Return the index of the first frame at or above the gate, or None."""
    loud = np.flatnonzero(db >= gate_db)
    return int(loud[0]) if len(loud) else None


def _edge_before(db: np.ndarray, gate_db: float, quiet_frames: int) -> int | None:
    """Return the index of the first loud frame after the last quiet run, or None if sound never starts quiet."""
    run = 0
    for index in range(len(db) - 1, -1, -1):
        run = run + 1 if db[index] < gate_db else 0
        if run >= quiet_frames:
            return index + run
    return None


def refine_edges(
    takes: list[tuple[Take, CommandLabel]], y: np.ndarray, gate_db: float, cfg: KeeperConfig,
    speech: list[tuple[float, float]] = (),
) -> list[tuple[Take, CommandLabel]]:
    """Return takes whose edges sit on the nearest real sound onset and offset, padded by the usual rolls but never into speech."""
    if not cfg.refine_edges or len(y) == 0:
        return list(takes)
    sr, duration = cfg.sample_rate, len(y) / cfg.sample_rate
    quiet_frames = round(QUIET_RUN_S / FINE_HOP_S)

    def db_between(start: float, end: float) -> tuple[np.ndarray, float]:
        """Return fine-frame dB values for [start, end) and the clamped start time they begin at."""
        start = max(0.0, start)
        end = min(duration, end)
        return rms_db_per_frame(np.ascontiguousarray(y[round(start * sr):round(end * sr)]), sr, FINE_HOP_S), start

    refined: list[tuple[Take, CommandLabel]] = []
    for take, label in sorted(takes, key=lambda pair: pair[0].start):
        search = cfg.refine_search_s
        start, end = take.start, take.end
        # Start: if the coarse start is quiet, move forward to the first sound; if it is already loud, walk back to the onset.
        db, origin = db_between(start, start + search)
        if len(db) and db[0] < gate_db:
            first = _first_sound(db, gate_db)
            if first is not None:
                start = origin + first * FINE_HOP_S
        else:
            db, origin = db_between(start - search, start)
            edge = _edge_before(db, gate_db, quiet_frames)
            if edge is not None:
                start = origin + edge * FINE_HOP_S
        # End: mirror image, scanning for the last sound.
        db, origin = db_between(end - search, end)
        if len(db) and db[-1] < gate_db:
            loud = np.flatnonzero(db >= gate_db)
            if len(loud):
                end = origin + (int(loud[-1]) + 1) * FINE_HOP_S
        else:
            db, origin = db_between(end, end + search)
            quiet = np.flatnonzero(db < gate_db)
            run_start = next((int(i) for i in quiet if (db[i:i + quiet_frames] < gate_db).all()), None)
            if run_start is not None:
                end = origin + run_start * FINE_HOP_S
        new_start = max(0.0, start - cfg.pre_roll_s)
        new_end = min(duration, end + cfg.post_roll_s)
        # Spoken regions bound a take: padding and snapping must not run into them.
        new_start = max([new_start] + [stop for _, stop in speech if stop <= take.start + 1e-6])
        new_end = min([new_end] + [begin for begin, _ in speech if begin >= take.end - 1e-6])
        if refined:  # Never overlap the previous take.
            new_start = max(new_start, refined[-1][0].end)
        if new_end - new_start > 0:
            refined.append((replace(take, start=float(new_start), end=float(new_end)), label))
        else:
            refined.append((take, label))
    return refined
