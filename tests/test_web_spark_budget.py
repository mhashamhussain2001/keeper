"""Keep CLI costs unchanged while completing long web memos within a bounded speech prefix."""

from pathlib import Path

import numpy as np
import pytest

from app.spark_budget import WebLabelParser, web_spark_budget
from keeper.config import KeeperConfig
from keeper.labels import MuseSparkLabelParser
from keeper.pipeline import run_pipeline
from keeper.types import CommandLabel, Region
from test_pipeline_mock import _synthetic_inputs


@pytest.mark.parametrize("duration,expected", [(60.0, 40), (600.0, 40), (601.0, 44), (1200.0, 80), (1800.0, 120), (3600.0, 120)])
def test_web_call_budget_scale(duration: float, expected: int) -> None:
    """Return None after checking duration-based web budgets while the CLI stays at forty calls."""
    assert web_spark_budget(duration) == expected and KeeperConfig().max_spark_calls == 40


def test_web_regions_are_capped_in_chronological_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after verifying only the earliest speech prefix reaches the unchanged Spark parser."""
    parser = WebLabelParser(tmp_path / "cache.json", 600.0)
    samples = np.zeros(16000, dtype=np.float32)
    regions = [Region(float(index), float(index + 1), "speech", {}) for index in range(43)]
    clips = [(samples, 16000, region) for region in reversed(regions)]
    called = []

    def parse(self: MuseSparkLabelParser, audio: np.ndarray, sr: int, region: Region) -> CommandLabel:
        """Return a non-command after recording only this synthetic region's time."""
        called.append(region.start)
        return CommandLabel(False, "unsorted", None, None, 0.0, intent="none")

    monkeypatch.setattr(MuseSparkLabelParser, "parse", parse)
    parser.preflight(clips)
    for region in regions:
        assert parser.parse(samples, 16000, region).intent == "none"
    assert called == [float(index) for index in range(40)]
    assert len(parser.skipped_regions) == 3


def test_web_memo_finishes_and_records_skipped_speech(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking over-cap speech becomes none with a saved warning and real event."""
    audio, scores, _ = _synthetic_inputs(tmp_path)
    parser = WebLabelParser(tmp_path / "cache.json", 600.0)
    parser.max_spark_calls = 1

    def parse(self: MuseSparkLabelParser, samples: np.ndarray, sr: int, region: Region) -> CommandLabel:
        """Return a file command for one synthetic speech region without a model call."""
        return CommandLabel(True, "verse", None, None, .9, intent="file")

    monkeypatch.setattr(MuseSparkLabelParser, "parse", parse)
    events = []
    _, _, warnings = run_pipeline(audio, parser, tmp_path / "out", scores_csv=scores, no_export=True, on_event=events.append)
    assert events[-1]["type"] == "done"
    assert next(event["data"] for event in events if event["type"] == "spark_limit") == {"n_skipped": 1, "cap": 1}
    assert any("budget reached" in warning for warning in warnings)
    assert "budget reached" in (tmp_path / "out" / "warnings.txt").read_text()


def test_exhausted_retry_budget_becomes_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after converting request-budget exhaustion into an unfiled region without increasing spend."""
    parser = WebLabelParser(tmp_path / "cache.json", 600.0)
    region = Region(0.0, 1.0, "speech", {})
    samples = np.zeros(16000, dtype=np.float32)
    parser.preflight([(samples, 16000, region)])

    def exhausted(self: MuseSparkLabelParser, audio: np.ndarray, sr: int, speech: Region) -> CommandLabel:
        """Return no command because this synthetic parser has used its allowed request budget."""
        raise RuntimeError("Spark cost guard: budget reached")

    monkeypatch.setattr(MuseSparkLabelParser, "parse", exhausted)
    assert parser.parse(samples, 16000, region).intent == "none"
    assert len(parser.skipped_regions) == 1
