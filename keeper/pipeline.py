"""Command-line orchestration of the offline Keeper pipeline."""

import argparse
import json
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from math import ceil
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd

from keeper.audio_io import load_audio, rms_db_per_frame
from keeper.audio_metadata import audio_format
from keeper.config import SCORE_GROUPS, KeeperConfig
from keeper.cut import cut_takes
from keeper.export import export_clips
from keeper.events import EventCallback, EventReporter
from keeper.frames import frame_labels_from_scores, frame_scores_from_windows
from keeper.labels import LabelParser, MockLabelParser, MuseSparkLabelParser
from keeper.regions import absorb_short, merge_frames, merge_adjacent
from keeper.segment_ast import ASTScorer, Scorer
from keeper.songmap import build_songmap
from keeper.trim import trim_takes
from keeper.types import CommandLabel, Region, SongMap
from keeper.unlabeled import find_unlabeled


def run_pipeline(
    audio_path: str | Path,
    parser: LabelParser,
    out_dir: str | Path,
    cfg: KeeperConfig | None = None,
    scores_csv: str | Path | None = None,
    scorer: Scorer | None = None,
    rms_json: str | Path | None = None,
    no_export: bool = False,
    duration_s: float | None = None,
    progress: Callable[[str], None] | None = None,
    on_event: EventCallback | None = None,
) -> tuple[SongMap, list[Region], list[str]]:
    """Return the exported song map, scored regions, and warnings after writing pipeline artifacts."""
    reporter = EventReporter(on_event)
    try:
        return _run_pipeline(audio_path, parser, out_dir, cfg, scores_csv, scorer, rms_json,
                             no_export, duration_s, progress, reporter)
    except Exception as exc:
        reporter.emit("failed", message=str(exc))
        raise


