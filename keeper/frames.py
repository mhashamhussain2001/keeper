"""Projection of overlapping classifier windows onto fixed activity frames."""

from collections import Counter
from heapq import heappop, heappush
from math import ceil, fsum
from typing import cast

import numpy as np
import pandas as pd

from keeper.config import SCORE_GROUPS, KeeperConfig
from keeper.types import FrameLabel


def frame_scores_from_windows(scores_df: pd.DataFrame, duration_s: float, hop_s: float) -> pd.DataFrame:
    """Return mean group probabilities over every window overlapping each frame."""
    if not np.isfinite(duration_s) or duration_s < 0 or not np.isfinite(hop_s) or hop_s <= 0:
        raise ValueError("Duration must be nonnegative and hop must be positive and finite")
    columns = ["start", "end", *SCORE_GROUPS]
    missing = set(columns) - set(scores_df.columns)
    if missing:
        raise ValueError("Missing window score columns: " + ", ".join(sorted(missing)))
    windows = scores_df[columns].to_numpy(dtype=float)
    if not np.isfinite(windows).all():
        raise ValueError("Window scores and times must be finite")
    if (windows[:, 0] < 0).any() or (windows[:, 1] <= windows[:, 0]).any():
        raise ValueError("Window times must have 0 <= start < end")
    values = scores_df[list(SCORE_GROUPS)].to_numpy(dtype=float)
    if ((values < 0) | (values > 1)).any():
        raise ValueError("Window probabilities must be between 0 and 1")
    frame_count = ceil(duration_s / hop_s)
    starts = np.arange(frame_count, dtype=float) * hop_s
    ends = np.minimum((np.arange(frame_count, dtype=float) + 1) * hop_s, duration_s)
    # Sweep windows once; fsum prevents cumulative cancellation from breaking vocal ties.
    ordered = np.argsort(windows[:, 0], kind="stable")
    active: dict[int, np.ndarray] = {}
    expirations: list[tuple[float, int]] = []
    next_window = 0
    means = []
    for start, end in zip(starts, ends):
        while next_window < len(ordered) and windows[ordered[next_window], 0] < end:
            index = int(ordered[next_window])
            if windows[index, 1] > start:
                active[index] = values[index]
                heappush(expirations, (windows[index, 1], index))
            next_window += 1
        while expirations and expirations[0][0] <= start:
            _, index = heappop(expirations)
            del active[index]
        if not active:
            raise ValueError("Every audio frame must overlap at least one score window")
        means.append([fsum(row[column] for row in active.values()) / len(active)
                      for column in range(len(SCORE_GROUPS))])
    result = pd.DataFrame(means, columns=list(SCORE_GROUPS))
    result.insert(0, "end", ends)
    result.insert(0, "start", starts)
    return result


def frame_labels_from_scores(scores_df: pd.DataFrame, rms_db: np.ndarray, cfg: KeeperConfig) -> list[FrameLabel]:
    """Return RMS-gated frame labels followed by a centered majority-vote filter."""
    if rms_db.ndim != 1 or not np.isfinite(rms_db).all():
        raise ValueError("RMS values must be a one-dimensional finite array")
    frames = frame_scores_from_windows(scores_df, float(len(rms_db) * cfg.hop_s), cfg.hop_s)
    labels: list[FrameLabel] = []
    for rms, singing, speech in zip(rms_db, frames["singing"], frames["speech"]):
        if rms < cfg.silence_db:
            labels.append("silence")
        elif max(singing, speech) < cfg.voice_min:
            labels.append("instrument")
        elif singing >= speech:
            labels.append("singing")
        else:
            labels.append("speech")
    radius = cfg.mode_filter_frames // 2
    smoothed: list[FrameLabel] = []
    for index, original in enumerate(labels):
        counts = Counter(labels[max(0, index - radius):index + radius + 1])
        highest = max(counts.values())
        winners = [label for label, count in counts.items() if count == highest]
        smoothed.append(cast(FrameLabel, winners[0]) if len(winners) == 1 else original)
    return smoothed
