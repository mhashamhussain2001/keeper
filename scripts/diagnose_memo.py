"""Diagnose cached memo predictions and benchmark only the first 60 seconds of audio."""

import argparse
import hashlib
import json
import os
import platform
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import rankdata

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.audacity_labels import TruthRow, read_audacity_labels
from eval.evaluate import frame_accuracy, take_boundary_error
from keeper.audio_io import load_audio, rms_db_per_frame
from keeper.config import FRAME_KINDS, KeeperConfig
from keeper.frames import frame_labels_from_scores, frame_scores_from_windows, island_vote, silence_gate_db
from keeper.regions import absorb_short, merge_frames
from keeper.types import FrameLabel, Region, SongMap


ROOT = Path(__file__).resolve().parents[1]


def activity_truth(path: Path) -> list[TruthRow]:
    """Return validated activity truth while preserving the singing/instrument distinction."""
    validated = read_audacity_labels(path)
    texts = [line.split("\t", 2)[2].strip() for line in path.read_text(encoding="utf-8-sig").splitlines()
             if line.strip()]
    aliases = {"sing": "singing", "inst": "instrument"}
    return [(start, end, aliases.get(text.casefold(), text.casefold()))
            for (start, end, _), text in zip(validated, texts) if not text.startswith(("cmd:", "take:"))]


def truth_at(truth: list[TruthRow], time_s: float) -> str:
    """Return midpoint activity truth, defaulting to silence in gaps."""
    return next((kind for start, end, kind in truth if start <= time_s < end), "silence")


def region_at(regions: list[Region], time_s: float) -> FrameLabel:
    """Return the predicted region kind containing a frame midpoint."""
    return next(region.kind for region in regions if region.start <= time_s < region.end)


def stage_trace(
    scores: pd.DataFrame, rms: np.ndarray, duration: float, truth: list[TruthRow],
    cfg: KeeperConfig | None = None,
) -> pd.DataFrame:
    """Return a frame-by-frame trace verified against the unchanged frame-label implementation."""
    config = cfg or KeeperConfig()
    frames = frame_scores_from_windows(scores, duration, config.hop_s)
    gate = silence_gate_db(rms, config)
    raw: list[FrameLabel] = []
    for energy, speech in zip(rms, frames["speech"]):
        raw.append("silence" if energy < gate else "speech" if speech >= config.speech_min else "music")
    voted = island_vote(raw, config)
    radius = config.mode_filter_frames // 2
    mode: list[FrameLabel] = []
    for index, original in enumerate(voted):
        counts = Counter(voted[max(0, index - radius):index + radius + 1])
        winners = [kind for kind, count in counts.items() if count == max(counts.values())]
        mode.append(winners[0] if len(winners) == 1 else original)
    actual_mode, actual_gate = frame_labels_from_scores(scores, rms, config)
    if mode != actual_mode or gate != actual_gate:
        raise ValueError("Diagnostic reconstruction differs from keeper.frames")
    regions = merge_frames(mode, config.hop_s)
    if regions:
        regions[-1].end = duration
    regions = absorb_short(regions, config)
    midpoints = (frames["start"] + frames["end"]) / 2
    frames["truth"] = [truth_at(truth, time_s) for time_s in midpoints]
    frames["rms_db"], frames["gate_db"] = rms, gate
    frames["raw"], frames["vote"], frames["mode"] = raw, voted, mode
    frames["absorb"] = [region_at(regions, time_s) for time_s in midpoints]
    frames["final"] = frames["absorb"]
    for key in ("island_start", "island_end", "island_length"):
        frames[key] = np.nan
    frames["island_skipped"] = False
    start = 0
    while start < len(raw):
        if raw[start] == "silence":
            start += 1
            continue
        end = start + 1
        while end < len(raw) and raw[end] != "silence":
            end += 1
        length = (end - start) * config.hop_s
        frames.loc[start:end - 1, ["island_start", "island_end", "island_length", "island_skipped"]] = [
            start * config.hop_s, end * config.hop_s, length, length > config.island_max_s]
        start = end
    return frames


