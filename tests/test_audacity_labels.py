"""Audacity import and frame-level evaluation fixtures."""

from pathlib import Path

import pytest

from eval.audacity_labels import read_audacity_labels
from eval.evaluate import frame_accuracy
from keeper.config import FRAME_KINDS
from keeper.types import Region


def test_read_labels_including_point(tmp_path: Path) -> None:
    """Return None after checking legacy music aliases map and command text is preserved."""
    path = tmp_path / "labels.txt"
    path.write_text("0\t1\tsinging\n1\t2\tinstrument\n2\t3\tspeech\n2.5\t2.5\tcmd:chorus B\n\n", encoding="utf-8")
    assert read_audacity_labels(path) == [(0.0, 1.0, "music"), (1.0, 2.0, "music"), (2.0, 3.0, "speech"),
                                           (2.5, 2.5, "cmd:chorus B")]


def test_malformed_labels_fail(tmp_path: Path) -> None:
    """Return None after checking malformed label records have an actionable error."""
    path = tmp_path / "labels.txt"
    path.write_text("0\tmusic\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        read_audacity_labels(path)


def test_frame_accuracy_and_confusion_matrix() -> None:
    """Return None after checking known class counts, precision, recall, and command exclusion."""
    predicted = [Region(0.0, 1.5, "music", {}), Region(1.5, 2.0, "speech", {})]
    truth = [(0.0, 1.0, "music"), (1.0, 2.0, "speech"), (1.5, 1.5, "cmd:chorus B")]
    metrics = frame_accuracy(predicted, truth, 0.5)
    assert metrics["overall_accuracy"] == 0.75
    assert metrics["per_class"]["music"] == {"precision": 2 / 3, "recall": 1.0}
    assert metrics["per_class"]["speech"] == {"precision": 1.0, "recall": 0.5}
    assert metrics["per_class"]["silence"] == {"precision": 0.0, "recall": 0.0}
    matrix = metrics["confusion_matrix"]
    assert set(matrix) == set(FRAME_KINDS)
    assert all(set(row) == set(FRAME_KINDS) for row in matrix.values())
    assert matrix["speech"]["music"] == 1


def test_frame_accuracy_includes_partial_final_frame() -> None:
    """Return None after checking a partial final interval contributes one comparison frame."""
    predicted = [Region(0.0, 1.0, "music", {}), Region(1.0, 1.2, "speech", {})]
    truth = [Region(0.0, 1.2, "music", {})]
    assert frame_accuracy(predicted, truth, 0.5)["overall_accuracy"] == 2 / 3


def test_evaluation_rejects_mismatched_durations() -> None:
    """Return None after checking evaluations cannot silently truncate the longer memo."""
    with pytest.raises(ValueError, match="durations"):
        frame_accuracy([Region(0.0, 1.0, "music", {})], [Region(0.0, 2.0, "music", {})], 0.5)
