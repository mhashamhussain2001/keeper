"""Offline Spark parsing, retry, cache, budget, and two-pass pipeline checks."""

import io
import json
import base64
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
import soundfile as sf

from keeper.config import KeeperConfig
from keeper.labels import MuseSparkLabelParser, spark_command, validate_spark
from keeper.pipeline import run_pipeline
from keeper.types import Region


def payload(intent: str = "file") -> dict:
    """Return a valid six-field Spark result for tests."""
    return dict(heard="that's the verse", intent=intent, section="verse" if intent == "file" else None,
                target="previous", note=None, confidence=0.9)


def response(content: str, finish: str = "stop") -> NS:
    """Return an OpenAI-shaped mocked chat response with usage."""
    return NS(choices=[NS(message=NS(content=content), finish_reason=finish)],
              usage=NS(total_tokens=20, completion_tokens_details=NS(reasoning_tokens=5)))


@pytest.mark.parametrize("intent,section,command", [("file", "verse", True), ("keep", "unsorted", True),
    ("reject", "rejected", True), ("none", "unsorted", False), ("performance", "unsorted", False), ("noise", "unsorted", False)])
def test_intent_mapping(intent: str, section: str, command: bool) -> None:
    """Return None after checking every intent's pipeline mapping and retained transcript."""
    label = spark_command(validate_spark(json.dumps(payload(intent))))
    assert (label.section, label.is_command, label.intent, label.variant) == (section, command, intent, None)
    assert label.heard == "that's the verse"


@pytest.mark.parametrize("first", ["bad JSON", json.dumps({**payload(), "intent": "bad"}),
                                 "I cannot hear or process the audio clip", ""])
def test_invalid_then_valid_retry(tmp_path: Path, first: str) -> None:
    """Return None after checking invalid output retries once and preserves attempt audit."""
    client = MagicMock()
    client.chat.completions.create.side_effect = [response(first), response("```json\n" + json.dumps(payload()) + "\n```")]
    parser = MuseSparkLabelParser(tmp_path / "cache.json", client=client)
    label = parser.parse(np.zeros(16000, dtype=np.float32), 16000, Region(0.0, 1.0, "speech", {}))
    assert label.intent == "file" and parser.calls_made == 2
    assert parser.records[0]["attempts"][0]["failure_reason"] and parser.records[0]["retries"] == 1


def test_two_failures_fall_back_and_length_retries(tmp_path: Path) -> None:
    """Return None after checking truncation is retried and final failure becomes cached none."""
    client = MagicMock()
    client.chat.completions.create.side_effect = [response(json.dumps(payload()), "length"), response("invalid")]
    parser = MuseSparkLabelParser(tmp_path / "cache.json", client=client)
    label = parser.parse(np.zeros(16000, dtype=np.float32), 16000, Region(0.0, 1.0, "speech", {}))
    assert label.intent == "none" and label.confidence == 0 and parser.records[0]["failure_reason"]


def test_client_signature_fallback(tmp_path: Path) -> None:
    """Return None after checking reasoning_effort moves to extra_body if the client rejects its keyword."""
    client = MagicMock()
    client.chat.completions.create.side_effect = [TypeError("unexpected keyword reasoning_effort"), response(json.dumps(payload()))]
    parser = MuseSparkLabelParser(tmp_path / "cache.json", client=client)
    assert parser.parse(np.zeros(8000, dtype=np.float32), 16000, Region(0.0, 0.5, "speech", {})).intent == "file"
    assert client.chat.completions.create.call_args.kwargs["extra_body"] == {"reasoning_effort": "low"}
    assert "reasoning_effort" not in {key for key in client.chat.completions.create.call_args.kwargs if key != "extra_body"}


def test_temperature_fallback(tmp_path: Path) -> None:
    """Return None after checking an unsupported temperature keyword is omitted on retry."""
    client = MagicMock()
    client.chat.completions.create.side_effect = [TypeError("unexpected keyword temperature"), response(json.dumps(payload()))]
    parser = MuseSparkLabelParser(tmp_path / "cache.json", client=client)
    assert parser.parse(np.zeros(8000, dtype=np.float32), 16000, Region(0.0, 0.5, "speech", {})).intent == "file"
    assert "temperature" not in client.chat.completions.create.call_args.kwargs


