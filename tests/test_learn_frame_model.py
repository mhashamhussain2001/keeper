"""Cached-only frame features, whole-memo fitting, and unchanged downstream baseline checks."""

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from eval.audacity_labels import read_audacity_labels
from keeper.config import KeeperConfig
from keeper.frames import frame_labels_from_scores
from keeper.labels import MockLabelParser
import keeper.pipeline as pipeline
import keeper.segment_ast as segment_ast
from scripts import learn_frame_model as learn


def score_table(duration: float) -> pd.DataFrame:
    """Return cached-like raw and grouped probabilities for music, speech, and quiet intervals."""
    starts = np.arange(0, duration, 0.5)
    music, speech = starts < 6, (starts >= 6) & (starts < 8)
    return pd.DataFrame({"start": starts, "end": starts + 2, "singing": np.where(music, 0.9, 0.001),
                         "speech": np.where(speech, 0.9, 0.01), "instrument": np.where(music, 0.3, 0.01),
                         "Singing": np.where(music, 0.8, 0.001), "Humming": np.where(music, 0.1, 0.001),
                         "Music": np.where(music, 0.3, 0.01)})


def forbid_ast(*args: Any, **kwargs: Any) -> None:
    """Return no result because experiments and their tests must never load an AST scorer."""
    raise AssertionError("Never rescore AST")


def test_frame_features_use_overlap_neighbors_gaps_and_unclear_without_label_leakage() -> None:
    """Return None after checking projected raw scores, true adjacent frames, endpoint replication, and targets."""
    scores = score_table(2)
    scores["speech"] = [0.1, 0.3, 0.6, 0.8]
    scores["Singing"] = [0.05, 0.15, 0.25, 0.35]
    rms = np.array([-60, -20, -10, -40], dtype=float)
    truth = [(0.0, 0.5, "music"), (0.5, 1.0, "unclear"), (1.5, 2.0, "speech"), (0.1, 0.2, "cmd:keep")]
    frame = learn.make_frame_table(scores, rms, 2, truth)
    assert frame["target"].tolist() == ["music", "unclear", "silence", "speech"]
    assert frame["scored"].tolist() == [True, False, True, True]
    assert frame["speech"].tolist() == pytest.approx([0.1, 0.2, 1 / 3, 0.45])
    assert frame["Singing"].tolist() == pytest.approx([0.05, 0.1, 0.15, 0.2])
    assert frame["speech_prev"].tolist() == pytest.approx([0.1, 0.1, 0.2, 1 / 3])
    assert frame["speech_next"].tolist() == pytest.approx([0.2, 1 / 3, 0.45, 0.45])
    assert frame["rms_above_floor"].to_numpy() == pytest.approx(rms - np.percentile(rms, 10))
    assert frame["rms_above_median"].to_numpy() == pytest.approx(rms - np.median(rms))
    assert len(learn.FEATURES) == 12
    changed_truth = [(0.0, 2.0, "silence")]
    pd.testing.assert_frame_equal(frame[learn.FEATURES], learn.make_frame_table(scores, rms, 2, changed_truth)[learn.FEATURES])


def test_scaling_and_class_weights_use_only_scored_training_rows() -> None:
    """Return None after checking unclear rows cannot affect fitted scaling or either model's labels."""
    frame = pd.DataFrame({feature: np.arange(10, dtype=float) for feature in learn.FEATURES})
    frame["target"] = ["music", "speech", "silence"] * 3 + ["unclear"]
    frame["scored"] = frame["target"] != "unclear"
    frame.loc[9, learn.FEATURES] = 10000
    logistic, tree = learn.fit_models(frame)
    assert logistic.named_steps["standardscaler"].mean_ == pytest.approx(np.full(12, 4.0))
    assert logistic.named_steps["logisticregression"].class_weight == "balanced"
    assert tree.class_weight == "balanced" and tree.get_depth() <= 3
    assert set(tree.classes_) == {"music", "speech", "silence"}


