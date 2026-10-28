"""Merging and minimum-duration smoothing of activity regions."""

from dataclasses import replace
from math import isfinite

from keeper.config import FRAME_KINDS, KeeperConfig
from keeper.types import FrameLabel, Region


def merge_frames(labels: list[FrameLabel], hop_s: float) -> list[Region]:
    """Return contiguous regions tiling the frame grid from zero through len(labels) * hop_s."""
    if not isfinite(hop_s) or hop_s <= 0:
        raise ValueError("hop_s must be positive and finite")
    regions: list[Region] = []
    for index, kind in enumerate(labels):
        if kind not in FRAME_KINDS:
            raise ValueError(f"Unknown frame kind: {kind}")
        end = float((index + 1) * hop_s)
        if regions and regions[-1].kind == kind:
            regions[-1].end = end
        else:
            regions.append(Region(float(index * hop_s), end, kind, {}))
    return regions


def _remerge(regions: list[Region]) -> list[Region]:
    """Return regions with adjacent identical kinds merged and duration-weighted scores."""
    merged: list[Region] = []
    for region in regions:
        if merged and merged[-1].kind == region.kind:
            previous = merged[-1]
            left_duration, right_duration = previous.end - previous.start, region.end - region.start
            keys = previous.mean_scores.keys() & region.mean_scores.keys()
            previous.mean_scores = {
                key: float((previous.mean_scores[key] * left_duration + region.mean_scores[key] * right_duration)
                           / (left_duration + right_duration)) for key in keys
            }
            previous.end = region.end
        else:
            merged.append(replace(region, mean_scores=dict(region.mean_scores)))
    return merged


def absorb_short(regions: list[Region], cfg: KeeperConfig) -> list[Region]:
    """Return regions after repeatedly absorbing short regions into their longer neighbor."""
    result = _remerge(regions)
    while len(result) > 1:
        for index, region in enumerate(result):
            if region.end - region.start >= cfg.min_region_s[region.kind]:
                continue
            neighbors = result[max(0, index - 1):index] + result[index + 1:index + 2]
            # Strict comparison chooses the left neighbor on equal lengths.
            neighbor = neighbors[0]
            if len(neighbors) > 1 and neighbors[1].end - neighbors[1].start > neighbor.end - neighbor.start:
                neighbor = neighbors[1]
            region.kind = neighbor.kind
            result = _remerge(result)
            break
        else:
            break
        # Each relabel merges with a neighbor, so every pass reduces region count.
    return result