def test_unannotated_speech_truth_and_command_priority() -> None:
    """Return None after checking unannotated none/noise truth and command priority in mixed regions."""
    from scripts.spark_eval import command_report
    records = [{"start": start, "end": end, "parsed": payload(intent), "cached": True, "attempts": [], "retries": 0}
               for start, end, intent in [(19.0, 21.0, "noise"), (30.0, 32.0, "none")]]
    report, accuracy = command_report([], records, "memo1")
    assert accuracy == 1.0 and "unannotated speech: noise" in report and "unannotated speech: none" in report
    records = [{**records[0], "end": 22.5, "parsed": payload()}]
    report, accuracy = command_report([(21.0, 22.0, "cmd:file section=verse")], records, "memo1")
    assert accuracy == 1.0 and "unannotated speech" not in report


def test_cache_and_budget_and_audio_format(tmp_path: Path) -> None:
    """Return None after checking valid JSON, WAV transport, cache hits, and persistent budget guard."""
    client = MagicMock()
    client.chat.completions.create.return_value = response(json.dumps(payload()))
    parser = MuseSparkLabelParser(tmp_path / "cache.json", max_spark_calls=1, client=client)
    audio = np.zeros(16000, dtype=np.float32)
    region = Region(0.0, 1.0, "speech", {})
    assert parser.parse(audio, 16000, region).intent == "file"
    url = client.chat.completions.create.call_args.kwargs["messages"][0]["content"][1]["audio_url"]["url"]
    info = sf.info(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert (info.samplerate, info.channels, info.subtype) == (16000, 1, "PCM_16")
    cached = MuseSparkLabelParser(tmp_path / "cache.json", max_spark_calls=1, client=client)
    assert cached.parse(audio, 16000, region).intent == "file" and cached.calls_made == 0
    with pytest.raises(RuntimeError, match="cost guard"):
        cached.preflight([(audio, 16000, Region(1.0, 2.0, "speech", {}))])
    with pytest.raises(RuntimeError, match="cost guard"):
        cached.parse(audio, 16000, Region(1.0, 2.0, "speech", {}))
    assert client.chat.completions.create.call_count == 1


@pytest.mark.parametrize("intent,expected", [("performance", "music"), ("noise", "silence"), ("reject", "speech")])
def test_pipeline_two_pass_padding_and_reject(tmp_path: Path, intent: str, expected: str) -> None:
    """Return None after checking padding, one parse per region, relabel-before-cut, and rejected takes."""
    sr = 16000
    y = np.full(10 * sr, 0.1, dtype=np.float32)
    source = tmp_path / "memo.wav"
    sf.write(source, y, sr)
    start = np.arange(20) * 0.5
    scores = tmp_path / "scores.csv"
    pd.DataFrame(dict(start=start, end=start + 0.5, singing=0.5, instrument=0.1,
                      speech=np.where((start >= 8) & (start < 9), 0.9, 0.1))).to_csv(scores, index=False)
    client = MagicMock()
    client.chat.completions.create.return_value = response(json.dumps({**payload(intent), "target": "next"}))
    parser = MuseSparkLabelParser(tmp_path / "cache.json", client=client)
    songmap, regions, warnings = run_pipeline(source, parser, tmp_path / "out", cfg=KeeperConfig(), scores_csv=scores)
    assert parser.calls_made == 1
    assert next(region.kind for region in regions if region.start <= 8.25 < region.end) == expected
    assert "label-before-take not supported:" in warnings[0]
    url = client.chat.completions.create.call_args.kwargs["messages"][0]["content"][1]["audio_url"]["url"]
    assert sf.info(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).duration == pytest.approx(1.6)
    if intent == "reject":
        assert songmap.sections["rejected"][0].end == 8.0
