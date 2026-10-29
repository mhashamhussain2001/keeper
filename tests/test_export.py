"""Short-take WAV export regression tests using temporary synthetic audio."""

from pathlib import Path

import numpy as np
import soundfile as sf

from keeper.config import KeeperConfig
from keeper.export import export_clips
from keeper.types import SongMap, Take


def test_export_ten_millisecond_take(tmp_path: Path) -> None:
    """Return None after checking a 0.01-second take writes a readable WAV despite overlapping fades."""
    cfg = KeeperConfig()
    source = tmp_path / "memo.wav"
    y = np.full(cfg.sample_rate, 0.25, dtype=np.float32)
    take = Take("chorus-a", "chorus", "A", 0.0, 0.01, str(source), None, None)
    songmap = SongMap(str(source), {"chorus": [take]})
    destination = tmp_path / "clips"

    exported = export_clips(y, cfg.sample_rate, songmap, destination, cfg.fade_ms)

    exported_take = exported.sections["chorus"][0]
    clip_path = destination / "chorus-a.wav"
    assert exported_take.clip_path == str(clip_path.resolve())
    clip, sr = sf.read(clip_path, dtype="float32")
    assert sr == cfg.sample_rate
    assert clip.shape == (160,) and clip.dtype == np.float32
    assert sf.info(clip_path).subtype == "PCM_16"
    assert np.isfinite(clip).all()
    assert clip[0] == clip[-1] == 0.0
    assert np.any(clip > 0.0)
    assert take.clip_path is None
    np.testing.assert_array_equal(y, np.full(cfg.sample_rate, 0.25, dtype=np.float32))