def feature_scores(scores: pd.DataFrame, duration: float) -> dict[str, np.ndarray]:
    """Return the six requested scores projected with the pipeline's overlapping-window averaging."""
    cfg = KeeperConfig()
    grouped = frame_scores_from_windows(scores, duration, cfg.hop_s)
    result = {"singing group": grouped["singing"].to_numpy(),
              "instrument group": grouped["instrument"].to_numpy()}
    for name in ("Singing", "Humming", "Music"):
        projected = scores[["start", "end"]].copy()
        projected["singing"], projected["speech"], projected["instrument"] = scores[name], 0.0, 0.0
        result[name] = frame_scores_from_windows(projected, duration, cfg.hop_s)["singing"].to_numpy()
    result["singing - instrument"] = result["singing group"] - result["instrument group"]
    return {name: result[name] for name in ("singing group", "Singing", "Humming", "instrument group",
                                           "Music", "singing - instrument")}


def roc_auc(positive: np.ndarray, values: np.ndarray) -> float | None:
    """Return tie-corrected ROC AUC for higher scores predicting singing, or None for one class."""
    count = int(positive.sum())
    negatives = len(positive) - count
    if count == 0 or negatives == 0:
        return None
    ranks = rankdata(values, method="average")
    return float((ranks[positive].sum() - count * (count + 1) / 2) / (count * negatives))


def best_threshold(positive: np.ndarray, values: np.ndarray, direction: int) -> tuple[float, float]:
    """Return the threshold maximizing balanced accuracy, conservatively breaking ties toward singing."""
    if not positive.any() or positive.all():
        raise ValueError("Balanced accuracy requires both singing and instrument truth")
    oriented = values * direction
    candidates = np.append(np.unique(oriented), np.nextafter(oriented.max(), np.inf))
    best_score, threshold = -1.0, float(candidates[0])
    for candidate in candidates:
        predicted = oriented >= candidate
        score = float((predicted[positive].mean() + (~predicted[~positive]).mean()) / 2)
        if score > best_score:
            best_score, threshold = score, float(candidate)
    return threshold * direction, best_score


def trim_simulation(songmap: SongMap, frames: pd.DataFrame, vocal: np.ndarray) -> SongMap:
    """Return simulated edge-trimmed takes retaining internal frames and bounded pre/post roll."""
    cfg = KeeperConfig()
    sections = {}
    for section, takes in songmap.sections.items():
        sections[section] = []
        for take in takes:
            keep = frames[(frames["start"] < take.end) & (frames["end"] > take.start) & vocal]
            if keep.empty:
                continue
            start = max(take.start, float(keep.iloc[0]["start"]) - cfg.pre_roll_s)
            end = min(take.end, float(keep.iloc[-1]["end"]) + cfg.post_roll_s)
            sections[section].append(replace(take, start=start, end=end))
    return SongMap(songmap.source_file, sections)


def silence_runs(frames: pd.DataFrame) -> tuple[list[list[Any]], dict[str, int]]:
    """Return contiguous missed-silence runs and a disjoint attribution of their frame losses."""
    mask = (frames["truth"] == "silence") & (frames["final"] == "music")
    causes = {"louder_than_gate": 0, "mode_filter": 0, "absorb_short": 0, "other": 0}
    for _, frame in frames[mask].iterrows():
        if frame["raw"] != "silence":
            causes["louder_than_gate"] += 1
        elif frame["mode"] != "silence":
            causes["mode_filter"] += 1
        elif frame["absorb"] != "silence":
            causes["absorb_short"] += 1
        else:
            causes["other"] += 1
    rows: list[list[Any]] = []
    index = 0
    while index < len(frames):
        if not mask.iloc[index]:
            index += 1
            continue
        end = index + 1
        while end < len(frames) and mask.iloc[end]:
            end += 1
        run = frames.iloc[index:end]
        duration = float(run.iloc[-1]["end"] - run.iloc[0]["start"])
        rows.append([f"{run.iloc[0]['start']:.3f}", f"{run.iloc[-1]['end']:.3f}", f"{duration:.3f}",
                     len(run), f"{run['rms_db'].median():.3f}", f"{run.iloc[0]['gate_db']:.3f}",
                     "yes" if duration < 1.0 else "no"])
        index = end
    return rows, causes


