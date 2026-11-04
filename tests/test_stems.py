"""Verify authenticated native-quality archives without models or network access."""

import io
import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pytest
import soundfile as sf

import app.server as server
import app.stems as stems
from tests.test_server import OWNER_B, api_runtime, memo, request


def seed_original(memo: Path) -> None:
    """Return None after adding stereo 44.1 kHz source audio and named, reordered and rejected takes."""
    server.settings.uploads_root.mkdir()
    samples = np.column_stack((np.full(44100, .2, dtype=np.float32), np.full(44100, .1, dtype=np.float32)))
    sf.write(server.settings.uploads_root / "original.wav", samples, 44100)
    (memo / "upload.json").write_text(json.dumps({"filename": "original.wav"}))
    rows = {"verse": [{"id": "v", "start": .1, "end": .3, "heard": "Make this the verse"}],
            "chorus": [{"id": "c", "start": .4, "end": .8}],
            "rejected": [{"id": "r", "start": .8, "end": .9}]}
    (memo / "songmap.json").write_text(json.dumps({"sections": rows}))
    (memo / "take_names.json").write_text(json.dumps({"v": "Soft/verse", "c": "Big chorus"}))
    (memo / "canvas.json").write_text(json.dumps({"clips": [{"take_id": "c", "start": 0, "lane": 1}, {"take_id": "v", "start": 2, "lane": 0}, {"take_id": "c", "start": 3, "lane": 0}]}))


def test_stems_owner_and_showcase(memo: Path) -> None:
    """Return None after checking different or absent tokens receive 404 and owners can export showcases."""
    seed_original(memo)
    assert request("GET", "/api/memos/demo/export/stems", owner=OWNER_B).status == 404
    assert request("GET", "/api/memos/demo/export/stems", owner=None).status == 404
    metadata = json.loads((memo / "meta.json").read_text())
    metadata["showcase"] = True
    (memo / "meta.json").write_text(json.dumps(metadata))
    assert request("GET", "/api/memos/demo/export/stems").status == 200
    assert request("GET", "/api/memos/demo/export/stems", owner=OWNER_B).status == 404


def test_stems_names_songmap_native_rate_channels_and_fades(memo: Path) -> None:
    """Return None after checking original stereo fidelity, sample counts, fades, names, order and heard text."""
    seed_original(memo)
    response = request("GET", "/api/memos/demo/export/stems")
    assert response.status == 200
    assert "My%20memo%20-%20stems.zip" in response.headers["content-disposition"]
    with ZipFile(io.BytesIO(response.body)) as archive:
        assert archive.namelist() == ["01 chorus Big chorus.wav", "02 verse Soft_verse.wav", "song_canvas.txt"]
        songmap = archive.read("song_canvas.txt").decode("utf-8")
        assert "Name: Soft/verse" in songmap and "Section: verse" in songmap
        assert "0.100000" in songmap and "0.300000" in songmap and "Make this the verse" in songmap
        samples, rate = sf.read(io.BytesIO(archive.read("02 verse Soft_verse.wav")), dtype="float32", always_2d=True)
        assert rate == 44100 and samples.shape == (8820, 2)
        assert np.all(samples[[0, -1]] == 0)
        assert samples[400, 0] == pytest.approx(.2, abs=1/32768)
        assert samples[400, 1] == pytest.approx(.1, abs=1/32768)
    recording = request("GET", "/api/memos/demo/recording")
    assert recording.headers["x-audio-sample-rate"] == "44100"
    assert recording.headers["x-audio-channels"] == "2"


def test_stems_rejected_flag_and_source_order(memo: Path) -> None:
    """Return None after checking rejection opt-in uses its own folder and absent canvases use source order."""
    seed_original(memo)
    (memo / "canvas.json").unlink()
    response = request("GET", "/api/memos/demo/export/stems?include_rejected=1")
    assert response.status == 200
    with ZipFile(io.BytesIO(response.body)) as archive:
        assert archive.namelist() == ["01 verse Soft_verse.wav", "02 chorus Big chorus.wav", "rejected/03 rejected Rejected 1.wav", "song_canvas.txt"]
        assert "Section: rejected" in archive.read("song_canvas.txt").decode("utf-8")


def test_stems_size_guard_before_cutting(memo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking the estimated PCM archive refuses oversized downloads with 413."""
    seed_original(memo)
    monkeypatch.setattr(stems, "MAX_STEMS_BYTES", 10000)
    response = request("GET", "/api/memos/demo/export/stems")
    assert response.status == 413 and "500 MB" in response.json()["detail"]


def test_stems_missing_original_and_bad_boundaries(memo: Path) -> None:
    """Return None after checking missing source files and invalid spans fail clearly without preview substitution."""
    assert request("GET", "/api/memos/demo/export/stems").status == 404
    seed_original(memo)
    (memo / "songmap.json").write_text(json.dumps({"sections": {"verse": [{"id": "v", "start": 0, "end": 2}]}}))
    assert request("GET", "/api/memos/demo/export/stems").status == 422
