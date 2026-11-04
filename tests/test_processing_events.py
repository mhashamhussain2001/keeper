"""Observations preserve pipeline output and survive parallel completion and bounded publishing."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any

import numpy as np
import pytest
import soundfile as sf

from app.event_status import EventStatus
from keeper import pipeline
from keeper.events import EventReporter
from keeper.labels import MockLabelParser, MuseSparkLabelParser
from keeper.types import CommandLabel, Region
from test_pipeline_mock import _synthetic_inputs
from unittest.mock import MagicMock
from tests.test_spark import payload, response


def test_event_order_and_results_unchanged(tmp_path: Path) -> None:
    """Return None after comparing observed and callback-free runs, speech counts, and terminal data."""
    audio, scores, labels = _synthetic_inputs(tmp_path)
    events: list[dict[str, Any]] = []
    observed, regions, warnings = pipeline.run_pipeline(audio, MockLabelParser(labels), tmp_path / "observed",
                                                        scores_csv=scores, no_export=True, on_event=events.append)
    normal, _, normal_warnings = pipeline.run_pipeline(audio, MockLabelParser(labels), tmp_path / "normal",
                                                       scores_csv=scores, no_export=True)
    assert observed == normal and warnings == normal_warnings
    kinds = [event["type"] for event in events]
    assert kinds[:5] == ["received", "loading_done", "ast_start", "ast_progress", "regions_found"]
    assert kinds[-1] == "done" and events[-1]["data"]["n_takes"] == 2
    assert kinds.count("spark_result") == sum(region.kind == "speech" for region in regions) == 2
    assert kinds.count("take_cut") == 2
    assert [e["t"] for e in events] == sorted(e["t"] for e in events)
    assert events[0]["data"]["sample_rate"] == 16000
    assert events[0]["data"]["channels"] == 1 and "filename" not in events[0]["data"]
    assert all(e["data"]["cached"] is False for e in events if e["type"] == "spark_result")


@pytest.mark.parametrize("available", [True, False])
def test_received_uses_native_metadata_or_omits_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, available: bool,
) -> None:
    """Return None after checking stereo native rates never become the mono 16 kHz working rate."""
    audio, scores, labels = _synthetic_inputs(tmp_path)
    samples, _ = sf.read(audio, dtype="float32")
    stereo = np.repeat(samples[:, None], 2, axis=1).repeat(3, axis=0)
    sf.write(audio, stereo, 48000)
    if not available:
        def unavailable(source: Path) -> tuple[int, int]:
            """Return no metadata because this decoder has no native-format probe."""
            raise OSError("No probe")

        monkeypatch.setattr(pipeline, "audio_format", unavailable)
    events: list[dict[str, Any]] = []
    pipeline.run_pipeline(audio, MockLabelParser(labels), tmp_path / "native", scores_csv=scores,
                          no_export=True, on_event=events.append)
    received = events[0]["data"]
    assert received == ({"duration_s": 30.0, "sample_rate": 48000, "channels": 2} if available else {"duration_s": 30.0})


def test_failed_event_and_cli_without_callback(tmp_path: Path) -> None:
    """Return None after checking failures are observable and the legacy CLI remains callback-free."""
    audio, scores, labels = _synthetic_inputs(tmp_path)
    events: list[dict[str, Any]] = []
    with pytest.raises(ValueError, match="Output directory"):
        pipeline.run_pipeline(audio, MockLabelParser(labels), audio.parent, on_event=events.append)
    assert events[-1]["type"] == "failed"
    assert pipeline.main([str(audio), "--mock-labels", str(labels), "--out", str(tmp_path / "cli"),
                          "--scores-csv", str(scores), "--no-export"]) == 0


def test_parallel_spark_reports_completion_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after proving a later region is reported before a blocked earlier parallel call."""
    audio, scores, _ = _synthetic_inputs(tmp_path)
    parser = MuseSparkLabelParser(tmp_path / "spark_cache.json")
    second_finished = Event()

    def parse(samples: np.ndarray, sr: int, region: Region) -> CommandLabel:
        """Return a deterministic command with the first call held until the second finishes."""
        if region.start < 15:
            assert second_finished.wait(5)
        return CommandLabel(True, "chorus", None, None, .92, "that's the chorus", intent="file")

    def callback(event: dict[str, Any]) -> None:
        """Return None after recording an observation and releasing the earlier call when appropriate."""
        events.append(event)
        if event["type"] == "spark_result" and event["data"]["start"] > 15:
            second_finished.set()

    events: list[dict[str, Any]] = []
    monkeypatch.setattr(parser, "parse", parse)
    monkeypatch.setattr(parser, "preflight", lambda clips: None)
    pipeline.run_pipeline(audio, parser, tmp_path / "parallel", scores_csv=scores, no_export=True, on_event=callback)
    results = [event for event in events if event["type"] == "spark_result"]
    assert len(results) == 2 and results[0]["data"]["start"] > results[1]["data"]["start"]
    assert results[0]["data"]["heard"] == "that's the chorus"


def test_concurrent_status_appends_and_cap(tmp_path: Path) -> None:
    """Return None after checking concurrent append integrity, valid JSON, monotonic times, and terminal retention."""
    status = EventStatus(tmp_path)
    reporter = EventReporter(status.event)
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda i: reporter.emit("spark_result", index=i), range(250)))
    saved = json.loads((tmp_path / "status.json").read_text())
    assert {event["data"]["index"] for event in saved["events"]} == set(range(250))
    assert [event["t"] for event in saved["events"]] == sorted(event["t"] for event in saved["events"])
    for i in range(500):
        reporter.emit("take_cut", index=i)
    reporter.emit("done", n_takes=500)
    status.finish("done")
    saved = json.loads((tmp_path / "status.json").read_text())
    assert len(saved["events"]) == 400 and saved["events"][-1]["type"] == "done"


def test_modal_writes_and_commits_coalesce(tmp_path: Path) -> None:
    """Return None after measuring that burst events and final status share a one-second write budget."""
    commits: list[float] = []
    status = EventStatus(tmp_path, lambda: commits.append(monotonic()))
    status.progress("loading audio")
    for i in range(120):
        status.event({"t": float(i), "type": "spark_result", "data": {"index": i}})
    assert len(commits) == 1
    status.event({"t": 121.0, "type": "done", "data": {}})
    status.finish("done")
    assert len(commits) == 2 and commits[1] - commits[0] >= 1.0
    saved = json.loads((tmp_path / "status.json").read_text())
    assert saved["state"] == "done" and len(saved["events"]) == 121


def test_cached_spark_events_retain_heard_text(tmp_path: Path) -> None:
    """Return None after checking event cache flags against real parser cache hits in a second run."""
    audio, scores, _ = _synthetic_inputs(tmp_path)
    client = MagicMock()
    client.chat.completions.create.return_value = response(json.dumps(payload()))
    cache = tmp_path / "spark_cache.json"
    for index in range(2):
        events: list[dict[str, Any]] = []
        parser = MuseSparkLabelParser(cache, client=client)
        pipeline.run_pipeline(audio, parser, tmp_path / str(index), scores_csv=scores, no_export=True, on_event=events.append)
        results = [e["data"] for e in events if e["type"] == "spark_result"]
        assert len(results) == 2 and all(e["cached"] == bool(index) for e in results)
        assert all(e["heard"] == "that's the verse" for e in results)
    assert client.chat.completions.create.call_count == 2
