"""Audacity import and frame-level evaluation fixtures."""

from pathlib import Path

import pytest

from eval.audacity_labels import read_audacity_labels
from eval.evaluate import frame_accuracy, take_boundary_error
from keeper.config import FRAME_KINDS
from keeper.types import Region, SongMap, Take


def test_read_labels_including_point(tmp_path: Path) -> None:
    """Return None after checking music aliases map and command/take truth is preserved."""
    path = tmp_path / "labels.txt"
    path.write_text("0\t1\tsinging\n1\t2\tinstrument\n2\t3\tspeech\n2.5\t2.5\tcmd:chorus B\n"
                    "0\t2\ttake:chorus B\n\n", encoding="utf-8")
    assert read_audacity_labels(path) == [(0.0, 1.0, "music"), (1.0, 2.0, "music"), (2.0, 3.0, "speech"),
                                           (2.5, 2.5, "cmd:chorus B"), (0.0, 2.0, "take:chorus B")]


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
    """Return None after checking evaluations cannot silently truncate longer truth activity."""
    with pytest.raises(ValueError, match="durations"):
        frame_accuracy([Region(0.0, 1.0, "music", {})], [Region(0.0, 2.0, "music", {})], 0.5)


def test_sparse_truth_uses_silence_in_all_gaps(tmp_path: Path) -> None:
    """Return None after checking leading, internal, and trailing truth gaps count as silence."""
    path = tmp_path / "truth.txt"
    path.write_text("1\t2\tsinging\n3\t4\tspeech\n4\t5\tinstrument\n"
                    "0\t10\ttake:chorus B\n8\t8\tcmd:keep that\n", encoding="utf-8")
    predicted = [(0.0, 1.0, "silence"), (1.0, 2.0, "music"), (2.0, 3.0, "music"),
                 (3.0, 4.0, "speech"), (4.0, 5.0, "music"), (5.0, 6.0, "silence")]
    metrics = frame_accuracy(predicted, read_audacity_labels(path), 1.0)
    assert metrics["overall_accuracy"] == 5 / 6
    assert metrics["confusion_matrix"]["silence"] == {"music": 1, "speech": 0, "silence": 2}


@pytest.mark.parametrize("truth", [[], [(2.0, 2.0, "cmd:keep"), (0.0, 4.0, "take:A")]])
def test_missing_activity_truth_is_all_silence(truth: list[tuple[float, float, str]]) -> None:
    """Return None after checking empty and annotation-only truth score every memo frame."""
    metrics = frame_accuracy([(0.0, 1.0, "silence"), (1.0, 1.2, "music")], truth, 0.5)
    assert metrics["overall_accuracy"] == 2 / 3
    assert metrics["confusion_matrix"]["silence"]["music"] == 1


def test_overlapping_activity_truth_is_rejected() -> None:
    """Return None after checking sparse truth still rejects ambiguous activity overlaps."""
    with pytest.raises(ValueError, match="overlap"):
        frame_accuracy([(0.0, 3.0, "music")], [(0.0, 2.0, "music"), (1.0, 3.0, "speech")], 0.5)


def test_predictions_still_require_full_coverage() -> None:
    """Return None after checking prediction gaps are not silently filled."""
    with pytest.raises(ValueError, match="without gaps"):
        frame_accuracy([(1.0, 2.0, "music")], [(1.0, 2.0, "music")], 0.5)


def _take(take_id: str, start: float, end: float) -> Take:
    """Return a predicted take with the requested ID and boundaries."""
    return Take(take_id, "chorus", take_id, start, end, "memo.wav", None, None)


def test_take_boundaries_match_by_overlap_and_count_misses_and_extras(tmp_path: Path) -> None:
    """Return None after checking maximum-overlap matching, boundary errors, misses, and extras."""
    songmap = SongMap("memo.wav", {
        "chorus": [_take("small", 1.0, 2.0), _take("best", 0.5, 4.5)],
        "verse": [_take("later", 6.25, 8.75), _take("extra", 10.0, 11.0)],
    })
    path = tmp_path / "truth.txt"
    path.write_text("1\t4\ttake:first\n6\t9\ttake:second\n11\t12\ttake:miss\n"
                    "0\t12\tsinging\n3\t3\tcmd:keep\n", encoding="utf-8")
    metrics = take_boundary_error(songmap, read_audacity_labels(path))
    assert metrics == {
        "per_take": [
            {"truth_label": "take:first", "predicted_id": "best", "start_error_s": 0.5, "end_error_s": 0.5},
            {"truth_label": "take:second", "predicted_id": "later", "start_error_s": 0.25, "end_error_s": 0.25},
            {"truth_label": "take:miss", "predicted_id": None, "start_error_s": None, "end_error_s": None},
        ],
        "misses": 1, "extras": 2,
    }


def test_take_matching_is_independent_and_ties_follow_songmap_order() -> None:
    """Return None after checking merged predictions may match multiple truth takes deterministically."""
    songmap = SongMap("memo.wav", {"chorus": [_take("first", 0.0, 4.0), _take("tied", 0.0, 4.0)]})
    metrics = take_boundary_error(songmap, [_take("A", 0.0, 1.0), _take("B", 3.0, 4.0)])
    assert [row["predicted_id"] for row in metrics["per_take"]] == ["first", "first"]
    assert metrics["per_take"][0]["start_error_s"] == 0.0
    assert metrics["per_take"][1]["end_error_s"] == 0.0
    assert metrics["misses"] == 0 and metrics["extras"] == 1


@pytest.mark.parametrize("predicted,truth,misses,extras", [
    ([], [], 0, 0),
    ([], [(0.0, 1.0, "take:A")], 1, 0),
    ([_take("A", 0.0, 1.0)], [], 0, 1),
    ([_take("A", 0.0, 1.0)], [(1.0, 2.0, "take:B")], 1, 1),
])
def test_take_matching_empty_and_nonoverlapping_inputs(
    predicted: list[Take], truth: list[tuple[float, float, str]], misses: int, extras: int,
) -> None:
    """Return None after checking absent takes and touching endpoints do not create matches."""
    metrics = take_boundary_error(SongMap("memo.wav", {"chorus": predicted}), truth)
    assert metrics["misses"] == misses and metrics["extras"] == extras


@pytest.mark.parametrize("start,end", [(1.0, 1.0), (-1.0, 1.0), (2.0, 1.0), (0.0, float("inf")),
                                       (float("nan"), 1.0)])
def test_take_boundaries_reject_invalid_intervals(start: float, end: float) -> None:
    """Return None after checking predicted and truth take intervals must be finite and positive."""
    with pytest.raises(ValueError, match="Take intervals"):
        take_boundary_error(SongMap("memo.wav", {}), [(start, end, "take:A")])
    with pytest.raises(ValueError, match="Take intervals"):
        take_boundary_error(SongMap("memo.wav", {"chorus": [_take("A", start, end)]}), [])
