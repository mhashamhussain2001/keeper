"""Takes that play the same notes are grouped; a unique take stays loose."""

import numpy as np

from keeper.config import KeeperConfig
from keeper.grouping import group_takes
from keeper.types import CommandLabel, Take

SR = 16000


def _melody(freqs: list[float], note_s: float = 0.6, repeat: int = 3) -> np.ndarray:
    """Return a simple repeated note sequence."""
    t = np.arange(int(note_s * SR)) / SR
    return np.concatenate([0.3 * np.sin(2 * np.pi * f * t) for f in freqs * repeat]).astype(np.float32)


def test_same_idea_grouped_unique_take_left_alone() -> None:
    """Return None after checking A, A (shorter), B, B and a unique C become Idea A, Idea B and one loose take."""
    a, b, c = [220.0, 277.2, 329.6], [196.0, 233.1, 392.0], [311.1, 415.3, 466.2]
    parts = [_melody(a), _melody(a, repeat=2), _melody(b), _melody(b, repeat=2), _melody(c)]
    y = np.concatenate([np.concatenate([p, np.zeros(2 * SR, dtype=np.float32)]) for p in parts])
    takes, cursor = [], 0.0
    for p in parts:
        end = cursor + len(p) / SR
        takes.append((Take("", "unlabeled", f"Take {len(takes) + 1}", cursor, end, "m.wav", None, None),
                      CommandLabel(False, "unlabeled", f"Take {len(takes) + 1}", None, 0.0)))
        cursor = end + 2
    sections = [label.section for _, label in group_takes(takes, y, KeeperConfig())]
    assert sections == ["Idea A", "Idea A", "Idea B", "Idea B", "unlabeled"]


def test_disabled_flag_leaves_takes_alone() -> None:
    """Return None after checking the off flag changes nothing."""
    takes = [(Take("", "unlabeled", "Take 1", 0, 1, "m", None, None), CommandLabel(False, "unlabeled", "Take 1", None, 0.0))] * 2
    assert group_takes(takes, np.zeros(SR, dtype=np.float32), KeeperConfig(group_takes=False)) == takes


def test_constant_tone_and_silent_takes_do_not_raise() -> None:
    """Return None after checking degenerate audio (flat tone, silence) never breaks the pipeline."""
    y = np.concatenate([np.full(3 * SR, 0.1, dtype=np.float32), np.zeros(3 * SR, dtype=np.float32)])
    takes = [(Take("", "unlabeled", f"Take {i}", 3.0 * i, 3.0 * i + 3.0, "m", None, None),
              CommandLabel(False, "unlabeled", f"Take {i}", None, 0.0)) for i in range(2)]
    assert len(group_takes(takes, y, KeeperConfig())) == 2