def _run_pipeline(
    audio_path: str | Path, parser: LabelParser, out_dir: str | Path,
    cfg: KeeperConfig | None, scores_csv: str | Path | None, scorer: Scorer | None,
    rms_json: str | Path | None, no_export: bool, duration_s: float | None,
    progress: Callable[[str], None] | None, reporter: EventReporter,
) -> tuple[SongMap, list[Region], list[str]]:
    """Return unchanged pipeline results while reporting completed work to the observer."""
    config = cfg if cfg is not None else KeeperConfig()
    if progress is not None:
        progress("loading audio")
    source = Path(audio_path).resolve()
    destination = Path(out_dir).resolve()
    if destination == source.parent:
        raise ValueError("Output directory must differ from the source file's folder")
    artifact_paths = {destination / name for name in ("songmap.json", "regions.json", "window_scores.csv", "window_scores.tmp", "warnings.txt")}
    if source in artifact_paths:
        raise ValueError("Output artifacts cannot overwrite the source file")
    if rms_json is not None:
        if scores_csv is None or not no_export or not isinstance(parser, MockLabelParser):
            raise ValueError("Precomputed RMS requires scores_csv, no_export, and MockLabelParser")
        rms = np.asarray(json.loads(Path(rms_json).read_text(encoding="utf-8")), dtype=float)
        if duration_s is None:
            meta_path = source / "meta.json" if source.is_dir() else Path(rms_json).parent / "meta.json"
            duration_s = float(json.loads(meta_path.read_text(encoding="utf-8"))["duration_s"])
        duration = float(duration_s)
        if not np.isfinite(duration) or duration < 0:
            raise ValueError("duration_s must be nonnegative and finite")
        if rms.ndim != 1 or len(rms) < ceil(duration / config.hop_s):
            raise ValueError("RMS frames must cover duration_s at the configured hop_s")
        if not np.isfinite(rms).all():
            raise ValueError("RMS values must be finite")
        rms = rms[:ceil(duration / config.hop_s)]
        # Mock parsing uses timestamps only; no synthetic substitute audio is needed.
        y = np.empty(0, dtype=np.float32)
    else:
        y = load_audio(source, config.sample_rate, use_ffmpeg=config.fast_audio_loading)
        if duration_s is not None:
            if not np.isfinite(duration_s) or not 0 <= duration_s <= len(y) / config.sample_rate:
                raise ValueError("duration_s must fit the source audio")
            y = y[:round(duration_s * config.sample_rate)]
        rms = rms_db_per_frame(y, config.sample_rate, config.hop_s)
        duration = float(len(y) / config.sample_rate)
    native = {}
    if reporter.callback is not None and rms_json is None:
        try:
            sample_rate, channels = audio_format(source)
            native = {"sample_rate": sample_rate, "channels": channels}
        except (OSError, ValueError, IndexError, KeyError, subprocess.SubprocessError):
            pass  # Missing native metadata must never substitute the 16 kHz working rate.
    reporter.emit("received", duration_s=duration, **native)
    reporter.emit("loading_done", duration_s=duration)
    if progress is not None:
        progress("scoring audio (cached AST)" if scores_csv is not None else "scoring audio (AST)")
    if scores_csv is not None:
        scores = pd.read_csv(scores_csv)
        reporter.emit("ast_start", n_windows=len(scores), device="CPU", cached=True,
                      window_s=config.win_s, hop_s=config.hop_s)
        reporter.emit("ast_progress", windows_done=len(scores))
    else:
        active_scorer = scorer if scorer is not None else ASTScorer()
        n_windows = ceil(duration / config.hop_s)
        reporter.emit("ast_start", n_windows=n_windows,
                      device="T4 GPU" if str(getattr(active_scorer, "device", "cpu")).startswith("cuda") else "CPU",
                      window_s=config.win_s, hop_s=config.hop_s)
        if reporter.callback is not None and on_ast_progress_supported(active_scorer):
            scores = active_scorer.score_windows(y, config.sample_rate, config.win_s, config.hop_s,
                                                 progress=lambda done: reporter.emit("ast_progress", windows_done=done))
        else:
            scores = active_scorer.score_windows(y, config.sample_rate, config.win_s, config.hop_s)
            reporter.emit("ast_progress", windows_done=len(scores))
    scores["music"] = scores[["singing", "instrument"]].max(axis=1)
    frame_labels, gate = frame_labels_from_scores(scores, rms, config)
    regions = merge_frames(frame_labels, config.hop_s)
    if regions:
        regions[-1].end = duration
    regions = absorb_short(regions, config)
    frame_scores = frame_scores_from_windows(scores, duration, config.hop_s)
    for region in regions:
        overlapping = frame_scores[(frame_scores["start"] < region.end) & (frame_scores["end"] > region.start)]
        weights = (overlapping["end"].clip(upper=region.end) - overlapping["start"].clip(lower=region.start))
        region.mean_scores = {
            group: float((overlapping[group] * weights).sum() / weights.sum()) for group in (*SCORE_GROUPS, "music")
        }
    speech = [region for region in regions if region.kind == "speech"]
    singing = [region for region in regions if region.kind == "music"]
    reporter.emit("regions_found", n_singing=len(singing), n_speech=len(speech),
                  total_singing_s=sum(region.end - region.start for region in singing),
                  speech_regions=[{"start": region.start, "end": region.end} for region in speech])
    # Keep completed scoring work even if the downstream command service fails.
    destination.mkdir(parents=True, exist_ok=True)
    score_checkpoint = destination / "window_scores.tmp"
    scores.to_csv(score_checkpoint, index=False)
    score_checkpoint.replace(destination / "window_scores.csv")
    if progress is not None:
        progress("reading commands (Spark)" if isinstance(parser, MuseSparkLabelParser) else "reading commands (mock)")
    padding = getattr(parser, "audio_padding_s", 0.0)
    clips = [(y[round(max(0.0, region.start - padding) * config.sample_rate):
                 round(min(duration, region.end + padding) * config.sample_rate)].copy(), config.sample_rate, region)
             for region in speech]
    if isinstance(parser, MuseSparkLabelParser):
        parser.configure_prompt(config.spark_prompt_v2)
        parser.preflight(clips)

    def parse_region(audio: np.ndarray, sr: int, region: Region) -> CommandLabel:
        """Return the original parser result after reporting this completed speech interpretation."""
        label = parser.parse(audio, sr, region)
        cached = False
        if isinstance(parser, MuseSparkLabelParser):
            with parser.lock:
                cached = next((bool(record.get("cached")) for record in reversed(parser.records)
                               if record["start"] == region.start and record["end"] == region.end), False)
        reporter.emit("spark_result", start=region.start, end=region.end, heard=label.heard or "",
                      intent=label.intent or ("file" if label.is_command else "none"), section=label.section,
                      target=label.target, scope=label.scope, confidence=label.confidence, cached=cached)
        return label

    if isinstance(parser, MuseSparkLabelParser) and config.parallel_spark:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(parse_region, audio, sr, region) for audio, sr, region in clips]
            commands = {region.start: future.result() for (_, _, region), future in zip(clips, futures)}
    else:
        commands = {region.start: parse_region(audio, sr, region) for audio, sr, region in clips}
    parser_warnings = []
    skipped = len(getattr(parser, "skipped_regions", ()))
    if skipped:
        parser_warnings.append(f"Speech command budget reached; {skipped} region(s) treated as none.")
        reporter.emit("spark_limit", n_skipped=skipped, cap=parser.max_spark_calls)
    for region in speech:
        label = commands[region.start]
        if label.target == "next" and not config.support_target_next:
            parser_warnings.append(f"label-before-take not supported: {label.heard or ''}")
        if label.intent in ("performance", "noise"):
            region.kind = "music" if label.intent == "performance" else "silence"
            del commands[region.start]
    regions = merge_adjacent(regions)
    if progress is not None:
        progress("cutting takes")
    takes, warnings = cut_takes(regions, commands, str(source), config)
    warnings = parser_warnings + warnings
    takes.extend(find_unlabeled(regions, [take for take, _ in takes], str(source), config))
    takes, trim_warnings = trim_takes(takes, frame_scores, config)
    warnings.extend(trim_warnings)
    songmap = build_songmap(takes, str(source))
    songmap.parser = "spark" if isinstance(parser, MuseSparkLabelParser) else "mock"
    destination.mkdir(parents=True, exist_ok=True)
    if progress is not None:
        progress("exporting clips")
    if not no_export:
        songmap = export_clips(y, config.sample_rate, songmap, destination, config.fade_ms)
    songmap.to_json(destination / "songmap.json")
    (destination / "regions.json").write_text(
        json.dumps({"silence_gate_db": gate, "regions": [region.to_dict() for region in regions]},
                   indent=2, allow_nan=False), encoding="utf-8",
    )
    (destination / "warnings.txt").write_text("".join(f"{warning}\n" for warning in warnings), encoding="utf-8")
    for section, section_takes in songmap.sections.items():
        for index, take in enumerate(section_takes, 1):
            name = f"{'Idea' if section == 'unsorted' else 'Take' if section == 'unlabeled' else section.capitalize()} {index}"
            reporter.emit("take_cut", take_id=take.id, name=name, section=section, start=take.start, end=take.end)
    reporter.emit("done", n_takes=sum(len(takes) for section, takes in songmap.sections.items() if section != "rejected"),
                  n_sections=sum(bool(takes) for section, takes in songmap.sections.items() if section != "rejected"),
                  elapsed_s=perf_counter() - reporter.started)
    return songmap, regions, warnings


