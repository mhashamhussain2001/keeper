"""Edge refinement snaps coarse take edges to the real onset and offset of sound."""

import numpy as np

from keeper.config import KeeperConfig
from keeper.refine import refine_edges
from keeper.types import CommandLabel, Take

SR = 16000
CFG = KeeperConfig(pre_roll_s=0.0, post_roll_s=0.0)


def _audio(on: float, off: float, total: float = 20.0) -> np.ndarray:
    """Return silence with one loud tone from on to off seconds."""
    y = np.zeros(int(total * SR), dtype=np.float32)
    t = np.arange(int((off - on) * SR)) / SR
    y[int(on * SR):int(on * SR) + len(t)] = (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    return y


def _take(start: float, end: float) -> list[tuple[Take, CommandLabel]]:
    """Return one take at the given coarse edges."""
    return [(Take("t", "unlabeled", "Take 1", start, end, "m.wav", None, None), CommandLabel(False, "unlabeled", "Take 1", None, 0.0))]


def test_quiet_coarse_edges_move_inward_to_sound() -> None:
    """Return None after checking edges placed in silence snap to within 50 ms of the tone."""
    take = refine_edges(_take(5.0, 12.5), _audio(5.37, 12.04), -45.0, CFG)[0][0]
    assert abs(take.start - 5.37) < 0.05 and abs(take.end - 12.04) < 0.05


def test_loud_coarse_edges_move_outward_to_sound() -> None:
    """Return None after checking edges placed inside the sound walk out to the real onset and offset."""
    take = refine_edges(_take(5.8, 11.7), _audio(5.37, 12.04), -45.0, CFG)[0][0]
    assert abs(take.start - 5.37) < 0.05 and abs(take.end - 12.04) < 0.05


def test_short_dip_inside_sound_does_not_end_it() -> None:
    """Return None after checking a 60 ms gap between notes is not treated as the end of the take."""
    y = _audio(5.0, 12.0)
    y[int(8.0 * SR):int(8.06 * SR)] = 0
    take = refine_edges(_take(5.2, 11.6), y, -45.0, CFG)[0][0]
    assert abs(take.start - 5.0) < 0.05 and abs(take.end - 12.0) < 0.05


def test_disabled_flag_and_empty_audio_keep_takes() -> None:
    """Return None after checking the off flag and missing audio leave takes untouched."""
    takes = _take(5.0, 12.5)
    assert refine_edges(takes, _audio(5.37, 12.04), -45.0, KeeperConfig(refine_edges=False)) == takes
    assert refine_edges(takes, np.empty(0, dtype=np.float32), -45.0, CFG) == takes


def test_padding_never_runs_into_speech() -> None:
    """Return None after checking a take ending where speech begins keeps that edge."""
    y = _audio(5.0, 14.0)
    take = refine_edges(_take(5.0, 12.0), y, -45.0, KeeperConfig(), [(12.0, 13.0)])[0][0]
    assert take.end == 12.0
