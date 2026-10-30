"""Relative noise floor gates, fallback, and clamping checks."""

from dataclasses import replace

import numpy as np
import pytest

from keeper.config import KeeperConfig
from keeper.frames import silence_gate_db


def test_relative_gate() -> None:
    """Return None after checking a -60 dB floor and -15 dB median yield -48 dB."""
    assert silence_gate_db(np.array([-60.0] * 20 + [-15.0] * 80), KeeperConfig()) == -48.0


def test_flat_memo_uses_absolute_fallback() -> None:
    """Return None after checking a low dynamic range memo uses the absolute fallback."""
    assert silence_gate_db(np.array([-25.0] * 20 + [-15.0] * 80), KeeperConfig()) == -45.0


@pytest.mark.parametrize(("floor", "expected"), [(-90.0, -60.0), (-40.0, -30.0)])
def test_relative_gate_clamps(floor: float, expected: float) -> None:
    """Return None after checking the relative gate clamps at either configured bound."""
    assert silence_gate_db(np.array([floor] * 20 + [-10.0] * 80), KeeperConfig()) == expected


def test_absolute_mode_and_empty_input() -> None:
    """Return None after checking absolute mode and empty memos use the configured gate."""
    cfg = replace(KeeperConfig(), silence_mode="absolute", silence_db=-42.0)
    assert silence_gate_db(np.array([-60.0] * 20 + [-15.0] * 80), cfg) == -42.0
    assert silence_gate_db(np.array([]), KeeperConfig()) == -45.0


@pytest.mark.parametrize("rms", [np.array([np.nan]), np.array([[0.0]])])
def test_invalid_rms_rejected(rms: np.ndarray) -> None:
    """Return None after checking malformed RMS cannot produce a gate."""
    with pytest.raises(ValueError, match="RMS"):
        silence_gate_db(rms, KeeperConfig())
