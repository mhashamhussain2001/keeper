"""Measure fresh-process decoders on Modal and compare memo1 AST probabilities only."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from typing import Any

import numpy as np

from keeper.audio_io import load_audio


def decode_trial(source: Path, destination: Path, fast: bool) -> dict[str, float]:
    """Return first-call and repeat decode times, persisting samples for comparisons."""
    times = []
    for _ in range(2):
        started = perf_counter()
        samples = load_audio(source, 16000, use_ffmpeg=fast)
        times.append(perf_counter() - started)
    np.save(destination, samples)
    return {"cold_s": times[0], "warm_s": times[1], "duration_s": len(samples) / 16000}


def compare_samples(first: np.ndarray, second: np.ndarray) -> dict[str, float | int]:
    """Return length and aligned sample-error statistics without hiding decoder length differences."""
    common = min(len(first), len(second))
    difference = first[:common].astype(np.float64) - second[:common]
    return {"librosa_samples": len(first), "ffmpeg_samples": len(second),
            "max_abs": float(np.max(np.abs(difference))), "rmse": float(np.sqrt(np.mean(difference ** 2)))}


def benchmark(directory: Path) -> dict[str, Any]:
    """Return cold/warm decoder timings and memo1 sample/AST differences using a cached GPU model."""
    result: dict[str, Any] = {"timings": {}, "samples": {}, "cold_definition": "first decode in a fresh Python process; disk cache not flushed"}
    for name in ("memo2.wav", "test_2.m4a", "test.wav"):
        arrays = {}
        for backend, fast in (("librosa", False), ("ffmpeg", True)):
            output = directory / f"{name}.{backend}.npy"
            completed = subprocess.run([sys.executable, "-m", "scripts.benchmark_audio_loading", "--trial",
                                        str(directory / name), str(output), "--backend", backend],
                                       capture_output=True, text=True, check=True, timeout=300)
            result["timings"][f"{name}/{backend}"] = json.loads(completed.stdout)
            arrays[backend] = np.load(output)
        result["samples"][name] = compare_samples(arrays["librosa"], arrays["ffmpeg"])
    from keeper.segment_ast import ASTScorer
    scorer = ASTScorer(device="cuda")
    scores = {}
    for backend in ("librosa", "ffmpeg"):
        samples = np.load(directory / f"test.wav.{backend}.npy")
        scores[backend] = scorer.score_windows(samples, 16000, 2.0, 0.5)
        scores[backend].to_csv(directory / f"memo1_{backend}_scores.csv", index=False)
    if scores["librosa"].shape != scores["ffmpeg"].shape:
        raise ValueError("Decoder score window shapes differ")
    columns = [column for column in scores["librosa"] if column not in ("start", "end")]
    result["ast_max_abs"] = float((scores["librosa"][columns] - scores["ffmpeg"][columns]).abs().to_numpy().max())
    result["ast_by_group"] = {group: float((scores["librosa"][group] - scores["ffmpeg"][group]).abs().max())
                              for group in ("singing", "speech", "instrument")}
    (directory / "loader_report.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    """Return zero after emitting a decoder subprocess trial as JSON."""
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--trial", nargs=2, type=Path, required=True)
    cli.add_argument("--backend", choices=("librosa", "ffmpeg"), required=True)
    args = cli.parse_args()
    print(json.dumps(decode_trial(*args.trial, args.backend == "ffmpeg")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
