"""Offline directional cutting, scope validation, and concurrent Spark budget checks."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from keeper.config import KeeperConfig
from keeper.cut import cut_takes
from keeper.labels import MuseSparkLabelParser, spark_command, validate_spark
from keeper.types import CommandLabel, Region
from tests.test_spark import payload, response


def command(target: str = "previous", scope: str = "whole") -> CommandLabel:
    """Return a command selecting a verse in the requested direction and scope."""
    return CommandLabel(True, "verse", None, None, 1.0, target=target, scope=scope)


def test_forward_gaps_stop_speech_and_no_double_claim() -> None:
    """Return None after checking forward gaps, rolls, speech stop, and a rejected backward claim."""
    regions = [Region(s, e, k, {}) for s, e, k in [
        (0, 1, "speech"), (1, 1.1, "silence"), (1.1, 3, "music"),
        (3, 4, "silence"), (4, 5, "music"), (5, 5.1, "silence"),
        (5.1, 6, "speech"), (6, 9, "music")]]
    takes, warnings = cut_takes(regions, {0: command("next"), 5.1: command()}, "memo.wav", KeeperConfig())
    assert [(take.start, take.end) for take, _ in takes] == [(1, 5.1)]
    assert len(warnings) == 1 and "already claimed" in warnings[0]


@pytest.mark.parametrize("gap", [4.0, 5.0])
def test_forward_long_gap_stops_before_later_music(gap: float) -> None:
    """Return None after checking silence at or beyond the gap limit stops a forward walk."""
    regions = [Region(0, 1, "speech", {}), Region(1, 3, "music", {}),
               Region(3, 3 + gap, "silence", {}), Region(3 + gap, 10, "music", {})]
    takes, warnings = cut_takes(regions, {0: command("next")}, "memo.wav", KeeperConfig())
    assert [(take.start, take.end) for take, _ in takes] == [(1, 3)]
    assert not warnings


def test_forward_end_of_memo_and_flag_off() -> None:
    """Return None after checking final music clamps to memo end and the disabled flag walks backward."""
    regions = [Region(0, 1, "speech", {}), Region(1, 3, "music", {})]
    takes, warnings = cut_takes(regions, {0: command("next")}, "memo.wav", KeeperConfig())
    assert (takes[0][0].start, takes[0][0].end) == (1, 3) and not warnings
    takes, warnings = cut_takes(regions, {0: command("next")}, "memo.wav", KeeperConfig(support_target_next=False))
    assert not takes and "no preceding music" in warnings[0]


@pytest.mark.parametrize("target", ["previous", "next"])
def test_last_attempt_keeps_last_music_after_pause(target: str) -> None:
    """Return None after checking only the final attempt remains in either collection direction."""
    regions = [Region(0, 1, "speech", {}), Region(1, 3, "music", {}),
               Region(3, 3.5, "silence", {}), Region(3.5, 5, "music", {}), Region(5, 6, "speech", {})]
    key = 0 if target == "next" else 5
    takes, warnings = cut_takes(regions, {key: command(target, "last_attempt")}, "memo.wav", KeeperConfig())
    assert (takes[0][0].start, takes[0][0].end) == (3.5, 5) and not warnings
    takes, _ = cut_takes(regions, {key: command(target, "last_attempt")}, "memo.wav", KeeperConfig(support_last_attempt=False))
    assert (takes[0][0].start, takes[0][0].end) == (1, 5)


def test_last_attempt_without_pause_keeps_whole_and_warns() -> None:
    """Return None after checking uninterrupted music is preserved with the specified warning."""
    regions = [Region(0, 5, "music", {}), Region(5, 6, "speech", {})]
    takes, warnings = cut_takes(regions, {5: command(scope="last_attempt")}, "memo.wav", KeeperConfig())
    assert (takes[0][0].start, takes[0][0].end) == (0, 5)
    assert warnings == ["last attempt requested but no pause found"]


def test_scope_validation_default_and_cache_version(tmp_path: Path) -> None:
    """Return None after checking scope defaults, validation, mapping, and separate prompt cache keys."""
    assert spark_command(validate_spark(json.dumps(payload()))).scope == "whole"
    assert spark_command(validate_spark(json.dumps({**payload(), "scope": "last_attempt"}))).scope == "last_attempt"
    with pytest.raises(ValueError, match="scope"):
        validate_spark(json.dumps({**payload(), "scope": "last"}))
    audio, region = np.zeros(16000, dtype=np.float32), Region(0, 1, "speech", {})
    old = MuseSparkLabelParser(tmp_path / "old.json", prompt_v2=False)
    new = MuseSparkLabelParser(tmp_path / "new.json")
    assert old._audio_key(audio, 16000, region)[1] != new._audio_key(audio, 16000, region)[1]


def test_parallel_matches_sequential_and_deduplicates(tmp_path: Path) -> None:
    """Return None after checking concurrent results, single-flight caching, and complete cache persistence."""
    clips = [(np.zeros(16000, dtype=np.float32), 16000, Region(i, i + 1, "speech", {})) for i in range(8)]
    client = MagicMock()
    client.chat.completions.create.return_value = response(json.dumps(payload()))
    sequential = MuseSparkLabelParser(tmp_path / "sequential.json", client=client)
    expected = [sequential.parse(*clip) for clip in clips]
    parser = MuseSparkLabelParser(tmp_path / "parallel.json", client=client)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(parser.parse, *clip) for clip in clips + clips]
        actual = [future.result() for future in futures]
    assert actual == expected + expected and parser.calls_made == 8
    assert len(json.loads(parser.cache_path.read_text())) == 8
    assert sum(record["cached"] for record in parser.records) == 8


def test_concurrent_budget_never_exceeded(tmp_path: Path) -> None:
    """Return None after checking competing calls and validation retries cannot exceed the shared budget."""
    client = MagicMock()
    client.chat.completions.create.return_value = response("invalid JSON")
    parser = MuseSparkLabelParser(tmp_path / "cache.json", max_spark_calls=3, client=client)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(parser.parse, np.zeros(8000, dtype=np.float32), 16000,
                               Region(i, i + 0.5, "speech", {})) for i in range(8)]
        errors = [future.exception() for future in futures]
    assert parser.calls_made == client.chat.completions.create.call_count == 3
    assert any(isinstance(error, RuntimeError) and "cost guard" in str(error) for error in errors)
    restarted = MuseSparkLabelParser(tmp_path / "cache.json", max_spark_calls=3, client=client)
    with pytest.raises(RuntimeError, match="cost guard"):
        restarted.parse(np.zeros(8000, dtype=np.float32), 16000, Region(20, 20.5, "speech", {}))
    assert client.chat.completions.create.call_count == 3


def test_ffmpeg_backend_is_explicit_and_duration_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking diagnostic decoding uses float32 mono PCM and respects validation limits."""
    import keeper.audio_io as audio_io
    from types import SimpleNamespace

    samples = np.array([0.0, 0.5, -0.5], dtype="<f4")
    process = MagicMock(return_value=SimpleNamespace(stdout=samples.tobytes()))
    monkeypatch.setattr(audio_io.subprocess, "run", process)
    actual = audio_io.load_audio("memo.m4a", 16000, use_ffmpeg=True, max_duration_s=601)
    np.testing.assert_array_equal(actual, samples)
    assert actual.dtype == np.float32 and actual.flags.c_contiguous
    arguments = process.call_args.args[0]
    assert arguments[-3:] == ["-f", "f32le", "pipe:1"]
    assert arguments[arguments.index("-t") + 1] == "601"
    assert arguments[arguments.index("-ac") + 1] == "1"
    assert not KeeperConfig().fast_audio_loading
