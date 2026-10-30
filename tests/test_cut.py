"""Exact command-to-take boundary fixtures from the Keeper specification."""

from keeper.config import KeeperConfig
from keeper.cut import cut_takes
from keeper.songmap import build_songmap
from keeper.types import CommandLabel, Region
from keeper.unlabeled import find_unlabeled


def test_spec_timeline_exact_boundaries() -> None:
    """Return None after checking both chorus options and the verse against the full specified timeline."""
    regions = [
        Region(0.0, 3.0, "silence", {}), Region(3.0, 15.0, "music", {}),
        Region(15.0, 17.0, "speech", {}), Region(17.0, 20.0, "music", {}),
        Region(20.0, 32.0, "music", {}), Region(32.0, 33.0, "silence", {}),
        Region(33.0, 40.0, "music", {}), Region(40.0, 42.0, "speech", {}),
        Region(42.0, 60.0, "music", {}), Region(60.0, 61.5, "speech", {}),
        Region(61.5, 70.0, "music", {}), Region(70.0, 72.0, "speech", {}),
    ]
    commands = {
        15.0: CommandLabel(True, "chorus", "A", None, 1.0),
        40.0: CommandLabel(True, "chorus", "B", None, 1.0),
        60.0: CommandLabel(False, "unsorted", None, None, 1.0),
        70.0: CommandLabel(True, "verse", None, None, 1.0),
    }
    takes, warnings = cut_takes(regions, commands, "memo.wav", KeeperConfig())
    assert warnings == []
    assert [(take.start, take.end) for take, _ in takes] == [(2.7, 15.0), (17.0, 40.0), (61.5, 70.0)]
    takes.extend(find_unlabeled(regions, [take for take, _ in takes], "memo.wav", KeeperConfig()))
    songmap = build_songmap(takes, "memo.wav")
    assert [(take.section, take.variant, take.start, take.end)
            for section_takes in songmap.sections.values() for take in section_takes] == [
        ("chorus", "A", 2.7, 15.0), ("chorus", "B", 17.0, 40.0),
        ("verse", "Take 1", 61.5, 70.0),
        ("unlabeled", "Take 1", 42.0, 60.0),
    ]


def test_command_without_preceding_music() -> None:
    """Return None after checking an unpaired command emits one warning and no take."""
    regions = [Region(0.0, 3.0, "silence", {}), Region(3.0, 4.0, "speech", {})]
    takes, warnings = cut_takes(regions, {3.0: CommandLabel(True, "chorus", "A", None, 1.0)},
                               "memo.wav", KeeperConfig())
    assert takes == []
    assert warnings == ["command at 3.0s had no preceding music"]


def test_gap_equal_to_limit_stops_walk() -> None:
    """Return None after checking a gap of exactly max_gap_s blocks earlier music."""
    regions = [Region(0.0, 5.0, "music", {}), Region(5.0, 9.0, "silence", {}),
               Region(9.0, 14.0, "music", {}), Region(14.0, 15.0, "speech", {})]
    takes, warnings = cut_takes(regions, {14.0: CommandLabel(True, "chorus", "A", None, 1.0)},
                               "memo.wav", KeeperConfig())
    assert warnings == []
    assert (takes[0][0].start, takes[0][0].end) == (9.0, 14.0)


def test_post_roll_does_not_reach_distant_command() -> None:
    """Return None after checking a short gap keeps only the configured post-roll duration."""
    regions = [Region(0.0, 5.0, "music", {}), Region(5.0, 7.0, "silence", {}),
               Region(7.0, 8.0, "speech", {})]
    takes, _ = cut_takes(regions, {7.0: CommandLabel(True, "verse", None, None, 1.0)},
                         "memo.wav", KeeperConfig())
    assert (takes[0][0].start, takes[0][0].end) == (0.0, 5.5)


def test_music_at_memo_start_ends_at_command() -> None:
    """Return None after checking music from zero stays inside the following command boundary."""
    command = Region(3.0, 4.0, "speech", {})
    regions = [Region(0.0, 3.0, "music", {}), command, Region(4.0, 6.0, "music", {})]
    label = CommandLabel(True, "chorus", "A", None, 1.0)

    takes, warnings = cut_takes(regions, {command.start: label}, "memo.wav", KeeperConfig())

    assert warnings == []
    assert len(takes) == 1
    take, take_label = takes[0]
    assert take.start == 0.0
    assert take.end == command.start
    assert take_label == label


def test_command_ending_at_memo_end_keeps_preceding_take() -> None:
    """Return None after checking a final command selects its preceding music without a trailing region."""
    cfg = KeeperConfig()
    music = Region(1.0, 3.0, "music", {})
    command = Region(3.0, 4.0, "speech", {})
    regions = [Region(0.0, 1.0, "silence", {}), music, command]
    label = CommandLabel(True, "verse", "A", "keep", 1.0)

    takes, warnings = cut_takes(regions, {command.start: label}, "memo.wav", cfg)

    assert warnings == []
    assert len(takes) == 1
    take, take_label = takes[0]
    assert take.start == music.start - cfg.pre_roll_s
    assert take.end == command.start
    assert (take.section, take.variant, take.note, take.source_file) == ("verse", "A", "keep", "memo.wav")
    assert take_label == label


def test_command_at_memo_start_has_no_preceding_take() -> None:
    """Return None after checking an initial command warns once and cannot select later music."""
    regions = [Region(0.0, 0.5, "speech", {}), Region(0.5, 3.0, "music", {})]
    label = CommandLabel(True, "chorus", "A", None, 1.0)

    takes, warnings = cut_takes(regions, {0.0: label}, "memo.wav", KeeperConfig())

    assert takes == []
    assert warnings == ["command at 0.0s had no preceding music"]


def test_short_music_rolls_stay_between_neighboring_commands() -> None:
    """Return None after checking overlapping roll margins cannot cross either neighboring command."""
    cfg = KeeperConfig()
    previous_command = Region(2.0, 2.5, "speech", {})
    music = Region(2.6, 2.7, "music", {})
    next_command = Region(2.8, 3.3, "speech", {})
    regions = [
        Region(0.0, 2.0, "music", {}), previous_command,
        Region(2.5, 2.6, "silence", {}), music,
        Region(2.7, 2.8, "silence", {}), next_command,
        Region(3.3, 5.0, "music", {}),
    ]
    commands = {
        previous_command.start: CommandLabel(True, "chorus", "A", None, 1.0),
        next_command.start: CommandLabel(True, "chorus", "B", None, 1.0),
    }
    assert music.end - music.start < cfg.pre_roll_s + cfg.post_roll_s

    takes, warnings = cut_takes(regions, commands, "memo.wav", cfg)

    assert warnings == []
    assert len(takes) == 2
    first_take, second_take = [take for take, _ in takes]
    assert (first_take.start, first_take.end) == (0.0, previous_command.start)
    assert (second_take.start, second_take.end) == (previous_command.end, next_command.start)
    assert first_take.end <= second_take.start
    assert previous_command.end <= second_take.start <= music.start
    assert music.end <= second_take.end <= next_command.start