def command_cause(frames: pd.DataFrame, start: float, end: float) -> str:
    """Return the last processing step that eliminated speech overlapping a missed command."""
    affected = frames[(frames["start"] < end) & (frames["end"] > start)]
    if (affected["final"] == "speech").any():
        return "something else: overlapping speech survived; inspect command association"
    if not (affected["raw"] == "speech").any():
        if (affected["speech"] < KeeperConfig().speech_min).all():
            return f"score below speech_min: maximum frame speech score {affected['speech'].max():.6f} < {KeeperConfig().speech_min:.2f}"
        return "something else: the RMS silence gate removed the speech candidates"
    for before, after, cause in (("raw", "vote", "island vote"), ("vote", "mode", "mode filter"),
                                 ("mode", "absorb", "absorb_short")):
        if (affected[before] == "speech").any() and not (affected[after] == "speech").any():
            if cause == "island vote":
                details = []
                for island_start in affected.loc[affected["raw"] == "speech", "island_start"].unique():
                    island = frames[frames["island_start"] == island_start]
                    winner, count = Counter(island["raw"]).most_common(1)[0]
                    details.append(f"{winner} won {count}/{len(island)} raw frames in the "
                                   f"{island.iloc[0]['island_length']:.1f} s island "
                                   f"({island.iloc[0]['island_start']:.1f}-{island.iloc[0]['island_end']:.1f} s); "
                                   f"length <= {KeeperConfig().island_max_s:.1f} s, so voting was not skipped")
                return cause + ": " + "; ".join(details)
            return cause
    return "something else: inspect the frame trace"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    """Return a Markdown table with escaped values."""
    return "\n".join("| " + " | ".join(str(value).replace("|", "\\|") for value in row) + " |"
                     for row in [headers, ["---"] * len(headers), *rows])


def cpu_model() -> str:
    """Return the available CPU model string without requiring additional packages."""
    if os.name == "nt":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return platform.processor() or "unavailable"


def benchmark_ast(source: Path, destination: Path) -> dict[str, Any]:
    """Return checkpointed first-60-second AST timings without modifying pipeline files or defaults."""
    import torch
    import keeper.segment_ast as ast_module

    cfg = KeeperConfig()
    # Slice before inference; no benchmark window reads past this 60-second input.
    y = load_audio(source, cfg.sample_rate)[:60 * cfg.sample_rate].copy()
    original_batch = ast_module.AST_BATCH_SIZE
    original_threads = torch.get_num_threads()
    cpu_count = os.cpu_count() or original_threads
    settings = [(original_batch, original_threads), (16, original_threads),
                (32, original_threads), (original_batch, cpu_count)]
    identity = {"torch": torch.__version__, "threads": original_threads, "cpu_count": cpu_count,
                "cpu_model": cpu_model(), "audio_s": len(y) / cfg.sample_rate,
                "audio_sha256": hashlib.sha256(y.tobytes()).hexdigest(), "default_batch": original_batch}
    checkpoint = destination / "diagnostics_benchmark.json"
    result: dict[str, Any] = {"environment": identity, "runs": []}
    if checkpoint.is_file():
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        if saved["environment"] != identity:
            raise ValueError("Benchmark checkpoint environment/input differs; preserve it and use another output folder")
        result = saved
    done = {(row["batch"], row["threads"]) for row in result["runs"]}
    if all(setting in done for setting in settings):
        return result
    started = perf_counter()
    scorer = ast_module.ASTScorer()
    result["model_load_s"] = perf_counter() - started
    reference_path = destination / f"diagnostics_benchmark_b{original_batch}_t{original_threads}.csv"
    reference = pd.read_csv(reference_path) if reference_path.is_file() else None
    try:
        for batch, threads in settings:
            if (batch, threads) in done:
                continue
            print(f"Benchmark: first {len(y) / cfg.sample_rate:.1f} s, batch={batch}, threads={threads}", flush=True)
            ast_module.AST_BATCH_SIZE = batch
            torch.set_num_threads(threads)
            started = perf_counter()
            row: dict[str, Any] = {"batch": batch, "threads": threads}
            try:
                scores = scorer.score_windows(y, cfg.sample_rate, cfg.win_s, cfg.hop_s)
                seconds = perf_counter() - started
                path = destination / f"diagnostics_benchmark_b{batch}_t{threads}.csv"
                scores.to_csv(path, index=False)
                if reference is None:
                    reference = scores.copy()
                columns = [column for column in scores if column not in ("start", "end")]
                difference = float(np.max(np.abs(scores[columns].to_numpy() - reference[columns].to_numpy())))
                row.update(seconds=seconds, windows=len(scores), seconds_per_window=seconds / len(scores),
                           max_abs_diff=difference, identical=difference < 1e-5)
                print(f"Finished: {seconds:.2f} s, max abs score difference={difference:.9g}", flush=True)
            except Exception as exc:
                row.update(error=f"{type(exc).__name__}: {exc}", seconds=perf_counter() - started)
                print(f"Benchmark failed: {row['error']}", flush=True)
            result["runs"].append(row)
            checkpoint.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    finally:
        ast_module.AST_BATCH_SIZE = original_batch
        torch.set_num_threads(original_threads)
    return result


