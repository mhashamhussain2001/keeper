"""Frame-resolution activity metrics and overlap-matched take boundary errors."""

from math import ceil, isclose, isfinite
from operator import attrgetter
from typing import cast

from keeper.config import FRAME_KINDS
from keeper.types import FrameLabel, Region, SongMap, Take


def _activity_regions(
    regions: list[Region] | list[tuple[float, float, str]], allow_gaps: bool = False,
) -> list[Region]:
    """Return validated activity regions, ignoring command and take annotations."""
    result: list[Region] = []
    for item in regions:
        if isinstance(item, Region):
            region = item
        else:
            start, end, label = item
            region = Region(float(start), float(end), cast(FrameLabel, label), {})
        if region.kind.startswith(("cmd:", "take:")):
            continue
        if region.kind not in FRAME_KINDS:
            raise ValueError(f"Unknown activity label: {region.kind}")
        if not isfinite(region.start) or not isfinite(region.end) or not 0 <= region.start < region.end:
            raise ValueError("Activity regions must have finite 0 <= start < end")
        result.append(region)
    result.sort(key=attrgetter("start"))
    boundary = 0.0
    for region in result:
        if region.start < boundary:
            raise ValueError("Activity regions must not overlap")
        if not allow_gaps and not isclose(region.start, boundary):
            raise ValueError("Activity regions must tile the memo from zero without gaps or overlaps")
        boundary = region.end
    return result


def _label_at(regions: list[Region], time_s: float) -> FrameLabel:
    """Return the activity label covering a frame midpoint, or silence in a gap."""
    for region in regions:
        if region.start <= time_s < region.end:
            return region.kind
    return "silence"


def frame_accuracy(
    pred_regions: list[Region] | list[tuple[float, float, str]],
    gt_regions: list[Region] | list[tuple[float, float, str]],
    hop_s: float,
) -> dict:
    """Return frame metrics over the prediction duration, treating truth gaps as silence."""
    if not isfinite(hop_s) or hop_s <= 0:
        raise ValueError("hop_s must be positive and finite")
    predicted = _activity_regions(pred_regions)
    truth = _activity_regions(gt_regions, allow_gaps=True)
    duration = predicted[-1].end if predicted else 0.0
    truth_end = truth[-1].end if truth else 0.0
    if truth_end > duration and not isclose(truth_end, duration):
        raise ValueError("Ground-truth activity exceeds the predicted memo duration; check durations")
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


def take_boundary_error(
    pred_songmap: SongMap, truth_takes: list[Take] | list[tuple[float, float, str]],
) -> dict:
    """Return per-take absolute boundary errors in seconds plus miss and extra counts.

    Truth accepts Take records or Audacity rows (only take: rows are selected).
    Each truth independently selects its greatest positive overlap, with ties
    resolved by song-map order. A prediction may match multiple truth takes.
    Misses have a null predicted_id and null errors; extras count unused
    predictions. Per-take results retain truth order and its label or ID.
    """
    predicted = [take for takes in pred_songmap.sections.values() for take in takes]
    truth = [
        (item.start, item.end, item.id) if isinstance(item, Take) else item
        for item in truth_takes
        if isinstance(item, Take) or item[2].startswith("take:")
    ]
    for start, end in [(take.start, take.end) for take in predicted] + [
        (start, end) for start, end, _ in truth
    ]:
        if not isfinite(start) or not isfinite(end) or not 0 <= start < end:
            raise ValueError("Take intervals must have finite 0 <= start < end")
    per_take = []
    matched: set[int] = set()
    misses = 0
    for start, end, label in truth:
        overlaps = [max(0.0, min(end, take.end) - max(start, take.start)) for take in predicted]
        best = max(range(len(predicted)), key=overlaps.__getitem__, default=None)
        take = predicted[best] if best is not None and overlaps[best] > 0.0 else None
        if take is None:
            misses += 1
        else:
            assert best is not None
            matched.add(best)
        per_take.append({
            "truth_label": label,
            "predicted_id": take.id if take is not None else None,
            "start_error_s": abs(take.start - start) if take is not None else None,
            "end_error_s": abs(take.end - end) if take is not None else None,
        })
    return {"per_take": per_take, "misses": misses, "extras": len(predicted) - len(matched)}
