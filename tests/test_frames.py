"""Synthetic classifier projection, gating, smoothing, and RMS tests."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import soundfile as sf

from keeper.audio_io import load_audio, rms_db_per_frame
from keeper.config import KeeperConfig
from keeper.frames import frame_labels_from_scores, frame_scores_from_windows


def _scores(singing: list[float], speech: list[float]) -> pd.DataFrame:
    """Return non-overlapping synthetic score windows on the default frame grid."""
    hop = KeeperConfig().hop_s
    starts = np.arange(len(singing)) * hop
    return pd.DataFrame({"start": starts, "end": starts + hop,
                         "singing": singing, "speech": speech, "instrument": 0.1})


def test_silence_override() -> None:
    """Return None after checking silence wins over high voice probabilities."""
    cfg = replace(KeeperConfig(), mode_filter_frames=1)
    assert frame_labels_from_scores(_scores([0.99], [0.98]), np.array([-60.0]), cfg) == ["silence"]


def test_low_voice_is_instrument() -> None:
    """Return None after checking low voice becomes instrument regardless of music score."""
    cfg = replace(KeeperConfig(), mode_filter_frames=1)
    assert frame_labels_from_scores(_scores([0.1], [0.15]), np.array([-10.0]), cfg) == ["instrument"]


def test_voice_tie_is_singing() -> None:
    """Return None after checking equal vocal probabilities choose singing."""
    cfg = replace(KeeperConfig(), mode_filter_frames=1)
    assert frame_labels_from_scores(_scores([0.8], [0.8]), np.array([-10.0]), cfg) == ["singing"]


def test_mode_filter_removes_blip() -> None:
    """Return None after checking the centered mode removes an isolated speech frame."""
    scores = _scores([0.9, 0.9, 0.1, 0.9, 0.9], [0.1, 0.1, 0.9, 0.1, 0.1])
    assert frame_labels_from_scores(scores, np.full(5, -10.0), KeeperConfig()) == ["singing"] * 5


def test_mode_tie_keeps_original() -> None:
    """Return None after checking truncated edge neighborhoods preserve tied original labels."""
    scores = _scores([0.9, 0.1], [0.1, 0.9])
    assert frame_labels_from_scores(scores, np.full(2, -10.0), KeeperConfig()) == ["singing", "speech"]


def test_overlap_uses_mean_and_half_open_intervals() -> None:
    """Return None after checking overlap means exclude windows touching only a boundary."""
    scores = pd.DataFrame({"start": [0.0, 0.5], "end": [1.0, 1.5],
                           "singing": [0.2, 0.8], "speech": [0.8, 0.2], "instrument": [0.1, 0.3]})
    frames = frame_scores_from_windows(scores, 1.5, 0.5)
    np.testing.assert_allclose(frames["singing"], [0.2, 0.5, 0.8])
    np.testing.assert_allclose(frames["instrument"], [0.1, 0.2, 0.3])


def test_partial_window_overlap_counts_equally() -> None:
    """Return None after checking any positive overlap contributes one unweighted window score."""
    scores = pd.DataFrame({"start": [0.0, 0.49], "end": [1.0, 0.51],
                           "singing": [0.2, 0.8], "speech": [0.1, 0.1], "instrument": [0.1, 0.1]})
    assert frame_scores_from_windows(scores, 1.0, 0.5)["singing"].tolist() == [0.5, 0.5]


def test_uncovered_frame_is_rejected() -> None:
    """Return None after checking missing score coverage fails explicitly."""
    with pytest.raises(ValueError, match="overlap"):
        frame_scores_from_windows(_scores([0.9], [0.1]), 1.0, 0.5)


def test_rms_includes_partial_final_frame() -> None:
    """Return None after checking dBFS for full, silent, and partial audio frames."""
    cfg = KeeperConfig()
    y = np.concatenate([np.ones(8000), np.zeros(8000), np.full(1600, 0.5)]).astype(np.float32)
    rms = rms_db_per_frame(y, cfg.sample_rate, cfg.hop_s)
    np.testing.assert_allclose(rms, [0.0, -200.0, 20 * np.log10(0.5)], atol=1e-8)
    assert rms_db_per_frame(np.array([], dtype=np.float32), cfg.sample_rate, cfg.hop_s).size == 0


def test_config_rejects_even_filter() -> None:
    """Return None after checking invalid even filter widths fail at configuration time."""
    with pytest.raises(ValueError, match="odd"):
        KeeperConfig(mode_filter_frames=2)


def test_load_audio_converts_stereo_and_resamples(tmp_path: Path) -> None:
    """Return None after checking file loading enforces mono float32 audio at 16000 Hz."""
    path = tmp_path / "stereo.wav"
    stereo = np.column_stack([np.full(44100, 0.2), np.full(44100, 0.4)]).astype(np.float32)
    sf.write(path, stereo, 44100, subtype="FLOAT")
    y = load_audio(path, KeeperConfig().sample_rate)
    assert y.shape == (16000,) and y.dtype == np.float32
    np.testing.assert_allclose(y[100:-100], 0.3, atol=1e-5)