def collect_diagnostics(name: str, root: Path) -> dict[str, Any]:
    """Return verified cached-score diagnostics, an edge-trim simulation, and memo1 transfer checks."""
    # Replay the saved pre-fix evaluation rather than silently changing its provenance.
    cfg = KeeperConfig(protect_speech_runs=False, edge_trim=False)
    source = root / "data" / f"{name}.wav"
    scores = pd.read_csv(root / "outputs" / f"{name}_scores.csv")
    truth_path = root / "data" / f"{name}_labels.txt"
    truth = activity_truth(truth_path)
    all_truth = read_audacity_labels(truth_path)
    destination = root / "outputs" / f"{name}_eval"
    audio = load_audio(source, cfg.sample_rate)
    duration = len(audio) / cfg.sample_rate
    frames = stage_trace(scores, rms_db_per_frame(audio, cfg.sample_rate, cfg.hop_s), duration, truth, cfg)
    metadata = json.loads((destination / "regions.json").read_text(encoding="utf-8"))
    regions = [Region(**row) for row in metadata["regions"]]
    final = [region_at(regions, time_s) for time_s in (frames["start"] + frames["end"]) / 2]
    if list(frames["absorb"]) != final or abs(metadata["silence_gate_db"] - frames.iloc[0]["gate_db"]) > 1e-9:
        raise ValueError("Reconstructed stages do not match the saved memo evaluation")
    frames["final"] = final
    frames.to_csv(destination / "diagnostics_frames.csv", index=False)
    features = feature_scores(scores, duration)
    singing = (frames["truth"] == "singing").to_numpy()
    instrument = (frames["truth"] == "instrument").to_numpy()
    eligible = singing | instrument
    feature_stats = []
    for label, values in features.items():
        auc = roc_auc(singing[eligible], values[eligible])
        feature_stats.append({"score": label, "singing_median": float(np.median(values[singing])),
                              "instrument_median": float(np.median(values[instrument])), "auc": auc})
    selected = max(feature_stats, key=lambda row: max(row["auc"], 1 - row["auc"]))
    direction = 1 if selected["auc"] >= 0.5 else -1
    values = features[selected["score"]]
    threshold, balanced = best_threshold(singing[eligible], values[eligible], direction)
    vocal = values * direction >= threshold * direction
    chosen = {**selected, "direction": direction, "threshold": threshold, "balanced_accuracy": balanced,
              "singing_recall": float(vocal[singing].mean()),
              "instrument_recall": float((~vocal[instrument]).mean())}
    songmap = SongMap.from_json(destination / "songmap.json")
    simulated = trim_simulation(songmap, frames, vocal)
    take_truth = [row for row in all_truth if row[2].startswith("take:")]
    original_errors = take_boundary_error(songmap, take_truth)
    simulated_errors = take_boundary_error(simulated, take_truth)
    fixture = root / "tests" / "fixtures" / "test_memo"
    fixture_duration = float(json.loads((fixture / "meta.json").read_text())["duration_s"])
    fixture_scores = pd.read_csv(fixture / "window_scores.csv")
    fixture_truth = activity_truth(fixture / "truth_labels.txt")
    fixture_frames = frame_scores_from_windows(fixture_scores, fixture_duration, cfg.hop_s)
    fixture_labels = np.array([truth_at(fixture_truth, time_s)
                               for time_s in (fixture_frames["start"] + fixture_frames["end"]) / 2])
    fixture_singing = fixture_labels == "singing"
    fixture_features = feature_scores(fixture_scores, fixture_duration)
    rejected = fixture_features[chosen["score"]] * direction < threshold * direction
    memo1 = {"singing_frames": int(fixture_singing.sum()),
             "instrument_frames": int((fixture_labels == "instrument").sum()),
             "singing_group_median": float(np.median(fixture_features["singing group"][fixture_singing])),
             "trim_candidates": int((rejected & fixture_singing).sum()),
             "trim_share": float(rejected[fixture_singing].mean())}
    run_rows, causes = silence_runs(frames)
    metrics = frame_accuracy(regions, [row for row in all_truth if row[2] != "unclear"], cfg.hop_s,
                             exclude_intervals=[(start, end) for start, end, label in all_truth if label == "unclear"])
    missed = [(start, end, label) for start, end, label in all_truth if label.startswith("cmd:")
              and not any(region.kind == "speech" and min(end, region.end) > max(start, region.start)
                          for region in regions)]
    return {"frames": frames, "features": feature_stats, "best": chosen, "memo1": memo1,
            "songmap": songmap, "simulated": simulated, "take_truth": take_truth,
            "current_errors": original_errors, "simulated_errors": simulated_errors,
            "silence_runs": run_rows, "silence_causes": causes, "metrics": metrics,
            "missed": missed, "duration": duration}


