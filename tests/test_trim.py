"""Singing-score edge trimming, roll bounds, and drop warnings for every take type."""

from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from keeper.config import KeeperConfig
from keeper.trim import trim_takes
from keeper.types import CommandLabel, Take
from keeper.labels import MockLabelParser
import keeper.pipeline as pipeline


def _trim(scores: list[float], cfg: KeeperConfig | None = None) -> tuple[list[tuple[Take, CommandLabel]], list[str]]:
    """Return a trimmed four-second take from eight half-second singing scores."""
    take = Take("verse-a", "verse", "A", 0.0, 4.0, "memo.wav", "note", None)
    label = CommandLabel(True, "verse", "A", "note", 1.0)
    frames = pd.DataFrame({"start": [i * 0.5 for i in range(8)],
                           "end": [(i + 1) * 0.5 for i in range(8)], "singing": scores})
    result = trim_takes([(take, label)], frames, cfg or KeeperConfig(edge_trim=True))
    assert (take.start, take.end) == (0.0, 4.0)
    return result


def test_leading_trim_reapplies_pre_roll() -> None:
    """Return None after checking instrument-only leading frames are removed with bounded pre-roll."""
    takes, warnings = _trim([0.0, 0.0] + [0.5] * 6)
    assert (takes[0][0].start, takes[0][0].end) == (0.7, 4.0) and warnings == []


def test_trailing_trim_reapplies_post_roll() -> None:
    """Return None after checking trailing instrument-only frames are removed with bounded post-roll."""
    takes, warnings = _trim([0.5] * 5 + [0.0] * 3)
    assert (takes[0][0].start, takes[0][0].end) == (0.0, 3.0) and warnings == []


def test_internal_instrument_frames_are_untouched() -> None:
    """Return None after checking internal low-score frames do not split or shorten a take."""
    takes, _ = _trim([0.5] + [0.0] * 6 + [0.5])
    assert (takes[0][0].start, takes[0][0].end) == (0.0, 4.0)
    assert takes[0][0].note == "note" and takes[0][1].variant == "A"


def test_all_instrument_take_is_dropped_with_exact_warning() -> None:
    """Return None after checking takes with no threshold-level singing are dropped and logged."""
    assert _trim([0.0] * 8) == ([], ["take dropped: no singing detected (0.0-4.0)"])


def test_edge_trim_can_be_disabled_and_threshold_is_inclusive() -> None:
    """Return None after checking the off flag preserves takes and equality retains singing."""
    takes, _ = _trim([0.0] * 8, KeeperConfig(edge_trim=False))
    assert (takes[0][0].start, takes[0][0].end) == (0.0, 4.0)
    takes, warnings = _trim([0.0018] * 8)
    assert len(takes) == 1 and warnings == []


@pytest.mark.parametrize("section", ["chorus", "unlabeled", "rejected"])
def test_trim_applies_to_all_take_sections(section: str) -> None:
    """Return None after checking no section or command status bypasses edge trimming."""
    take = Take("test", section, "A", 0.0, 2.0, "memo.wav", None, None)
    label = CommandLabel(False, "unsorted", None, None, 1.0)
    frames = pd.DataFrame({"start": [0.0, 0.5, 1.0, 1.5], "end": [0.5, 1.0, 1.5, 2.0],
                           "singing": [0.0, 0.0, 0.5, 0.0]})
    result, warnings = trim_takes([(take, label)], frames, KeeperConfig(edge_trim=True))
    assert (result[0][0].start, result[0][0].end) == (0.7, 2.0) and warnings == []


@pytest.mark.parametrize("field,value", [("protect_speech_run_s", 0.0), ("protect_speech_run_s", float("inf")),
                                         ("trim_singing_min", -0.1), ("trim_singing_min", float("nan")),
                                         ("trim_singing_min", 1.1)])
def test_new_config_rejects_invalid_thresholds(field: str, value: float) -> None:
    """Return None after checking new duration and probability settings reject invalid values."""
    with pytest.raises(ValueError):
        replace(KeeperConfig(), **{field: value})


def test_pipeline_trims_commanded_and_unlabeled_takes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking trimming runs after both candidate sources and saves drop warnings."""
    source = tmp_path / "fixture"
    source.mkdir()
    (source / "meta.json").write_text('{"duration_s": 13.0}', encoding="utf-8")
    rms = source / "rms.json"
    rms.write_text(json.dumps([-20.0] * 26), encoding="utf-8")
    start = np.arange(26) * 0.5
    scores = source / "scores.csv"
    pd.DataFrame({"start": start, "end": start + 0.5, "singing": 0.0, "instrument": 0.8,
                  "speech": np.where((start >= 8) & (start < 9), 0.9, 0.1)}).to_csv(scores, index=False)
    commands = source / "commands.json"
    commands.write_text(json.dumps({"8.0": CommandLabel(True, "chorus", "A", None, 1.0).to_dict()}),
                         encoding="utf-8")
    result, _, warnings = pipeline.run_pipeline(source, MockLabelParser(commands), tmp_path / "result",
                                               cfg=KeeperConfig(edge_trim=True), scores_csv=scores,
                                               rms_json=rms, no_export=True)
    assert result.sections == {}
    assert warnings == ["take dropped: no singing detected (0.0-8.0)",
                        "take dropped: no singing detected (9.0-13.0)"]
    assert (tmp_path / "result" / "warnings.txt").read_text() == "\n".join(warnings) + "\n"
