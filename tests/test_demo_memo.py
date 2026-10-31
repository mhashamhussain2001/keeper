"""Validate seeded demo metadata and clip duration with synthetic audio only."""

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from scripts.make_demo_memo import make_demo


@pytest.mark.parametrize("real_audio", [False, True])
def test_demo_memo_has_three_playable_chorus_takes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], real_audio: bool,
) -> None:
    """Return None after checking fixture seeding exports exact-duration placeholder or real WAVs."""
    audio = None
    if real_audio:
        audio = tmp_path / "original.wav"
        sf.write(audio, np.zeros(round(28.28 * 16000), dtype=np.float32), 16000)
    destination = tmp_path / "outputs" / "demo"
    songmap = make_demo(destination, audio)
    assert set(songmap.sections) == {"chorus"}
    takes = songmap.sections["chorus"]
    assert [take.variant for take in takes] == ["A", "B", "C"]
    for take in takes:
        assert not Path(take.clip_path).is_absolute()
        assert Path(take.clip_path).name.startswith("placeholder_") is not real_audio
        clip, sr = sf.read(destination / take.clip_path, dtype="float32")
        assert sr == 16000 and clip.ndim == 1 and clip.dtype == np.float32
        assert len(clip) / sr == pytest.approx(take.end - take.start, abs=1 / sr)
        assert np.max(np.abs(clip)) <= 0.036
    assert (destination / "window_scores.csv").is_file() and (destination / "regions.json").is_file()
    assert ("PLACEHOLDERS" in capsys.readouterr().out) is not real_audio
