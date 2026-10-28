"""Command-line orchestration of the offline Keeper pipeline."""

import argparse
import json
from collections import Counter
from pathlib import Path

import pandas as pd

from keeper.audio_io import load_audio, rms_db_per_frame
from keeper.config import SCORE_GROUPS, KeeperConfig
from keeper.cut import cut_takes
from keeper.export import export_clips
from keeper.frames import frame_labels_from_scores, frame_scores_from_windows
from keeper.labels import LabelParser, MockLabelParser
from keeper.regions import absorb_short, merge_frames
from keeper.segment_ast import ASTScorer, Scorer
from keeper.songmap import build_songmap
from keeper.types import Region, SongMap


def run_pipeline(
    audio_path: str | Path,
    parser: LabelParser,
    out_dir: str | Path,
    cfg: KeeperConfig | None = None,
    scores_csv: str | Path | None = None,
    scorer: Scorer | None = None,
) -> tuple[SongMap, list[Region], list[str]]:
    """Return the exported song map, scored regions, and warnings after writing pipeline artifacts."""
    config = cfg if cfg is not None else KeeperConfig()
    source = Path(audio_path).resolve()
    destination = Path(out_dir).resolve()
    if destination == source.parent:
        raise ValueError("Output directory must differ from the source file's folder")
    artifact_paths = {destination / name for name in ("songmap.json", "regions.json", "window_scores.csv", "warnings.txt")}
    if source in artifact_paths:
        raise ValueError("Output artifacts cannot overwrite the source file")
    y = load_audio(source, config.sample_rate)
    if scores_csv is not None:
        scores = pd.read_csv(scores_csv)
    else:
        active_scorer = scorer if scorer is not None else ASTScorer()
        scores = active_scorer.score_windows(y, config.sample_rate, config.win_s, config.hop_s)
    rms = rms_db_per_frame(y, config.sample_rate, config.hop_s)
    frame_labels = frame_labels_from_scores(scores, rms, config)
    regions = merge_frames(frame_labels, config.hop_s)
    duration = float(len(y) / config.sample_rate)
    if regions:
        regions[-1].end = duration
    regions = absorb_short(regions, config)
    frame_scores = frame_scores_from_windows(scores, duration, config.hop_s)
    for region in regions:
        overlapping = frame_scores[(frame_scores["start"] < region.end) & (frame_scores["end"] > region.start)]
        weights = (overlapping["end"].clip(upper=region.end) - overlapping["start"].clip(lower=region.start))
        region.mean_scores = {
            group: float((overlapping[group] * weights).sum() / weights.sum()) for group in SCORE_GROUPS
        }
    commands = {
        region.start: parser.parse(
            y[round(region.start * config.sample_rate):round(region.end * config.sample_rate)].copy(),
            config.sample_rate, region,
        )
        for region in regions if region.kind == "speech"
    }
    takes, warnings = cut_takes(regions, commands, str(source), config)
    songmap = build_songmap(takes, str(source))
    songmap = export_clips(y, config.sample_rate, songmap, destination, config.fade_ms)
    songmap.to_json(destination / "songmap.json")
    (destination / "regions.json").write_text(
        json.dumps([region.to_dict() for region in regions], indent=2, allow_nan=False), encoding="utf-8",
    )
    scores.to_csv(destination / "window_scores.csv", index=False)
    (destination / "warnings.txt").write_text("".join(f"{warning}\n" for warning in warnings), encoding="utf-8")
    return songmap, regions, warnings


def main(argv: list[str] | None = None) -> int:
    """Return zero after running the CLI pipeline and printing a compact result summary."""
    cli = argparse.ArgumentParser(description="Organize a songwriting memo using local scores and mock commands.")
    cli.add_argument("audio_path", type=Path)
    cli.add_argument("--parser", choices=("mock",), default="mock")
    cli.add_argument("--mock-labels", type=Path, required=True)
    cli.add_argument("--out", type=Path, required=True)
    cli.add_argument("--scores-csv", type=Path, help="Load window scores and skip AST entirely")
    args = cli.parse_args(argv)
    songmap, regions, warnings = run_pipeline(
        args.audio_path, MockLabelParser(args.mock_labels), args.out, scores_csv=args.scores_csv,
    )
    print("Regions: " + str(dict(Counter(region.kind for region in regions))))
    print("Takes: " + str({section: len(takes) for section, takes in songmap.sections.items()}))
    print(f"Warnings: {len(warnings)}")
    for warning in warnings:
        print(warning)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
