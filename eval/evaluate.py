"""Frame-resolution activity metrics with ground-truth rows and prediction columns."""

from math import ceil, isclose, isfinite
from operator import attrgetter
from typing import cast

from keeper.config import FRAME_KINDS
from keeper.types import FrameLabel, Region


def _activity_regions(regions: list[Region] | list[tuple[float, float, str]]) -> list[Region]:
    """Return validated activity regions, ignoring raw Audacity command annotations."""
    result: list[Region] = []
    for item in regions:
        if isinstance(item, Region):
            region = item
        else:
            start, end, label = item
            if label.startswith("cmd:"):
                continue
            region = Region(float(start), float(end), cast(FrameLabel, label), {})
        if region.kind not in FRAME_KINDS:
            raise ValueError(f"Unknown activity label: {region.kind}")
        if not isfinite(region.start) or not isfinite(region.end) or not 0 <= region.start < region.end:
            raise ValueError("Activity regions must have finite 0 <= start < end")
        result.append(region)
    result.sort(key=attrgetter("start"))
    boundary = 0.0
    for region in result:
        if not isclose(region.start, boundary):
            raise ValueError("Activity regions must tile the memo from zero without gaps or overlaps")
        boundary = region.end
    return result


def _label_at(regions: list[Region], time_s: float) -> FrameLabel:
    """Return the activity label covering a frame midpoint."""
    for region in regions:
        if region.start <= time_s < region.end:
            return region.kind
    raise ValueError("Frame midpoint is not covered by an activity region")


def frame_accuracy(
    pred_regions: list[Region] | list[tuple[float, float, str]],
    gt_regions: list[Region] | list[tuple[float, float, str]],
    hop_s: float,
) -> dict:
    """Return overall accuracy, per-class precision/recall, and a truth-by-prediction confusion matrix."""
    if not isfinite(hop_s) or hop_s <= 0:
        raise ValueError("hop_s must be positive and finite")
    predicted, truth = _activity_regions(pred_regions), _activity_regions(gt_regions)
    pred_duration = predicted[-1].end if predicted else 0.0
    duration = truth[-1].end if truth else 0.0
    if not isclose(pred_duration, duration):
        raise ValueError("Predicted and ground-truth durations must match")
    matrix = {actual: {guess: 0 for guess in FRAME_KINDS} for actual in FRAME_KINDS}
    frame_count = ceil(duration / hop_s)
    for index in range(frame_count):
        midpoint = (index * hop_s + min((index + 1) * hop_s, duration)) / 2
        matrix[_label_at(truth, midpoint)][_label_at(predicted, midpoint)] += 1
    per_class = {}
    for kind in FRAME_KINDS:
        true_positive = matrix[kind][kind]
        actual_count = sum(matrix[kind].values())
        predicted_count = sum(row[kind] for row in matrix.values())
        per_class[kind] = {
            "precision": true_positive / predicted_count if predicted_count else 0.0,
            "recall": true_positive / actual_count if actual_count else 0.0,
        }
    return {
        "overall_accuracy": sum(matrix[kind][kind] for kind in FRAME_KINDS) / frame_count if frame_count else 0.0,
        "per_class": per_class,
        "confusion_matrix": matrix,
    }
