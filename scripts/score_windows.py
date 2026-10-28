"""Score a memo using locally cached AST artifacts without running the pipeline."""

import argparse
import sys
from pathlib import Path

# Direct execution sets sys.path to scripts/, so expose the adjacent keeper package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from keeper.audio_io import load_audio
from keeper.config import KeeperConfig
from keeper.segment_ast import ASTScorer


def main(argv: list[str] | None = None) -> int:
    """Return zero after saving only the AST window score table to CSV."""
    cli = argparse.ArgumentParser(description="Score audio with a locally cached AST model (no downloads).")
    cli.add_argument("audio", type=Path)
    cli.add_argument("--out", type=Path, required=True)
    args = cli.parse_args(argv)
    if args.out.resolve().parent == args.audio.resolve().parent or args.out.resolve() == args.audio.resolve():
        cli.error("Output must be outside the source file's folder")
    cfg = KeeperConfig()
    audio = load_audio(args.audio, cfg.sample_rate)
    scores = ASTScorer().score_windows(audio, cfg.sample_rate, cfg.win_s, cfg.hop_s)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    scores.to_csv(args.out, index=False)
    print(f"Saved {len(scores)} windows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
