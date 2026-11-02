"""Ownership migration and display-only waveform tests without network or Modal."""

import json
from pathlib import Path

import numpy as np

from app.metadata import migrate_memos, read_meta, save_meta, set_showcase
from app.storage import Settings
from app.waveform import waveform_peaks


def test_migration_is_private_ownerless_idempotent_and_preserves_artifacts(tmp_path: Path) -> None:
    """Return None after checking migration adds only metadata and preserves every existing byte."""
    settings = Settings(outputs_root=tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "songmap.json").write_text('{"sections":{}}')
    (legacy / "upload.json").write_text('{"filename":"old.wav"}')
    (legacy / "clip.wav").write_bytes(b"audio stays")
    original = {path.name: path.read_bytes() for path in legacy.iterdir()}
    owned = tmp_path / "owned"
    owned.mkdir()
    save_meta(owned, {"owner": "existing", "showcase": True, "title": "Preserve"})
    existing = (owned / "meta.json").read_bytes()
    assert migrate_memos(settings) == ["legacy"]
    metadata = read_meta(legacy)
    assert metadata["owner"] is None and metadata["showcase"] is False
    assert metadata["title"].startswith("Memo, ") and metadata["original_filename"] == "old.wav"
    assert all((legacy / name).read_bytes() == content for name, content in original.items())
    assert migrate_memos(settings) == [] and (owned / "meta.json").read_bytes() == existing
    set_showcase(settings, "legacy", True)
    assert read_meta(legacy)["showcase"] is True and read_meta(legacy)["owner"] is None


def test_waveform_has_800_peaks_and_preserves_internal_audio() -> None:
    """Return None after checking bins cover both ends, silence, tiny inputs, and bounded amplitudes."""
    audio = np.zeros(1600, dtype=np.float32)
    audio[0], audio[-1] = -0.25, 0.75
    before = audio.copy()
    peaks = waveform_peaks(audio)
    assert len(peaks) == 800 and peaks[0] == 0.25 and peaks[-1] == 0.75
    assert np.array_equal(audio, before)
    assert waveform_peaks(np.zeros(0, dtype=np.float32)) == [0.0] * 800
    assert len(waveform_peaks(np.ones(3, dtype=np.float32))) == 800
