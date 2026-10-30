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
    groups = [*SCORE_GROUPS, "music"]
    scores_df = scores_df.copy()
    if {"singing", "instrument"} <= set(scores_df.columns):
        scores_df["music"] = scores_df[["singing", "instrument"]].max(axis=1)
    columns = ["start", "end", *groups]
    missing = set(columns) - set(scores_df.columns)
    if missing:
        raise ValueError("Missing window score columns: " + ", ".join(sorted(missing)))
    windows = scores_df[columns].to_numpy(dtype=float)
    if not np.isfinite(windows).all():
        raise ValueError("Window scores and times must be finite")
    if (windows[:, 0] < 0).any() or (windows[:, 1] <= windows[:, 0]).any():
        raise ValueError("Window times must have 0 <= start < end")
    values = scores_df[groups].to_numpy(dtype=float)
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
                      for column in range(len(groups))])
    result = pd.DataFrame(means, columns=groups)
    result.insert(0, "end", ends)
    result.insert(0, "start", starts)
    return result


def silence_gate_db(rms_db: np.ndarray, cfg: KeeperConfig) -> float:
    """Return the absolute or clamped relative silence gate for the memo."""
    if rms_db.ndim != 1 or not np.isfinite(rms_db).all():
        raise ValueError("RMS values must be a one-dimensional finite array")
    if cfg.silence_mode == "absolute" or not rms_db.size:
        return float(cfg.silence_db)
    floor = float(np.percentile(rms_db, cfg.noise_floor_pct))
    if np.median(rms_db) - floor < cfg.min_dynamic_range_db:
        return float(cfg.silence_db)
    return float(np.clip(floor + cfg.silence_margin_db, *cfg.gate_clamp_db))


def island_vote(labels: list[FrameLabel], cfg: KeeperConfig) -> list[FrameLabel]:
    """Return labels with short non-silence islands assigned their qualifying majority class."""
    voted = labels.copy()
    start = 0
    while start < len(labels):
        if labels[start] == "silence":
            start += 1
            continue
        end = start + 1
        while end < len(labels) and labels[end] != "silence":
            end += 1
        if (end - start) * cfg.hop_s <= cfg.island_max_s:
            winner, count = Counter(labels[start:end]).most_common(1)[0]
            if count / (end - start) >= cfg.island_vote_min:
                voted[start:end] = [winner] * (end - start)
        start = end
    return voted


def frame_labels_from_scores(
    scores_df: pd.DataFrame, rms_db: np.ndarray, cfg: KeeperConfig,
) -> tuple[list[FrameLabel], float]:
    """Return gated labels after island voting and mode filtering, plus the chosen gate."""
    gate = silence_gate_db(rms_db, cfg)
    frames = frame_scores_from_windows(scores_df, float(len(rms_db) * cfg.hop_s), cfg.hop_s)
    labels: list[FrameLabel] = []
    for rms, speech in zip(rms_db, frames["speech"]):
        if rms < gate:
            labels.append("silence")
        elif speech >= cfg.speech_min:
            labels.append("speech")
        else:
            labels.append("music")
    labels = island_vote(labels, cfg)
    radius = cfg.mode_filter_frames // 2
    smoothed: list[FrameLabel] = []
    for index, original in enumerate(labels):
        counts = Counter(labels[max(0, index - radius):index + radius + 1])
        highest = max(counts.values())
        winners = [label for label, count in counts.items() if count == highest]
        smoothed.append(cast(FrameLabel, winners[0]) if len(winners) == 1 else original)
    return smoothed, gate
