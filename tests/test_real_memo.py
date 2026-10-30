"""Audio-free regression of real AST scores against the memo's Audacity truth."""

import json
import socket
from pathlib import Path

import pytest

import keeper.pipeline as pipeline
from eval.audacity_labels import read_audacity_labels
from eval.evaluate import frame_accuracy
from keeper.labels import MockLabelParser


FIXTURE = Path(__file__).parent / "fixtures" / "test_memo"


def _forbid_external_input(*args: object, **kwargs: object) -> None:
    """Return no result because the numeric fixture must never load audio, AST, or network data."""
    raise AssertionError("Numeric fixture must stay offline and audio-free")


def test_real_memo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking real memo accuracy, chorus boundaries, and the chosen gate."""
    monkeypatch.setattr(socket.socket, "connect", _forbid_external_input)
    monkeypatch.setattr(socket.socket, "connect_ex", _forbid_external_input)
    monkeypatch.setattr(socket, "create_connection", _forbid_external_input)
    monkeypatch.setattr(pipeline, "load_audio", _forbid_external_input)
    monkeypatch.setattr(pipeline, "ASTScorer", _forbid_external_input)
    songmap, regions, warnings = pipeline.run_pipeline(
        FIXTURE, MockLabelParser(FIXTURE / "mock_commands.json"), tmp_path / "result",
        scores_csv=FIXTURE / "window_scores.csv", rms_json=FIXTURE / "rms_db.json", no_export=True,
    )
    metrics = frame_accuracy(regions, read_audacity_labels(FIXTURE / "truth_labels.txt"), 0.5)
    print(f"Frame accuracy: {metrics['overall_accuracy']:.6f}")
    print("Per-class recall:", {kind: row["recall"] for kind, row in metrics["per_class"].items()})
    metadata = json.loads((tmp_path / "result" / "regions.json").read_text(encoding="utf-8"))
    print(f"Silence gate: {metadata['silence_gate_db']:.2f} dB")
    takes = songmap.sections.get("chorus", [])
    for take in takes:
        print(f"chorus {take.variant}: {take.start:.2f}-{take.end:.2f} s")
    assert metrics["overall_accuracy"] >= 0.85
    assert warnings == []
    assert len(takes) == 3 and sum(map(len, songmap.sections.values())) == 3
    assert [take.variant for take in takes] == ["A", "B", "C"]
    for take, expected in zip(takes, [(0.0, 8.0), (10.52, 20.64), (23.05, 26.85)]):
        assert (take.start, take.end) == pytest.approx(expected, abs=0.75)
        assert take.clip_path is None
    assert songmap.sections.get("unlabeled", []) == []
    assert not list((tmp_path / "result").glob("*.wav"))