def on_ast_progress_supported(scorer: Scorer) -> bool:
    """Return whether the scorer accepts optional progress without changing custom scorer contracts."""
    from inspect import signature

    return "progress" in signature(scorer.score_windows).parameters


def main(argv: list[str] | None = None) -> int:
    """Return zero after running the CLI pipeline and printing a compact result summary."""
    cli = argparse.ArgumentParser(description="Organize a songwriting memo using local scores and mock commands.")
    cli.add_argument("audio_path", type=Path)
    cli.add_argument("--parser", choices=("mock", "spark"), default="mock")
    cli.add_argument("--mock-labels", type=Path)
    cli.add_argument("--out", type=Path, required=True)
    cli.add_argument("--scores-csv", type=Path, help="Load window scores and skip AST entirely")
    cli.add_argument("--rms-json", type=Path, help="Load frame RMS without opening audio (mock parser only)")
    cli.add_argument("--no-export", action="store_true", help="Write metadata without WAV clips")
    cli.add_argument("--duration-s", type=float, help="Memo duration; defaults to meta.json with --rms-json")
    args = cli.parse_args(argv)
    if args.parser == "mock" and args.mock_labels is None:
        cli.error("--mock-labels is required with --parser mock")
    parser = (MockLabelParser(args.mock_labels) if args.parser == "mock" else
              MuseSparkLabelParser(args.out / "spark_cache.json", KeeperConfig().max_spark_calls,
                                   prompt_v2=KeeperConfig().spark_prompt_v2))
    songmap, regions, warnings = run_pipeline(
        args.audio_path, parser, args.out, scores_csv=args.scores_csv,
        rms_json=args.rms_json, no_export=args.no_export, duration_s=args.duration_s,
    )
    print("Regions: " + str(dict(Counter(region.kind for region in regions))))
    print("Takes: " + str({section: len(takes) for section, takes in songmap.sections.items()}))
    for section, takes in songmap.sections.items():
        for take in takes:
            print(f"  {section} {take.variant}: {take.start:.2f}-{take.end:.2f} s")
    metadata = json.loads((args.out / "regions.json").read_text(encoding="utf-8"))
    print(f"Silence gate: {metadata['silence_gate_db']:.2f} dB")
    print(f"Warnings: {len(warnings)}")
    for warning in warnings:
        print(warning)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
