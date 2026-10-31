"""Seed the web UI from cached fixture scores with placeholder or real clips."""

import argparse
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import soundfile as sf

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from keeper.audio_io import load_audio
from keeper.config import KeeperConfig
from keeper.export import export_clips
from keeper.labels import MockLabelParser
from keeper.pipeline import run_pipeline
from keeper.types import SongMap, Take


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "test_memo"


def make_demo(out_dir: Path, audio: Path | None = None) -> SongMap:
    """Return a saved fixture song map with playable relative clip paths."""
    songmap, _, _ = run_pipeline(
        FIXTURE, MockLabelParser(FIXTURE / "mock_commands.json"), out_dir,
        scores_csv=FIXTURE / "window_scores.csv", rms_json=FIXTURE / "rms_db.json", no_export=True,
    )
    config = KeeperConfig()
    if audio is not None:
        songmap.source_file = str(audio.resolve())
        for takes in songmap.sections.values():
            for take in takes:
                take.source_file = songmap.source_file
        songmap = export_clips(load_audio(audio, config.sample_rate), config.sample_rate,
                               songmap, out_dir, config.fade_ms)
    else:
        print("WARNING: Demo clips are quiet sine-tone PLACEHOLDERS, not the original recording.")
    sections: dict[str, list[Take]] = {}
    for section, takes in songmap.sections.items():
        sections[section] = []
        for index, take in enumerate(takes):
            if audio is None:
                filename = f"placeholder_{take.id}.wav"
                count = round((take.end - take.start) * config.sample_rate)
                times = np.arange(count, dtype=np.float32) / config.sample_rate
                clip = (0.035 * np.sin(2 * np.pi * (220 + index * 55) * times)).astype(np.float32)
                fade = min(round(config.fade_ms * config.sample_rate / 1000), count)
                if fade:
                    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
                    clip[:fade] *= ramp
                    clip[-fade:] *= ramp[::-1]
                sf.write(out_dir / filename, clip, config.sample_rate, subtype="PCM_16")
            else:
                filename = Path(take.clip_path).name
            sections[section].append(replace(take, clip_path=filename))
    result = SongMap(songmap.source_file, sections)
    result.to_json(out_dir / "songmap.json")
    print(f"Wrote {sum(map(len, sections.values()))} takes to {out_dir}")
    return result


def main(argv: list[str] | None = None) -> int:
    """Return zero after generating the demo memo and printing its clip type."""
    cli = argparse.ArgumentParser(description="Create fixture_demo with placeholder tones or original audio clips.")
    cli.add_argument("--audio", type=Path, help="Export real fixture takes from this recording, e.g. data/test.wav")
    args = cli.parse_args(argv)
    make_demo(ROOT / "outputs" / "fixture_demo", args.audio)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
