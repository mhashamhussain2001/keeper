"""Verify diagnostic attribution, score statistics, edge simulation, and isolated timing settings."""

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import keeper.segment_ast as ast_module
import scripts.diagnose_memo as diagnostic
from keeper.types import SongMap, Take


def test_activity_truth_preserves_singing_and_instrument_aliases(tmp_path: Path) -> None:
    """Return None after checking diagnostic truth retains distinctions collapsed by pipeline evaluation."""
    path = tmp_path / "truth.txt"
    path.write_text("0\t1\tsInG\n1\t2\tINST\n2\t3\tUNCLEAR\n2\t2\tcmd:none\n", encoding="utf-8")
    assert diagnostic.activity_truth(path) == [(0.0, 1.0, "singing"), (1.0, 2.0, "instrument"),
                                               (2.0, 3.0, "unclear")]


def test_raw_label_projection_matches_overlapping_window_means() -> None:
    """Return None after checking raw-label and group scores use the same overlap projection."""
    windows = pd.DataFrame({"start": [0.0, 1.0], "end": [2.0, 3.0], "singing": [0.1, 0.8],
                            "instrument": [0.9, 0.2], "speech": [0.0, 0.0], "Singing": [0.1, 0.8],
                            "Humming": [0.05, 0.1], "Music": [0.8, 0.2]})
    projected = diagnostic.feature_scores(windows, 3.0)
    assert projected["singing group"][2] == pytest.approx(0.45)
    assert projected["Music"][2] == pytest.approx(0.5)
    assert projected["Humming"][2] == pytest.approx(0.075)
    assert projected["singing - instrument"][2] == pytest.approx(-0.1)


@pytest.mark.parametrize("values,expected", [([0.1, 0.2, 0.8, 0.9], 1.0),
                                             ([0.9, 0.8, 0.2, 0.1], 0.0),
                                             ([0.5, 0.5, 0.5, 0.5], 0.5)])
def test_auc_ties_and_direction(values: list[float], expected: float) -> None:
    """Return None after checking perfect, reversed, and tied ROC scores."""
    assert diagnostic.roc_auc(np.array([False, False, True, True]), np.array(values)) == expected
    assert diagnostic.roc_auc(np.ones(4, dtype=bool), np.array(values)) is None


@pytest.mark.parametrize("values,direction,expected", [([0.1, 0.2, 0.8, 0.9], 1, 0.8),
                                                        ([0.9, 0.8, 0.2, 0.1], -1, 0.2)])
def test_threshold_optimizes_balanced_accuracy(values: list[float], direction: int, expected: float) -> None:
    """Return None after checking both score directions can recover a perfect balanced threshold."""
    threshold, balanced = diagnostic.best_threshold(np.array([False, False, True, True]),
                                                     np.array(values), direction)
    assert threshold == expected and balanced == 1.0


def _scores(count: int, speech_index: int) -> pd.DataFrame:
    """Return half-second windows with one isolated speech candidate."""
    start = np.arange(count) * 0.5
    speech = np.full(count, 0.1)
    speech[speech_index] = 0.8
    return pd.DataFrame({"start": start, "end": start + 0.5,
                         "singing": 0.5, "instrument": 0.2, "speech": speech})


def test_stage_trace_attributes_short_island_and_long_island_mode_losses() -> None:
    """Return None after checking verified traces distinguish island voting from mode filtering."""
    short = diagnostic.stage_trace(_scores(12, 5), np.full(12, -20.0), 6.0, [])
    assert short.loc[5, "raw"] == "speech" and short.loc[5, "vote"] == "music"
    assert not short["island_skipped"].any()
    assert diagnostic.command_cause(short, 2.5, 3.0).startswith("island vote: music won 11/12")
    long = diagnostic.stage_trace(_scores(20, 10), np.full(20, -20.0), 10.0, [])
    assert long["island_skipped"].all() and long.loc[10, "vote"] == "speech"
    assert long.loc[10, "mode"] == "music" and diagnostic.command_cause(long, 5.0, 5.5) == "mode filter"


def test_silence_loss_causes_are_disjoint() -> None:
    """Return None after checking above-gate, mode-filled, and absorbed losses sum exactly once."""
    frames = pd.DataFrame({"start": [0.0, 0.5, 1.0, 1.5], "end": [0.5, 1.0, 1.5, 1.75],
                           "truth": "silence", "final": "music", "rms_db": [-20, -20, -50, -50],
                           "gate_db": -40, "raw": ["music", "speech", "silence", "silence"],
                           "mode": ["music", "music", "music", "silence"], "absorb": "music"})
    rows, causes = diagnostic.silence_runs(frames)
    assert causes == {"louder_than_gate": 2, "mode_filter": 1, "absorb_short": 1, "other": 0}
    assert len(rows) == 1 and rows[0][2:4] == ["1.750", 4]


def test_trim_simulation_keeps_internal_frames_rolls_and_original_songmap() -> None:
    """Return None after checking edge-only trimming, empty-take removal, and unchanged originals."""
    frames = pd.DataFrame({"start": np.arange(10), "end": np.arange(10) + 1})
    vocal = np.array([False, False, True, True, False, False, True, False, False, False])
    original = SongMap("memo.wav", {"verse": [Take("v", "verse", "A", 0.0, 10.0, "memo.wav", None, None)],
                                    "unlabeled": [Take("u", "unlabeled", "A", 8.0, 10.0, "memo.wav", None, None)]})
    trimmed = diagnostic.trim_simulation(original, frames, vocal)
    take = trimmed.sections["verse"][0]
    assert (take.start, take.end) == (1.7, 7.5)
    assert trimmed.sections["unlabeled"] == []
    assert original.sections["verse"][0].start == 0.0 and len(original.sections["unlabeled"]) == 1


def test_benchmark_uses_only_prefix_restores_settings_and_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return None after checking the timing harness isolates overrides and reuses completed checkpoints."""
    threads = [4]
    calls: list[tuple[int, int, int]] = []
    fake_torch = SimpleNamespace(__version__="test", get_num_threads=lambda: threads[0],
                                 set_num_threads=lambda value: threads.__setitem__(0, value))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(diagnostic.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(diagnostic, "cpu_model", lambda: "test CPU")
    monkeypatch.setattr(diagnostic, "load_audio", lambda path, sr: np.zeros(80 * sr, dtype=np.float32))

    class Scorer:
        """Fake model exposing the benchmark's input size and runtime overrides."""

        def score_windows(self, y: np.ndarray, sr: int, win_s: float, hop_s: float) -> pd.DataFrame:
            """Return identical probabilities while recording each benchmark configuration."""
            calls.append((len(y), ast_module.AST_BATCH_SIZE, threads[0]))
            return pd.DataFrame({"start": [0.0, 0.5], "end": [2.0, 2.5], "singing": [0.1, 0.2]})

    monkeypatch.setattr(ast_module, "ASTScorer", Scorer)
    result = diagnostic.benchmark_ast(tmp_path / "audio.wav", tmp_path)
    assert calls == [(60 * 16000, 8, 4), (60 * 16000, 16, 4),
                     (60 * 16000, 32, 4), (60 * 16000, 8, 8)]
    assert all(row["identical"] for row in result["runs"])
    assert ast_module.AST_BATCH_SIZE == 8 and threads[0] == 4
    diagnostic.benchmark_ast(tmp_path / "audio.wav", tmp_path)
    assert len(calls) == 4