def test_missing_cache_fails_before_any_audio_or_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking a missing cache cannot trigger audio loading or AST inference."""
    monkeypatch.setattr(learn, "load_audio", forbid_ast)
    monkeypatch.setattr(pipeline, "ASTScorer", forbid_ast)
    with pytest.raises(FileNotFoundError, match="refusing to rescore"):
        learn.run_experiment(tmp_path)
    assert not (tmp_path / "outputs" / "learned_frames").exists()


def test_two_whole_memo_folds_never_rescore_or_drop_downstream_frames(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking exactly two isolated fits, saved evidence, and cache-preserving execution."""
    fixture = tmp_path / "tests" / "fixtures" / "test_memo"
    fixture.mkdir(parents=True)
    (tmp_path / "outputs").mkdir()
    (tmp_path / "data").mkdir()
    score_table(12).to_csv(fixture / "window_scores.csv", index=False)
    score_table(12).to_csv(tmp_path / "outputs" / "memo2_scores.csv", index=False)
    (fixture / "meta.json").write_text('{"duration_s":12}')
    (fixture / "rms_db.json").write_text(json.dumps([-20] * 16 + [-80] * 8))
    text = "0\t6\tSING\n6\t8\tSPEECH\n8\t9\tUNCLEAR\n6\t8\tcmd:file section=verse\n0\t6\ttake:verse\n"
    (fixture / "truth_labels.txt").write_text(text)
    (tmp_path / "data" / "memo2_labels.txt").write_text(text)
    audio = np.concatenate([np.full(128000, 0.1), np.full(64000, 0.0001)]).astype(np.float32)
    monkeypatch.setattr(learn, "load_audio", lambda path, sr: audio)
    monkeypatch.setattr(pipeline, "ASTScorer", forbid_ast)
    monkeypatch.setattr(segment_ast, "ASTScorer", forbid_ast)
    load = learn.load_memos
    memos = load(tmp_path)
    monkeypatch.setattr(learn, "load_memos", lambda root: memos)
    fitted = []
    fit = learn.fit_models

    def record_fit(frame: pd.DataFrame) -> Any:
        """Return models after recording the single whole-memo table provided to fitting."""
        fitted.append(frame)
        return fit(frame)

    monkeypatch.setattr(learn, "fit_models", record_fit)
    report = learn.run_experiment(tmp_path)
    assert len(fitted) == 2 and fitted[0] is memos[1].frames and fitted[1] is memos[0].frames
    assert "random splits leak" in report and "Tree rules" in report and "Logistic coefficients" in report
    output = tmp_path / "outputs" / "learned_frames"
    results = json.loads((output / "results.json").read_text())
    assert results["folds"]["memo1"]["training_memo"] == "memo2"
    assert results["folds"]["memo2"]["training_memo"] == "memo1"
    for name in ("memo1", "memo2"):
        table = pd.read_csv(output / f"{name}_frames.csv")
        assert "unclear" not in set(table["target"])
        predictions = pd.read_csv(output / f"{name}_predictions.csv")
        assert len(predictions) == 24 and (~predictions["scored"]).sum() == 2
    assert not list(output.rglob("*.wav"))


def test_hand_baseline_matches_current_numeric_fixture_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking experimental hand-rule metrics and take boundaries match the real pipeline."""
    fixture = Path(__file__).parent / "fixtures" / "test_memo"
    scores = pd.read_csv(fixture / "window_scores.csv")
    rms = np.asarray(json.loads((fixture / "rms_db.json").read_text()), dtype=float)[:57]
    truth = read_audacity_labels(fixture / "truth_labels.txt")
    frames = learn.make_frame_table(scores, rms, 28.28, truth)
    memo = learn.Memo("memo1", str(fixture), scores, frames, rms, 28.28, truth, learn.MEMO1_TAKES, fixture / "window_scores.csv", "")
    labels = frame_labels_from_scores(scores, rms, KeeperConfig())[0]
    measured = learn.evaluate_labels(memo, labels, tmp_path / "experiment")
    monkeypatch.setattr(pipeline, "ASTScorer", forbid_ast)
    golden, _, _ = pipeline.run_pipeline(fixture, MockLabelParser(fixture / "mock_commands.json"), tmp_path / "baseline",
                                         scores_csv=fixture / "window_scores.csv", rms_json=fixture / "rms_db.json", no_export=True)
    assert measured["frame_metrics"]["overall_accuracy"] == pytest.approx(0.8596491228070176)
    assert measured["commands_detected"] == 3 and measured["positive_hits"] == 3
    predicted = json.loads((tmp_path / "experiment" / "songmap.json").read_text())
    assert [(take["start"], take["end"]) for take in predicted["sections"]["chorus"]] == [
        (take.start, take.end) for take in golden.sections["chorus"]]


def test_downstream_oracle_reject_uses_current_rejected_section(tmp_path: Path) -> None:
    """Return None after checking an oracle reject produces a rejected take without entering positive hits."""
    scores = score_table(12)
    rms = np.array([-20] * 16 + [-80] * 8, dtype=float)
    truth = [(0.0, 6.0, "music"), (6.0, 8.0, "speech"), (6.5, 7.5, "cmd:reject")]
    frame = learn.make_frame_table(scores, rms, 12, truth)
    memo = learn.Memo("fixture", "fixture.wav", scores, frame, rms, 12, truth,
                      [(0.0, 6.0, "take:rejected")], tmp_path / "unused.csv", "")
    labels = ["music"] * 12 + ["speech"] * 4 + ["silence"] * 8
    result = learn.evaluate_labels(memo, labels, tmp_path / "result")
    songmap = json.loads((tmp_path / "result" / "songmap.json").read_text())
    assert "rejected" in songmap["sections"]
    assert result["commands_detected"] == 1 and result["positive_total"] == 0
