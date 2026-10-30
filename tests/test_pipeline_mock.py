"""Network-blocked pipeline and non-destructive clip export integration tests."""

import json
import socket
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import soundfile as sf

import keeper.pipeline as pipeline
from keeper.config import KeeperConfig
from keeper.export import export_clips
from keeper.labels import MockLabelParser, MuseSparkLabelParser
from keeper.types import CommandLabel, Region, SongMap, Take


def _forbid_network(*args: object, **kwargs: object) -> None:
    """Return no result because every attempted network connection fails the test."""
    raise AssertionError("Tests must not use network access")


def _forbid_ast() -> None:
    """Return no scorer because CSV pipeline tests must never construct AST."""
    raise AssertionError("CSV pipeline must not load AST")


def _synthetic_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return paths to a temporary synthetic 30-second memo, scores, and mock commands."""
    cfg = KeeperConfig()
    times = np.arange(30 * cfg.sample_rate) / cfg.sample_rate
    rng = np.random.default_rng(7)
    y = (0.1 * np.sin(2 * np.pi * 220 * times) + 0.01 * rng.standard_normal(len(times))).astype(np.float32)
    audio_path = tmp_path / "synthetic.wav"
    sf.write(audio_path, y, cfg.sample_rate, subtype="FLOAT")
    # Exact two-second classifier windows deliberately straddle the speech boundaries.
    starts = np.arange(0.0, 30.0, cfg.hop_s)
    speech = ((starts >= 8.5) & (starts < 11.5)) | ((starts >= 23.5) & (starts < 26.5))
    scores = pd.DataFrame({"start": starts, "end": starts + cfg.win_s,
                           "singing": np.where(speech, 0.05, 0.95),
                           "speech": np.where(speech, 0.95, 0.05), "instrument": 0.1})
    scores_path = tmp_path / "scores.csv"
    scores.to_csv(scores_path, index=False)
    labels_path = tmp_path / "commands.json"
    labels_path.write_text(json.dumps({
        "9.0": CommandLabel(True, "chorus", "B", "keep that one", 1.0).to_dict(),
        "24.0": CommandLabel(True, "verse", None, None, 1.0).to_dict(),
    }), encoding="utf-8")
    return audio_path, scores_path, labels_path


class _ShortMemoScorer:
    """Deterministic singing scores for a memo shorter than the classifier window."""

    def score_windows(self, y: np.ndarray, sr: int, win_s: float, hop_s: float) -> pd.DataFrame:
        """Return hop-spaced singing scores whose windows extend beyond the short memo."""
        assert sr == 16000
        assert y.dtype == np.float32 and y.shape == (24000,)
        assert len(y) / sr < win_s
        starts = np.arange(0.0, len(y) / sr, hop_s)
        return pd.DataFrame({"start": starts, "end": starts + win_s,
                             "singing": 0.95, "speech": 0.05, "instrument": 0.1})


def test_pipeline_scores_csv_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    """Return None after checking all pipeline artifacts and expected sections without AST or network."""
    monkeypatch.setattr(socket, "create_connection", _forbid_network)
    monkeypatch.setattr(socket.socket, "connect", _forbid_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _forbid_network)
    monkeypatch.setattr(pipeline, "ASTScorer", _forbid_ast)
    audio, scores, labels = _synthetic_inputs(tmp_path)
    original_bytes = audio.read_bytes()
    destination = tmp_path / "result"
    assert pipeline.main([str(audio), "--parser", "mock", "--mock-labels", str(labels),
                          "--scores-csv", str(scores), "--out", str(destination)]) == 0
    songmap = SongMap.from_json(destination / "songmap.json")
    assert set(songmap.sections) == {"chorus", "verse"}
    assert songmap.sections["chorus"][0].variant == "B"
    assert songmap.sections["verse"][0].variant == "Take 1"
    for takes in songmap.sections.values():
        for take in takes:
            clip, sr = sf.read(take.clip_path, dtype="float32")
            assert sr == KeeperConfig().sample_rate and clip.ndim == 1
            assert sf.info(take.clip_path).subtype == "PCM_16"
            assert clip[0] == 0.0 and clip[-1] == 0.0
    metadata = json.loads((destination / "regions.json").read_text(encoding="utf-8"))
    assert metadata["silence_gate_db"] == -45.0
    regions = metadata["regions"]
    assert regions[0]["start"] == 0.0 and regions[-1]["end"] == 30.0
    assert all(left["end"] == right["start"] for left, right in zip(regions, regions[1:]))
    assert all(set(region["mean_scores"]) == {"singing", "speech", "instrument", "music"} for region in regions)
    assert (destination / "window_scores.csv").exists()
    assert (destination / "warnings.txt").read_text(encoding="utf-8") == ""
    assert audio.read_bytes() == original_bytes
    assert "Warnings: 0" in capsys.readouterr().out


def test_partial_frame_duration_and_speech_only_parser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking exact partial duration and parser dispatch for speech alone."""
    monkeypatch.setattr(pipeline, "ASTScorer", _forbid_ast)
    audio = tmp_path / "partial.wav"
    sf.write(audio, np.full(19200, 0.1, dtype=np.float32), 16000)
    scores = tmp_path / "scores.csv"
    pd.DataFrame({"start": [0.0], "end": [2.0], "singing": [0.9],
                  "speech": [0.1], "instrument": [0.1]}).to_csv(scores, index=False)
    labels = tmp_path / "labels.json"
    labels.write_text("{}", encoding="utf-8")
    songmap, regions, warnings = pipeline.run_pipeline(audio, MockLabelParser(labels), tmp_path / "result", scores_csv=scores)
    assert [(region.start, region.end) for region in regions] == [(0.0, 1.2)]
    assert songmap.sections == {} and warnings == []


