"""Optional singing-score trimming of take edges without changing internal content."""

from dataclasses import replace

import numpy as np
import pandas as pd

from keeper.config import KeeperConfig
from keeper.types import CommandLabel, Take


def trim_takes(
    takes: list[tuple[Take, CommandLabel]], frame_scores: pd.DataFrame, cfg: KeeperConfig,
) -> tuple[list[tuple[Take, CommandLabel]], list[str]]:
    """Return edge-trimmed take/command pairs and warnings for takes with no detected singing."""
    if not cfg.edge_trim:
        return list(takes), []
    values = frame_scores[["start", "end", "singing"]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values[:, 0] < 0).any() or (values[:, 1] <= values[:, 0]).any():
        raise ValueError("Trim frames must have finite 0 <= start < end and finite singing scores")
    if ((values[:, 2] < 0) | (values[:, 2] > 1)).any():
        raise ValueError("Trim singing scores must be probabilities")
    result: list[tuple[Take, CommandLabel]] = []
    warnings: list[str] = []
    for take, label in takes:
        singing = values[(values[:, 0] < take.end) & (values[:, 1] > take.start)
                         & (values[:, 2] >= cfg.trim_singing_min)]
        if not len(singing):
            warnings.append(f"take dropped: no singing detected ({take.start}-{take.end})")
            continue
        start = float(max(take.start, singing[:, 0].min() - cfg.pre_roll_s))
        end = float(min(take.end, singing[:, 1].max() + cfg.post_roll_s))
        result.append((replace(take, start=start, end=end), label))
    return result, warnings
