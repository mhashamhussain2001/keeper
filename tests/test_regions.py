"""Region tiling and terminating short-region absorption tests."""

from keeper.config import KeeperConfig
from keeper.regions import absorb_short, merge_frames
from keeper.types import FrameLabel, Region


def test_regions_tile_exactly() -> None:
    """Return None after checking contiguous frame regions cover the complete memo."""
    labels: list[FrameLabel] = ["silence"] * 2 + ["singing"] * 6 + ["speech"] * 2
    regions = merge_frames(labels, 0.5)
    assert [(region.start, region.end, region.kind) for region in regions] == [
        (0.0, 1.0, "silence"), (1.0, 4.0, "singing"), (4.0, 5.0, "speech"),
    ]
    assert regions[0].start == 0.0 and regions[-1].end == len(labels) * 0.5
    assert all(left.end == right.start for left, right in zip(regions, regions[1:]))


def test_singing_blip_absorbed_inside_speech() -> None:
    """Return None after checking a short singing island becomes surrounding speech."""
    labels: list[FrameLabel] = ["speech"] * 4 + ["singing"] + ["speech"] * 4
    original = merge_frames(labels, 0.5)
    regions = absorb_short(original, KeeperConfig())
    assert [(region.start, region.end, region.kind) for region in regions] == [(0.0, 4.5, "speech")]
    assert original[1].kind == "singing"


def test_alternating_short_regions_terminate() -> None:
    """Return None after checking alternating sub-threshold labels converge to a complete tiling."""
    labels: list[FrameLabel] = ["singing", "instrument"] * 50
    regions = absorb_short(merge_frames(labels, 0.5), KeeperConfig())
    assert len(regions) < len(labels)
    assert regions[0].start == 0.0 and regions[-1].end == 50.0
    assert all(left.end == right.start for left, right in zip(regions, regions[1:]))
    assert all(region.end - region.start >= KeeperConfig().min_region_s[region.kind] for region in regions)


def test_longer_neighbor_wins() -> None:
    """Return None after checking absorption prefers the longer neighboring region."""
    regions = [Region(0.0, 3.0, "speech", {}), Region(3.0, 3.5, "singing", {}),
               Region(3.5, 7.5, "instrument", {})]
    result = absorb_short(regions, KeeperConfig())
    assert [(region.start, region.end, region.kind) for region in result] == [
        (0.0, 3.0, "speech"), (3.0, 7.5, "instrument"),
    ]


def test_equal_neighbors_choose_left() -> None:
    """Return None after checking equal neighboring lengths choose the earlier kind."""
    regions = [Region(0.0, 3.0, "speech", {}), Region(3.0, 3.5, "singing", {}),
               Region(3.5, 6.5, "instrument", {})]
    assert absorb_short(regions, KeeperConfig())[0].end == 3.5


def test_single_short_region_and_empty_input() -> None:
    """Return None after checking neighborless regions are retained and empty input stays empty."""
    assert absorb_short([], KeeperConfig()) == []
    region = Region(0.0, 0.1, "singing", {})
    assert absorb_short([region], KeeperConfig()) == [region]
