"""Repeated command selection and unchanged decoder warmup regression checks."""

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from keeper.audio_io import load_audio, warm_librosa
from keeper.commands import chain_commands
from keeper.config import KeeperConfig
from keeper.cut import cut_takes
from keeper.types import CommandLabel, Region


def test_memo2_bridge_chain() -> None:
    """Return None after checking the two bridge commands select the following truth performance."""
    regions = [Region(s, e, kind, {}) for s, e, kind in [
        (285, 287, "speech"), (287, 288.5, "silence"), (288.5, 290, "speech"),
        (290, 291, "silence"), (291, 305.5, "music"), (305.5, 310, "silence")]]
    commands = {285: CommandLabel(True, "bridge", None, None, .96, "Okay, bridge idea", "next"),
                288.5: CommandLabel(True, "bridge", None, None, .92, "Idea for a bridge")}
    chained, labels = chain_commands(regions, commands)
    assert (chained[0].start, chained[0].end, labels[285].target) == (285, 290, "next")
    assert len(labels) == 1 and 288.5 not in labels
    takes, warnings = cut_takes(regions, commands, "memo2.wav", KeeperConfig())
    assert len(takes) == 1 and not warnings
    take = takes[0][0]
    assert take.section == "bridge" and take.start >= 290 and take.end == 305.5
    assert min(take.end, 305.10) - max(take.start, 294.84) >= (305.10 - 294.84) / 2
    takes, _ = cut_takes(regions, commands, "memo2.wav", KeeperConfig(merge_adjacent_commands=False))
    assert not takes
    assert regions[0].end == 287 and commands[288.5].target == "previous"


@pytest.mark.parametrize("middle,section,is_command", [("music", "bridge", True),
                         ("silence", "verse", True), ("silence", "bridge", False)])
def test_chaining_stops_at_music_different_section_or_noncommand(middle: str, section: str, is_command: bool) -> None:
    """Return None after checking music and unrelated speech prevent a command chain."""
    regions = [Region(0, 1, "speech", {}), Region(1, 2, middle, {}), Region(2, 3, "speech", {})]
    commands = {0: CommandLabel(True, "bridge", None, None, 1),
                2: CommandLabel(is_command, section, None, None, 1)}
    chained, labels = chain_commands(regions, commands)
    assert chained == regions and labels == commands


def test_chain_three_commands_preserves_any_next_and_last_metadata() -> None:
    """Return None after checking a multi-command chain retains its next direction and final note."""
    regions = [Region(i, i + 1, "speech", {}) for i in range(3)]
    commands = {i: CommandLabel(True, "chorus", None, str(i), 1, str(i), "next" if i == 1 else "previous") for i in range(3)}
    chained, labels = chain_commands(regions, commands)
    assert len(chained) == 1 and chained[0].end == 3
    assert labels[0].target == "next" and labels[0].note == "2" and labels[0].heard == "0; 1; 2"


def test_warmup_preserves_loader_samples(tmp_path: Path) -> None:
    """Return None after checking priming librosa changes neither stereo resampling nor PCM samples."""
    path = tmp_path / "stereo.wav"
    rng = np.random.default_rng(2)
    sf.write(path, rng.uniform(-.1, .1, (4410, 2)).astype(np.float32), 44100, subtype="FLOAT")
    before = load_audio(path, 16000)
    assert warm_librosa() >= 0
    np.testing.assert_array_equal(load_audio(path, 16000), before)