def render_diagnostics(name: str, data: dict[str, Any], benchmark: dict[str, Any] | None) -> str:
    """Return the complete evidence report with diagnostic-only proposals and simulation caveats."""
    cfg = KeeperConfig()
    frames = data["frames"]
    best = data["best"]
    memo1 = data["memo1"]
    gate = float(frames.iloc[0]["gate_db"])
    lines = [f"# {name} diagnostics", "",
             f"Audio: {data['duration']:.6f} s; {len(frames)} frames at {cfg.hop_s:.1f} s hop. "
             f"Scores: outputs/{name}_scores.csv. Full-memo scores were read, never regenerated.",
             "Stage reconstruction replays the saved baseline with protect_speech_runs=False and edge_trim=False.",
             "No keeper files, thresholds, clips, or evaluated song maps were changed. "
             "The reconstructed stages match keeper.frames and the saved final regions exactly.", "",
             "## A. Missed commands: processing trace", "",
             f"speech_min={cfg.speech_min:.2f}; gate={gate:.6f} dB; island_max_s={cfg.island_max_s:.1f}; "
             f"island_vote_min={cfg.island_vote_min:.2f}; mode filter={cfg.mode_filter_frames} frames. "
             "Truth uses frame midpoints; uncovered time is silence. S/I scores are singing/instrument groups. "
             "Island length follows the implementation's frame count times hop; '-' means raw silence, outside any sound island."]
    focuses = [(95.0, 100.0), (112.0, 125.0)] if name == "memo2" else [
        (max(0.0, start - 2.0), end + 2.0) for start, end, _ in data["missed"]]
    for start, end in focuses:
        lines += ["", f"Frames {start:.1f}-{end:.1f} s (end exclusive):", ""]
        rows = []
        for _, frame in frames[(frames["start"] >= start) & (frames["start"] < end)].iterrows():
            has_island = pd.notna(frame["island_start"])
            rows.append([f"{frame['start']:.1f}", frame["truth"], f"{frame['rms_db']:.3f}",
                         f"{gate:.3f}", f"{frame['speech']:.6f}", f"{frame['singing']:.6f}",
                         f"{frame['instrument']:.6f}", frame["raw"], frame["vote"], frame["mode"],
                         frame["absorb"], frame["final"],
                         f"{frame['island_start']:.1f}" if has_island else "-",
                         f"{frame['island_end']:.1f}" if has_island else "-",
                         f"{frame['island_length']:.1f}" if has_island else "-",
                         ("yes" if frame["island_skipped"] else "no") if has_island else "-"])
        lines.append(table(["Start", "Truth", "RMS dB", "Gate dB", "Speech", "S group", "I group",
                            "Raw", "Vote", "Mode", "Absorb", "Final", "Island start", "Island end", "Length", ">max skipped"], rows))
    causes = []
    lines += ["", "Command conclusions:", ""]
    for start, end, label in data["missed"]:
        cause = command_cause(frames, start, end)
        causes.append(cause)
        lines.append(f"- {start:.6f}-{end:.6f} s, {label}: {cause}.")
    lines += ["", "## B. Singing versus instrument-only frames", "",
              f"Eligible midpoint truth: {(frames['truth'] == 'singing').sum()} singing frames and "
              f"{(frames['truth'] == 'instrument').sum()} instrument frames; all other classes excluded. "
              "AUC below assumes larger values mean singing (ties receive half credit).", "",
              table(["Per-frame score", "Singing median", "Instrument median", "ROC AUC"], [
                  [row["score"], f"{row['singing_median']:.6f}", f"{row['instrument_median']:.6f}",
                   f"{row['auc']:.6f}"] for row in data["features"]]), ""]
    operator = ">=" if best["direction"] == 1 else "<="
    lines += [f"Best single score by direction-adjusted AUC: **{best['score']}**, "
              f"AUC={max(best['auc'], 1 - best['auc']):.6f}. Singing rule: "
              f"**score {operator} {best['threshold']:.17g}**. "
              f"Threshold maximizes balanced accuracy on these same memo2 frames: **{best['balanced_accuracy']:.6f}** "
              f"(singing recall {best['singing_recall']:.6f}, instrument recall {best['instrument_recall']:.6f}).",
              "Score and direction are selected by AUC; the threshold is selected by balanced accuracy. "
              "This is an in-sample diagnostic, not evidence of a general-purpose safe threshold.", "",
              "### Edge-trimming simulation only", "",
              f"Trim only leading/trailing instrument-classified frame runs; retain all internal frames. "
              f"Reapply {cfg.pre_roll_s:.1f} s pre-roll and {cfg.post_roll_s:.1f} s post-roll, clamped to each original take. "
              "Drop a simulated take only if no singing-classified frame overlaps it. "
              "Each truth take is independently rematched by maximum positive overlap, across sections, as in the existing secondary metric.", ""]
    current_takes = [take for takes in data["songmap"].sections.values() for take in takes]
    simulated_takes = [take for takes in data["simulated"].sections.values() for take in takes]
    dropped = sorted({take.id for take in current_takes} - {take.id for take in simulated_takes})
    trim_rows = []
    for truth, before, after in zip(data["take_truth"], data["current_errors"]["per_take"],
                                   data["simulated_errors"]["per_take"]):
        matched = next((take for take in simulated_takes if take.id == after["predicted_id"]), None)
        trim_rows.append([truth[2], f"{truth[0]:.2f}-{truth[1]:.2f}", before["predicted_id"] or "MISS",
                          f"{before['start_error_s']:.3f}/{before['end_error_s']:.3f}" if before["predicted_id"] else "-",
                          after["predicted_id"] or "MISS", f"{matched.start:.3f}-{matched.end:.3f}" if matched else "-",
                          f"{after['start_error_s']:.3f}/{after['end_error_s']:.3f}" if matched else "-"])
    lines += [table(["Truth take", "Truth seconds", "Current match", "Current start/end error s",
                     "Simulated match", "Simulated seconds", "New start/end error s"], trim_rows), "",
              f"Simulated predictions retained: {len(simulated_takes)}/{len(current_takes)}; "
              f"truth misses: {data['current_errors']['misses']} -> {data['simulated_errors']['misses']}. "
              "Rejected truth remains a diagnostic row; trimming does not implement rejection or change section assignments.", "",
              "Dropped in simulation: " + (", ".join(dropped) if dropped else "none") + ". "
              "Edge-only trimming cannot split multiple sung phrases or remove leading material that this score still classifies as singing.", "",
              "### Memo1 transfer check", "",
              f"Fixture singing frames: {memo1['singing_frames']}; instrument truth frames: {memo1['instrument_frames']}. "
              f"ROC AUC is undefined without instrument truth. Singing-group median on truth singing: "
              f"**{memo1['singing_group_median']:.6f}**. The memo2 rule classifies "
              f"**{memo1['trim_candidates']}/{memo1['singing_frames']} ({memo1['trim_share']:.2%})** "
              "of memo1 singing frames as instrument (trim candidates). "
              "Internal candidates would remain under edge-only trimming; this is the frame-level risk rate requested, not a claim that every candidate lies at a take edge.",
              ("The memo2 threshold rejects annotated memo1 singing, so it is unsafe on those observed frames."
               if memo1["trim_candidates"] else
               "The threshold preserves all annotated memo1 singing in this check; this small fixture has no instrument truth, so broader safety is unproven."),
              "", "## C. Truth silence predicted as music", "",
              table(["Start s", "End s", "Length s", "Frames", "Median RMS dB", "Gate dB", "<1 s?"], data["silence_runs"]), ""]
    silence = data["silence_causes"]
    lost = sum(silence.values())
    lines += [f"Total: **{lost} frames**. Disjoint cause attribution: "
              f"**{silence['louder_than_gate']}** were already above/equal to the RMS gate and labeled non-silence before smoothing; "
              f"**{silence['mode_filter']}** raw-silence frames were filled by the mode filter; "
              f"**{silence['absorb_short']}** survived through the mode filter and were absorbed by absorb_short; "
              f"other={silence['other']}.",
              "The latter two categories count short-gap losses. RMS evidence alone cannot identify whether the above-gate audio was piano sustain or room noise; listening would be needed.",
              "RMS integrates the whole 0.5-second frame while truth is assigned at its midpoint: an edge frame scored as silence can still contain music in part of the frame. Above-gate boundary energy is therefore not automatically noise or a gate defect.",
              "", "## D. First-60-second AST benchmark", ""]
    if benchmark is None:
        lines += ["Benchmark still pending; this intermediate report is cached-score analysis only."]
        performance = "Pending measurement"
        runtime_fix = "Complete benchmark before proposing a runtime change"
        runtime_effect = "Pending measurement; no frame-rule changes proposed"
    else:
        env = benchmark["environment"]
        lines += [f"torch={env['torch']}; torch.get_num_threads()={env['threads']}; os.cpu_count()={env['cpu_count']}; "
                  f"CPU={env['cpu_model']}. Input={env['audio_s']:.1f} s only.",
                  f"Model load: {benchmark.get('model_load_s', 0):.3f} s, excluded from score timings. "
                  "Each configuration is one sequential trial with the same model/input; no separate warm-up. "
                  "The 60-second input is truncated before scoring, so its final windows are padded within that prefix. "
                  "Some diagnostic/test work overlapped the timing run; small timing differences should be treated as noise. "
                  "Completed timing checkpoints for this exact input and environment are reused on rerun.", ""]
        if name == "memo2":
            lines += ["The historical full-memo run in SESSION_LOG took approximately 2,866 s for 753 windows "
                      "(3.806 s/window), with batch 8 and four threads. Each minute produces 120 overlapping 2-second windows. "
                      "This CPU-only benchmark measures throughput and configuration effects; it cannot fully attribute historical "
                      "latency to one cause or explain run-to-run load without additional profiling.", ""]
        rows = []
        successful = [row for row in benchmark["runs"] if "error" not in row]
        runtime_fix = "Investigate failed benchmark configurations"
        runtime_effect = "No validated throughput change"
        performance = "No successful configuration"
        for row in benchmark["runs"]:
            rows.append([row["batch"], row["threads"], f"{row['seconds']:.3f}",
                         f"{row['seconds_per_window']:.6f}" if "error" not in row else "-",
                         f"{row['max_abs_diff']:.9g}" if "error" not in row else "-",
                         str(row.get("identical", row.get("error")))])
        lines += [table(["Batch", "Threads", "Seconds", "Seconds/window", "Max abs diff", "Diff <1e-5"], rows), ""]
        if successful:
            fastest = min(successful, key=lambda row: row["seconds"])
            speedup = successful[0]["seconds"] / fastest["seconds"]
            performance = f"Batch {fastest['batch']}, threads {fastest['threads']}: {fastest['seconds'] / env['audio_s']:.3f} min/min audio"
            default_fastest = (fastest["batch"], fastest["threads"]) == (env["default_batch"], env["threads"])
            runtime_fix = ("Keep measured defaults; profile CPU model/feature costs before other changes" if default_fastest
                           else "Validate the fastest measured configuration before changing defaults")
            runtime_effect = ("No speedup from tested changes; frame metrics unchanged" if default_fastest
                              else f"{speedup:.3f}x prefix throughput; check score differences before adopting")
            lines += [f"Fastest observed: **{performance}**. "
                      f"Speedup over the current batch/thread setting: {successful[0]['seconds'] / fastest['seconds']:.3f}x. "
                      f"All successful configurations satisfy max abs score diff <1e-5: "
                      f"**{all(row['identical'] for row in successful)}**. Compared all raw/group probability columns against the batch-{env['default_batch']} baseline.",
                      "Extrapolation is from this prefix, not a timed full-memo rerun; it excludes startup/export. "
                      "The earlier 47-minute full score run used four threads. Defaults were not changed; temporary batch/thread overrides were restored."]
    baseline = data["metrics"]["overall_accuracy"]
    upper = baseline + lost / data["metrics"]["scored_frames"]
    cause_summary = "; ".join(dict.fromkeys(cause.split(":", 1)[0] for cause in causes)) or "none"
    lines += ["", "## E. Evidence and proposed fixes (not implemented)", "",
              "Expected effects below are conditional proposals, except the explicitly measured trimming simulation and benchmark. "
              "No threshold selected here has been installed in the pipeline.", "",
              table(["Problem", "Evidence / cause", "Proposed fix", "Expected effect on memo2", "Risk to memo1"], [
                  ["Missed commands", f"{len(causes)} commands lost to {cause_summary}", "Protect speech candidates from music-majority island overwrites; validate on held-out commands",
                   "Potentially recover 2 command detections; the missed file command could recover 1 positive take hit; reject needs separate intent handling",
                   "Speech-rule changes can split singing; rerun fixture before any implementation"],
                  ["Instrument-only take edges", f"Best diagnostic score: {best['score']}; balanced accuracy {best['balanced_accuracy']:.3f}",
                   "Validate edge-only trimming; do not apply the fitted threshold yet", "Boundary-error simulation above; no section or frame-label changes",
                   f"{memo1['trim_share']:.2%} of fixture singing classified as trim candidates; no instrument truth"],
                  ["Silence false positives", f"{silence['louder_than_gate']} above gate; {silence['mode_filter']} mode-filled; {silence['absorb_short']} absorbed",
                   "Inspect frame/truth boundary alignment, then validate gate behavior and short-gap retention",
                   f"At most +{lost} correct frames: {baseline:.6f} -> {upper:.6f} if all corrected with no new errors",
                   "Gate or smoothing changes can cut quiet singing and alter existing take boundaries"],
                  ["Label-before-take", "target=next is mapped to preceding music; one command has no preceding music",
                   "Add explicit forward-target state in a future change", "Could recover the bridge hit; no expected frame-accuracy effect",
                   "Keep default previous-target behavior for fixture commands"],
                  ["Rejected take survives", "reject is a noncommand; orphan music remains eligible for unlabeled export",
                   "Design explicit rejection/exclusion semantics in a future change", "Could suppress the rejected unlabeled take; frame accuracy unchanged",
                   "No reject truth in memo1; add a dedicated regression before implementing"],
                  ["AST latency", performance, runtime_fix, runtime_effect,
                   "Verify fixture scores/labels if any numerical differences are material"],
              ])]
    return "\n".join(lines) + "\n"


def diagnose(name: str, root: Path = ROOT, run_benchmark: bool = True) -> str:
    """Return and save a cached-score diagnostic report with optional checkpointed CPU timings."""
    destination = root / "outputs" / f"{name}_eval"
    data = collect_diagnostics(name, root)
    benchmark = benchmark_ast(root / "data" / f"{name}.wav", destination) if run_benchmark else None
    report = render_diagnostics(name, data, benchmark)
    (destination / "diagnostics.md").write_text(report, encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    """Return zero after running the requested memo diagnostic phases."""
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("name")
    cli.add_argument("--benchmark-only", action="store_true")
    cli.add_argument("--analysis-only", action="store_true")
    args = cli.parse_args(argv)
    if not args.name or args.name in (".", "..") or any(char in args.name for char in "/\\:"):
        raise ValueError("Memo name must be a filename stem")
    destination = ROOT / "outputs" / f"{args.name}_eval"
    destination.mkdir(parents=True, exist_ok=True)
    if args.benchmark_only:
        benchmark_ast(ROOT / "data" / f"{args.name}.wav", destination)
    else:
        print(diagnose(args.name, run_benchmark=not args.analysis_only), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
