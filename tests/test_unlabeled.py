"""Unclaimed music selection, gap limits, and roll clamping checks."""

from keeper.config import KeeperConfig
from keeper.types import Region, Take
from keeper.unlabeled import find_unlabeled


def test_unlabeled_stretch_and_time_order() -> None:
    """Return None after checking music joins short silence and variants follow chronological order."""
    regions = [Region(0.0, 1.0, "speech", {}), Region(1.0, 3.0, "music", {}),
               Region(3.0, 4.0, "silence", {}), Region(4.0, 6.0, "music", {}),
               Region(6.0, 7.0, "speech", {}), Region(7.0, 11.0, "music", {})]
    takes = find_unlabeled(regions, [], "memo.wav", KeeperConfig())
    assert [(take.section, take.variant, take.start, take.end) for take, _ in takes] == [
        ("unlabeled", "Take 1", 1.0, 6.0), ("unlabeled", "Take 2", 7.0, 11.0),
    ]


def test_overlap_drops_entire_stretch() -> None:
    """Return None after checking any command-take overlap excludes the whole candidate."""
    regions = [Region(0.0, 8.0, "music", {})]
    command = Take("chorus-a", "chorus", "A", 2.0, 4.0, "memo.wav", None, None)
    assert find_unlabeled(regions, [command], "memo.wav", KeeperConfig()) == []


def test_minimum_counts_music_only() -> None:
    """Return None after checking long intervening silence cannot satisfy minimum music duration."""
    regions = [Region(0.0, 1.0, "music", {}), Region(1.0, 4.0, "silence", {}),
               Region(4.0, 5.0, "music", {})]
    assert find_unlabeled(regions, [], "memo.wav", KeeperConfig()) == []


def test_long_silence_splits_and_rolls_clamp() -> None:
    """Return None after checking silence at the gap limit splits takes with bounded rolls."""
    regions = [Region(0.0, 0.1, "silence", {}), Region(0.1, 3.1, "music", {}),
               Region(3.1, 7.1, "silence", {}), Region(7.1, 10.1, "music", {}),
               Region(10.1, 10.2, "silence", {})]
    takes = find_unlabeled(regions, [], "memo.wav", KeeperConfig())
    assert [(take.start, take.end) for take, _ in takes] == [(0.0, 3.6), (6.8, 10.2)]
