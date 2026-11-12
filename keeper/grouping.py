"""Group takes that play the same musical idea, using which notes sound over time."""

from dataclasses import replace
from string import ascii_uppercase

import numpy as np

from keeper.config import KeeperConfig
from keeper.types import CommandLabel, Take

CHROMA_HOP = 1600  # 0.1 s at 16 kHz.


def _chroma(y: np.ndarray, take: Take, sr: int) -> np.ndarray:
    """Return unit-length 12-note energy per 0.1 s for one take."""
    import librosa

    segment = y[round(take.start * sr):round(take.end * sr)]
    chroma = librosa.feature.chroma_stft(y=segment, sr=sr, n_fft=4096, hop_length=CHROMA_HOP, tuning=0.0)
    return chroma / (np.linalg.norm(chroma, axis=0, keepdims=True) + 1e-9)


def _distance(a: np.ndarray, b: np.ndarray) -> float:
    """Return how badly the shorter take fits anywhere inside the longer one (0 identical, ~0.3 unrelated)."""
    import librosa

    query, reference = (a, b) if a.shape[1] <= b.shape[1] else (b, a)
    cost, _ = librosa.sequence.dtw(X=query, Y=reference, subseq=True, metric="cosine")
    return float(cost[-1].min() / query.shape[1])


def group_takes(
    takes: list[tuple[Take, CommandLabel]], y: np.ndarray, cfg: KeeperConfig,
) -> list[tuple[Take, CommandLabel]]:
    """Return takes with repeated unlabeled attempts moved into sections named Idea A, Idea B in order of first appearance."""
    loose = [index for index, (_, label) in enumerate(takes) if label.section == "unlabeled"]
    if not cfg.group_takes or len(y) == 0 or len(loose) < 2:
        return list(takes)
    from sklearn.cluster import AgglomerativeClustering

    try:
        features = [_chroma(y, takes[index][0], cfg.sample_rate) for index in loose]
        # ponytail: threshold fit on one memo; refit on the labelled set. Cost is O(n^2) DTW pairs (fine below ~60 takes).
        matrix = np.array([[_distance(a, b) if a is not b else 0.0 for b in features] for a in features])
        if not np.isfinite(matrix).all():
            raise ValueError("non-finite take distance")
    except Exception:  # Grouping is a bonus: any failure leaves takes ungrouped instead of failing the memo.
        return list(takes)
    clusters = AgglomerativeClustering(n_clusters=None, metric="precomputed", linkage="average",
                                       distance_threshold=cfg.group_distance_max).fit_predict(matrix)
    members: dict[int, list[int]] = {}
    for position, cluster in enumerate(clusters):
        members.setdefault(int(cluster), []).append(loose[position])
    result = list(takes)
    letters = iter(ascii_uppercase)
    for indices in sorted(members.values(), key=min):
        if len(indices) < 2:
            continue
        section = f"Idea {next(letters)}"
        for index in indices:
            take, label = takes[index]
            result[index] = (take, replace(label, section=section, variant=None))
    return result