def test_pipeline_memo_shorter_than_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking a 1.5-second memo produces finite regions inside its exact duration."""
    monkeypatch.setattr(socket, "create_connection", _forbid_network)
    monkeypatch.setattr(socket.socket, "connect", _forbid_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _forbid_network)
    monkeypatch.setattr(pipeline, "ASTScorer", _forbid_ast)
    cfg = KeeperConfig()
    duration = 1.5
    assert duration < cfg.win_s
    times = np.arange(round(duration * cfg.sample_rate)) / cfg.sample_rate
    y = (0.1 * np.sin(2 * np.pi * 220 * times)).astype(np.float32)
    audio = tmp_path / "short.wav"
    sf.write(audio, y, cfg.sample_rate, subtype="FLOAT")
    labels = tmp_path / "labels.json"
    labels.write_text("{}", encoding="utf-8")
    destination = tmp_path / "result"

    songmap, regions, warnings = pipeline.run_pipeline(
        audio, MockLabelParser(labels), destination, cfg=cfg, scorer=_ShortMemoScorer(),
    )

    assert [(region.start, region.end, region.kind) for region in regions] == [(0.0, duration, "music")]
    assert all(0.0 <= region.start < region.end <= duration for region in regions)
    assert all(left.end == right.start for left, right in zip(regions, regions[1:]))
    for region in regions:
        assert set(region.mean_scores) == {"singing", "speech", "instrument", "music"}
        assert all(np.isfinite(score) and 0.0 <= score <= 1.0 for score in region.mean_scores.values())
        assert region.mean_scores == pytest.approx({"singing": 0.95, "speech": 0.05, "instrument": 0.1, "music": 0.95})
    assert songmap.sections == {} and warnings == []
    assert SongMap.from_json(destination / "songmap.json") == songmap
    assert json.loads((destination / "regions.json").read_text(encoding="utf-8"))["regions"] == [
        region.to_dict() for region in regions
    ]


def test_cli_help_does_not_load_model(monkeypatch: pytest.MonkeyPatch,
                                    capsys: pytest.CaptureFixture[str]) -> None:
    """Return None after checking CLI help exits successfully without constructing AST."""
    monkeypatch.setattr(pipeline, "ASTScorer", _forbid_ast)
    with pytest.raises(SystemExit) as exc:
        pipeline.main(["--help"])
    assert exc.value.code == 0
    assert "--scores-csv" in capsys.readouterr().out


def test_export_preserves_input_and_rejects_source_folder(tmp_path: Path) -> None:
    """Return None after checking export copies audio and song maps and protects the source folder."""
    source = tmp_path / "memo.wav"
    take = Take("chorus-a", "chorus", "A", 0.0, 1.0, str(source), None, None)
    songmap = SongMap(str(source), {"chorus": [take]})
    y = np.ones(16000, dtype=np.float32)
    with pytest.raises(ValueError, match="source file's folder"):
        export_clips(y, 16000, songmap, tmp_path, 10.0)
    exported = export_clips(y, 16000, songmap, tmp_path / "clips", 10.0)
    assert np.all(y == 1.0) and take.clip_path is None
    clip, _ = sf.read(exported.sections["chorus"][0].clip_path, dtype="float32")
    assert clip[0] == clip[-1] == 0.0


def test_mock_rounding_and_noncommand_default(tmp_path: Path) -> None:
    """Return None after checking rounded time lookup and missing-fixture behavior."""
    path = tmp_path / "commands.json"
    label = CommandLabel(True, "chorus", "B", None, 1.0)
    path.write_text(json.dumps({"2.0": label.to_dict()}), encoding="utf-8")
    parser = MockLabelParser(path)
    y = np.zeros(16000, dtype=np.float32)
    assert parser.parse(y, 16000, Region(2.1, 3.0, "speech", {})) == label
    assert not parser.parse(y, 16000, Region(5.0, 6.0, "speech", {})).is_command
    with pytest.raises(ValueError, match="Only speech"):
        parser.parse(y, 16000, Region(2.0, 3.0, "music", {}))


def test_muse_stub_never_calls_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking the unimplemented adapter reads only the environment."""
    monkeypatch.delenv("META_API_KEY", raising=False)
    monkeypatch.setattr(socket, "create_connection", _forbid_network)
    parser = MuseSparkLabelParser()
    assert parser.api_key == ""
    with pytest.raises(NotImplementedError, match="Muse Spark audio input not verified yet"):
        parser.parse(np.zeros(16000, dtype=np.float32), 16000, Region(0.0, 1.0, "speech", {}))
