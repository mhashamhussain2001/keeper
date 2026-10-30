"""Short sound island voting and silence boundary checks."""

from dataclasses import replace

import numpy as np
import pandas as pd

from keeper.config import KeeperConfig
from keeper.frames import frame_labels_from_scores, island_vote
from keeper.types import FrameLabel


def test_majority_wins() -> None:
    """Return None after checking a three-to-one island becomes all music without mutation."""
    labels: list[FrameLabel] = ["music", "music", "music", "speech"]
    assert island_vote(labels, KeeperConfig()) == ["music"] * 4
    assert labels[-1] == "speech"


def test_even_split_unchanged() -> None:
    """Return None after checking a tied island stays unchanged."""
    labels: list[FrameLabel] = ["music", "music", "speech", "speech"]
    assert island_vote(labels, KeeperConfig()) == labels


def test_long_island_unchanged() -> None:
    """Return None after checking an island exceeding the duration limit skips voting."""
    labels: list[FrameLabel] = ["music"] * 16 + ["speech"]
    assert island_vote(labels, KeeperConfig()) == labels


def test_silence_bounds_independent_islands() -> None:
    """Return None after checking silence is preserved between independently voted islands."""
    labels: list[FrameLabel] = ["silence"] + ["music"] * 3 + ["speech", "silence", "speech", "silence"]
    assert island_vote(labels, KeeperConfig()) == ["silence"] + ["music"] * 4 + ["silence", "speech", "silence"]


def test_exact_duration_and_vote_share_are_inclusive() -> None:
    """Return None after checking duration and winning-share thresholds include equality."""
    cfg = replace(KeeperConfig(), island_max_s=2.5)
    assert island_vote(["speech"] * 3 + ["music"] * 2, cfg) == ["speech"] * 5


def test_island_voting_precedes_mode_filter() -> None:
    """Return None after checking island voting removes a boundary leak before mode filtering."""
    cfg = KeeperConfig()
    starts = np.arange(6) * cfg.hop_s
    scores = pd.DataFrame({"start": starts, "end": starts + cfg.hop_s,
                           "singing": 0.1, "instrument": 0.1,
                           "speech": [0.1, 0.1, 0.1, 0.9, 0.9, 0.9]})
    labels, _ = frame_labels_from_scores(scores, np.array([-10.0] * 4 + [-80.0, -10.0]), cfg)
    assert labels == ["music"] * 4 + ["silence", "speech"]
