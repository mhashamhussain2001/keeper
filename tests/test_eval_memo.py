"""Offline coverage for oracle command mapping and reusable memo evaluation."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import soundfile as sf

import keeper.pipeline as pipeline
from eval.evaluate import frame_accuracy
from keeper.types import Region, SongMap, Take
from scripts.eval_memo import evaluate_memo, oracle_commands, render_report


def test_oracle_commands_use_greatest_overlap_and_audit_misses_and_collisions() -> None:
    """Return None after checking maximum overlap, noncommands, missing speech, and collision policy."""
    speech = [Region(1.0, 3.0, "speech", {}), Region(4.0, 5.0, "speech", {}),
              Region(8.0, 9.0, "speech", {})]
    truth = [(2.0, 4.5, "cmd:file section=verse"), (4.0, 4.4, "cmd:reject"),
             (4.5, 4.8, "cmd:keep"), (6.0, 7.0, "cmd:none"),
             (8.0, 8.5, "cmd:file section=bridge target=next note=test"),
             (9.0, 10.0, "cmd:keep")]
    labels, audit = oracle_commands(truth, speech)
    assert labels["1.0"]["section"] == "verse" and labels["1.0"]["is_command"]
    assert labels["4.0"]["section"] == "unsorted" and labels["4.0"]["is_command"]
    assert audit[1]["status"] == "superseded (same speech region)"
    assert audit[3]["speech_start"] is None and audit[-1]["speech_start"] is None
    assert labels["8.0"]["section"] == "bridge" and labels["8.0"]["note"] == "test"
    assert audit[4]["command"]["target"] == "next"
    for intent in ("reject", "none"):
        mapped, _ = oracle_commands([(1.0, 2.0, f"cmd:{intent}")], speech)
        assert not mapped["1.0"]["is_command"]


def test_primary_hits_require_section_and_half_truth_coverage() -> None:
    """Return None after checking hits, wrong sections, rejected output, and unscored extras."""
    songmap = SongMap("test.wav", {
        "verse": [Take("verse-a", "verse", "A", 0.0, 5.0, "test.wav", None, None)],
        "unlabeled": [Take("unlabeled-a", "unlabeled", "A", 20.0, 30.0, "test.wav", None, None)],
    })
    truth = [(0.0, 10.0, "take:verse"), (0.0, 10.01, "take:verse"),
             (0.0, 5.0, "take:chorus"), (20.0, 25.0, "take:rejected")]
    metrics = frame_accuracy([(0.0, 30.0, "music")], [], 0.5)
    report = render_report("test", 30.0, metrics, songmap, truth, [], [], 1.0, True)
    assert "Positive command/take hits: **1/3**" in report
    assert "take:chorus | 0.000-5.000 | - | 0.00% | MISS" in report
    assert "take:rejected | 20.000-25.000 | YES | unlabeled-a [unlabeled], 100.00% covered" in report
    assert "## Extras (unscored)" in report and "## Secondary: take boundary errors" in report


def _inputs(root: Path) -> pd.DataFrame:
    """Return deterministic window scores after writing synthetic audio and sparse truth."""
    (root / "data").mkdir()
    (root / "outputs").mkdir()
    y = (0.1 * np.sin(2 * np.pi * 220 * np.arange(12 * 16000) / 16000)).astype(np.float32)
    y[11 * 16000:] = 0.0
    sf.write(root / "data" / "sample.wav", y, 16000)
    (root / "data" / "sample_labels.txt").write_text(
        "0\t3\tSING\n3\t4\tUNCLEAR\n4\t9\tINST\n9\t11\tSPEECH\n"
        "9.1\t9.8\tcmd:file section=verse\n0\t9\ttake:verse\n", encoding="utf-8",
    )
    starts = np.arange(24) * 0.5
    return pd.DataFrame({"start": starts, "end": starts + 0.5,
                         "singing": 0.95, "instrument": 0.05,
                         "speech": np.where((starts >= 9) & (starts < 11), 0.95, 0.01)})


def _forbid_ast() -> None:
    """Return no model because an existing CSV must never be rescored."""
    raise AssertionError("Cached evaluation must not load AST")


def test_cached_evaluation_exports_oracle_clips_without_loading_ast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return None after checking cached evaluation runs the real pipeline, exports, and saves its report."""
    scores = _inputs(tmp_path)
    cache = tmp_path / "outputs" / "sample_scores.csv"
    scores.to_csv(cache, index=False)
    original = cache.read_bytes()
    monkeypatch.setattr(pipeline, "ASTScorer", _forbid_ast)
    report = evaluate_memo("sample", tmp_path)
    destination = tmp_path / "outputs" / "sample_eval"
    assert cache.read_bytes() == original
    assert (destination / "report.md").read_text(encoding="utf-8") == report
    assert "UNCLEAR: **2 frames**" in report and "Positive command/take hits: **1/1**" in report
    assert "Command detection: 1/1" in report and "existing cache; not rescored" in report
    songmap = json.loads((destination / "songmap.json").read_text())
    clip = Path(songmap["sections"]["verse"][0]["clip_path"])
    assert clip.is_file() and sf.info(clip).samplerate == 16000


def test_missing_score_cache_is_generated_once_then_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return None after checking an absent CSV is generated once and future runs use it unchanged."""
    scores = _inputs(tmp_path)
    calls: list[int] = []

    class Scorer:
        """Deterministic scorer recording calls without models or network activity."""

        def score_windows(self, y: np.ndarray, sr: int, win_s: float, hop_s: float) -> pd.DataFrame:
            """Return fixed score windows and record this scoring call."""
            calls.append(1)
            return scores.copy()

    monkeypatch.setattr(pipeline, "ASTScorer", Scorer)
    report = evaluate_memo("sample", tmp_path)
    assert "generated once; cached for future runs" in report and calls == [1]
    monkeypatch.setattr(pipeline, "ASTScorer", _forbid_ast)
    assert "existing cache; not rescored" in evaluate_memo("sample", tmp_path)
