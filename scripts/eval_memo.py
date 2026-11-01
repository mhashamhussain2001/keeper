"""Evaluate a local memo with cached AST scores and truth-derived oracle commands."""

import argparse
import json
import shutil
import sys
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.audacity_labels import TruthRow, parse_command_truth, read_audacity_labels
from eval.evaluate import frame_accuracy, take_boundary_error
from keeper.config import FRAME_KINDS, KeeperConfig
from keeper.labels import MockLabelParser
from keeper.pipeline import run_pipeline
from keeper.types import CommandLabel, Region, SongMap


ROOT = Path(__file__).resolve().parents[1]


def _overlap(start: float, end: float, other_start: float, other_end: float) -> float:
    """Return the positive overlap duration of two intervals in seconds."""
    return max(0.0, min(end, other_end) - max(start, other_start))


def oracle_commands(
    truth: list[TruthRow], regions: list[Region],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Return mock labels and audited maximum-overlap truth-to-speech attachments."""
    speech = [region for region in regions if region.kind == "speech"]
    labels: dict[str, dict[str, Any]] = {}
    audit: list[dict[str, Any]] = []
    owners: dict[str, dict[str, Any]] = {}
    for start, end, text in sorted(truth, key=lambda row: row[0]):
        if not text.startswith("cmd:"):
            continue
        command = parse_command_truth(text)
        best = max(speech, key=lambda region: _overlap(start, end, region.start, region.end), default=None)
        if best is not None and _overlap(start, end, best.start, best.end) == 0.0:
            best = None
        row = {"start": start, "end": end, "text": text, "command": asdict(command),
               "speech_start": best.start if best else None, "speech_end": best.end if best else None,
               "status": "applied" if best else "missed (no speech overlap)"}
        audit.append(row)
        if best is None:
            continue
        # One parser result fits each speech region; retain the last truth command
        # chronologically if multiple commands collapse into that same region.
        key = str(best.start)
        if key in owners:
            owners[key]["status"] = "superseded (same speech region)"
        owners[key] = row
        label = CommandLabel(command.intent in ("file", "keep"),
                             (command.section or "unsorted") if command.intent == "file" else "unsorted",
                             None, command.note, 1.0)
        labels[key] = label.to_dict()
    return labels, audit


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    """Return a Markdown table with escaped cell values."""
    lines = [headers, ["---"] * len(headers), *rows]
    return "\n".join("| " + " | ".join(str(value).replace("|", "\\|").replace("\n", " ")
                                       for value in row) + " |" for row in lines)


def _span(start: float, end: float) -> str:
    """Return a compact start-end interval in seconds."""
    return f"{start:.3f}-{end:.3f}"


def _validate_truth(truth: list[TruthRow], duration: float) -> None:
    """Return None after checking activity truth is unambiguous and annotations fit the memo."""
    boundary = 0.0
    for start, end, label in sorted(truth, key=lambda row: row[0]):
        if end > duration:
            raise ValueError(f"Truth interval extends beyond memo duration: {label}")
        if label.startswith(("cmd:", "take:")):
            continue
        if label not in (*FRAME_KINDS, "unclear") or start == end or start < boundary:
            raise ValueError(f"Unknown, empty, or overlapping activity truth: {label} at {start}")
        boundary = end


def render_report(
    name: str, duration: float, metrics: dict[str, Any], songmap: SongMap, truth: list[TruthRow],
    audit: list[dict[str, Any]], warnings: list[str], elapsed_s: float, cache_used: bool,
) -> str:
    """Return the complete frame, oracle-command, take-hit, and secondary boundary report."""
    takes = [take for section in songmap.sections.values() for take in section]
    truth_takes = [row for row in truth if row[2].startswith("take:")]
    rejected = [row for row in truth_takes if row[2][5:].strip() in ("reject", "rejected")]
    positive = [row for row in truth_takes if row not in rejected]
    matrix = metrics["confusion_matrix"]
    correct = sum(matrix[kind][kind] for kind in FRAME_KINDS)
    excluded = metrics["excluded_predictions"]
    report = [f"# {name} evaluation", "",
              f"Duration: {duration:.6f} s. Frame hop: {KeeperConfig().hop_s:.3f} s.",
              f"AST scores: outputs/{name}_scores.csv ({'existing cache; not rescored' if cache_used else 'generated once; cached for future runs'}).",
              "AST uses the transformers numpy filterbank fallback; pipeline settings and thresholds are unchanged.",
              f"Evaluation runtime: {elapsed_s:.2f} s (includes two pipeline passes; includes scoring only if cache was absent).",
              "", "## Primary: frame accuracy", "",
              f"Accuracy: **{metrics['overall_accuracy']:.6f}** ({correct}/{metrics['scored_frames']} scored frames).",
              "Frame midpoints are scored; unannotated activity is silence. UNCLEAR frames are excluded from accuracy, precision, recall, and the 3x3 matrix.", "",
              _table(["Class", "Recall", "Precision"], [
                  [kind, f"{metrics['per_class'][kind]['recall']:.6f}",
                   f"{metrics['per_class'][kind]['precision']:.6f}"] for kind in FRAME_KINDS]), "",
              "Confusion matrix: truth rows, prediction columns.", "",
              _table(["Truth / predicted", *FRAME_KINDS], [
                  [kind, *[matrix[kind][guess] for guess in FRAME_KINDS]] for kind in FRAME_KINDS]), "",
              f"UNCLEAR: **{sum(excluded.values())} frames**; predictions: "
              + ", ".join(f"{kind}={excluded[kind]}" for kind in FRAME_KINDS) + ".", "",
              "## Oracle command attachments", "",
              "Truth commands are attached to the predicted speech region with greatest positive overlap (ties choose earliest). "
              "These are oracle intents, not measured speech-recognition results. "
              "If multiple truth commands map to one region, the latest command wins and earlier ones are marked superseded.", "",
              _table(["Truth interval (s)", "Command", "Predicted speech (s)", "Status"], [
                  [_span(row["start"], row["end"]), row["text"],
                   _span(row["speech_start"], row["speech_end"]) if row["speech_start"] is not None else "-",
                   row["status"]] for row in audit])]
    applied = sum(row["status"] == "applied" for row in audit)
    missed = [row for row in audit if row["speech_start"] is None]
    report += ["", f"Command detection: {len(audit) - len(missed)}/{len(audit)} truth commands overlap predicted speech; "
               f"{applied} applied, {len(missed)} missed, {len(audit) - applied - len(missed)} superseded.",
               "", "Missed commands:"]
    report += ([f"- {_span(row['start'], row['end'])} s: {row['text']}" for row in missed] or ["- None."])
    report += ["", "## Primary: command/take hits", "",
               "A positive truth take is a hit if a predicted take in the same section covers at least 50% of its truth duration. "
               "Matching is independent for each truth take. Rejected truth takes are checked separately and excluded from the positive-hit denominator."]
    hit_rows = []
    hits = 0
    for start, end, label in positive:
        section = label[5:].strip()
        candidates = [take for take in takes if take.section == section]
        best = max(candidates, key=lambda take: _overlap(start, end, take.start, take.end), default=None)
        coverage = _overlap(start, end, best.start, best.end) / (end - start) if best else 0.0
        hits += coverage >= 0.5
        hit_rows.append([label, _span(start, end), best.id if best and coverage > 0 else "-",
                         f"{coverage:.2%}", "HIT" if coverage >= 0.5 else "MISS"])
    report += ["", f"Positive command/take hits: **{hits}/{len(positive)}**.", "",
               _table(["Truth take", "Truth (s)", "Same-section prediction", "Truth covered", "Result"], hit_rows),
               "", "Rejected truth takes (any positive overlap counts as still produced, including unlabeled takes):", ""]
    rejection_rows = []
    for start, end, label in rejected:
        produced = [take for take in takes if _overlap(start, end, take.start, take.end) > 0]
        description = "; ".join(f"{take.id} [{take.section}], "
                                f"{_overlap(start, end, take.start, take.end) / (end - start):.2%} covered"
                                for take in produced)
        rejection_rows.append([label, _span(start, end), "YES" if produced else "NO", description or "-"])
    report.append(_table(["Truth take / intent", "Truth (s)", "Still produced?", "Overlapping predictions"], rejection_rows)
                  if rejected else "None.")
    report += ["", "## Known unsupported: label-before-take", "",
               "target=next is not supported by the current pipeline. These commands use the same preceding-take behavior as other attached commands:"]
    report += ([f"- {_span(row['start'], row['end'])} s: {row['text']} ({row['status']})."
                for row in audit if row["command"]["target"] == "next"] or ["- None."])
    report += ["", "## Extras (unscored)", "", "Every pipeline take in the unlabeled section is listed here; these do not affect positive command/take-hit counts.", ""]
    unlabeled = [take for take in takes if take.section == "unlabeled"]
    report += [f"Unlabeled extras (unscored): **{len(unlabeled)}**.", ""]
    report.append(_table(["Unlabeled take", "Start-end (s)", "Duration (s)"], [
        [take.id, _span(take.start, take.end), f"{take.end - take.start:.3f}"] for take in unlabeled])
        if unlabeled else "None.")
    boundaries = take_boundary_error(songmap, truth_takes)
    report += ["", "## Secondary: take boundary errors", "",
               "Uses take_boundary_error across all predicted sections, including unlabeled, and all truth takes, including rejected. "
               "Each truth independently chooses greatest positive overlap; errors are absolute seconds. "
               "These section-agnostic diagnostic matches are separate from the primary hit criteria.", "",
               f"Matches: **{len(truth_takes) - boundaries['misses']}**; misses: **{boundaries['misses']}**; "
               f"extras: **{boundaries['extras']}** (unused predictions).", ""]
    boundary_rows = []
    for truth_take, row in zip(truth_takes, boundaries["per_take"]):
        take = next((take for take in takes if take.id == row["predicted_id"]), None)
        boundary_rows.append([row["truth_label"], _span(truth_take[0], truth_take[1]),
                              row["predicted_id"] or "MISS", _span(take.start, take.end) if take else "-",
                              f"{row['start_error_s']:.3f}" if take else "-",
                              f"{row['end_error_s']:.3f}" if take else "-"])
    report += [_table(["Truth take", "Truth (s)", "Prediction", "Prediction (s)", "Start error (s)", "End error (s)"], boundary_rows),
               "", "## Pipeline warnings", ""]
    report += [f"- {warning}" for warning in warnings] or ["- None."]
    return "\n".join(report) + "\n"


def evaluate_memo(name: str, root: Path = ROOT) -> str:
    """Return and save a memo report after running the unchanged pipeline with oracle commands."""
    if not name or name in (".", "..") or any(char in name for char in "/\\:"):
        raise ValueError("Memo name must be a filename stem")
    source = root / "data" / f"{name}.wav"
    truth = read_audacity_labels(root / "data" / f"{name}_labels.txt")
    destination = root / "outputs" / f"{name}_eval"
    cache = root / "outputs" / f"{name}_scores.csv"
    if not source.is_file():
        raise FileNotFoundError(source)
    # Validate command grammar before any expensive scoring or output changes.
    for _, _, text in truth:
        if text.startswith("cmd:"):
            parse_command_truth(text)
    destination.mkdir(parents=True, exist_ok=True)
    empty_labels = destination / "empty_mock_labels.json"
    empty_labels.write_text("{}\n", encoding="utf-8")
    cache_used = cache.is_file()
    started = perf_counter()
    _, preview, _ = run_pipeline(source, MockLabelParser(empty_labels), destination,
                                 scores_csv=cache if cache_used else None, no_export=True)
    if not cache_used:
        with (destination / "window_scores.csv").open("rb") as scores, cache.open("xb") as saved:
            shutil.copyfileobj(scores, saved)
    duration = preview[-1].end if preview else 0.0
    _validate_truth(truth, duration)
    labels, audit = oracle_commands(truth, preview)
    oracle_path = destination / "oracle_commands.json"
    oracle_path.write_text(json.dumps(labels, indent=2) + "\n", encoding="utf-8")
    (destination / "oracle_mapping.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    songmap, regions, warnings = run_pipeline(source, MockLabelParser(oracle_path), destination, scores_csv=cache)
    if [(region.start, region.end, region.kind) for region in regions] != [
        (region.start, region.end, region.kind) for region in preview
    ]:
        raise ValueError("Oracle pass changed the predicted activity regions")
    metrics = frame_accuracy(regions, [row for row in truth if row[2] != "unclear"], KeeperConfig().hop_s,
                             exclude_intervals=[(start, end) for start, end, label in truth if label == "unclear"])
    report = render_report(name, duration, metrics, songmap, truth, audit, warnings,
                           perf_counter() - started, cache_used)
    (destination / "report.md").write_text(report, encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    """Return zero after evaluating a memo and printing its complete Markdown report."""
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("name", help="Memo stem: data/<name>.wav, data/<name>_labels.txt, outputs/<name>_scores.csv")
    args = cli.parse_args(argv)
    print(evaluate_memo(args.name), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
