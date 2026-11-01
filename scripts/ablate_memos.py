"""Measure protected speech and edge trimming with cached scores and oracle commands only."""

import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import patch

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import keeper.pipeline as pipeline
from eval.audacity_labels import TruthRow, read_audacity_labels
from eval.evaluate import frame_accuracy, take_boundary_error
from keeper.config import FRAME_KINDS, KeeperConfig
from keeper.labels import MockLabelParser
from keeper.types import SongMap
from scripts.eval_memo import oracle_commands


ROOT = Path(__file__).resolve().parents[1]
SETTINGS = [("none", False, False), ("protect only", True, False),
            ("trim only", False, True), ("both", True, True)]
# Existing test_real_memo.py boundary expectations; the fixture has no take: rows.
MEMO1_TAKES: list[TruthRow] = [(0.0, 8.0, "take:chorus A"), (10.52, 20.64, "take:chorus B"),
                             (23.05, 26.85, "take:chorus C")]


def normalize_oracles(truth: list[TruthRow]) -> tuple[list[TruthRow], dict[TruthRow, str]]:
    """Return command truth normalized from legacy section/variant labels while retaining variants."""
    normalized = []
    variants = {}
    for start, end, text in truth:
        variant = None
        if text.startswith("cmd:") and text[4:].split()[0] not in ("file", "keep", "reject", "none"):
            section, variant = text[4:].split(maxsplit=1)
            text = f"cmd:file section={section}"
        row = (start, end, text)
        normalized.append(row)
        if variant is not None:
            variants[row] = variant
    return normalized, variants


def positive_hits(songmap: SongMap, truth: list[TruthRow]) -> tuple[int, int]:
    """Return same-section half-truth-overlap hits and the number of positive truth takes."""
    hits, total = 0, 0
    for start, end, label in truth:
        section = label[5:].split()[0]
        if section in ("reject", "rejected"):
            continue
        total += 1
        if any(max(0.0, min(end, take.end) - max(start, take.start)) >= (end - start) * 0.5
               for take in songmap.sections.get(section, [])):
            hits += 1
    return hits, total


def measure_case(
    source: Path, cache: Path, truth: list[TruthRow], take_truth: list[TruthRow],
    destination: Path, cfg: KeeperConfig, rms_json: Path | None = None,
) -> dict[str, Any]:
    """Return one cached-score pipeline measurement after rebuilding oracle speech attachments."""
    if not cache.is_file():
        raise FileNotFoundError(f"Required score cache missing; refusing to rescore: {cache}")
    destination.mkdir(parents=True, exist_ok=True)
    empty = destination / "empty_mock_labels.json"
    empty.write_text("{}\n", encoding="utf-8")
    with patch.object(pipeline, "ASTScorer", side_effect=AssertionError("Ablation must never rescore")):
        _, preview, _ = pipeline.run_pipeline(source, MockLabelParser(empty), destination, cfg=cfg,
                                              scores_csv=cache, rms_json=rms_json, no_export=True)
        normalized, variants = normalize_oracles(truth)
        commands, audit = oracle_commands(normalized, preview)
        for row in audit:
            original = (row["start"], row["end"], row["text"])
            if row["status"] == "applied" and original in variants:
                commands[str(row["speech_start"])]["variant"] = variants[original]
        command_path = destination / "oracle_commands.json"
        command_path.write_text(json.dumps(commands, indent=2) + "\n", encoding="utf-8")
        (destination / "oracle_mapping.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
        songmap, regions, warnings = pipeline.run_pipeline(
            source, MockLabelParser(command_path), destination, cfg=cfg,
            scores_csv=cache, rms_json=rms_json, no_export=True,
        )
    metrics = frame_accuracy(regions, [row for row in truth if row[2] != "unclear"], cfg.hop_s,
                             exclude_intervals=[(start, end) for start, end, kind in truth if kind == "unclear"])
    hits, total = positive_hits(songmap, take_truth)
    return {"config": asdict(cfg), "frame_metrics": metrics,
            "commands_detected": sum(row["speech_start"] is not None for row in audit),
            "commands_total": len(audit), "positive_hits": hits, "positive_total": total,
            "boundary_errors": take_boundary_error(songmap, take_truth), "warnings": warnings,
            "takes": [take.to_dict() for takes in songmap.sections.values() for take in takes]}


def not_worse(candidate: dict[str, Any], baseline: dict[str, Any]) -> bool:
    """Return whether all requested metrics and per-take absolute errors preserve or improve baseline."""
    if candidate["frame_metrics"]["overall_accuracy"] < baseline["frame_metrics"]["overall_accuracy"]:
        return False
    if any(candidate["frame_metrics"]["per_class"][kind]["recall"] <
           baseline["frame_metrics"]["per_class"][kind]["recall"] for kind in FRAME_KINDS):
        return False
    if candidate["commands_detected"] < baseline["commands_detected"] or candidate["positive_hits"] < baseline["positive_hits"]:
        return False
    for current, old in zip(candidate["boundary_errors"]["per_take"], baseline["boundary_errors"]["per_take"]):
        for key in ("start_error_s", "end_error_s"):
            if old[key] is not None and (current[key] is None or current[key] > old[key] + 1e-9):
                return False
    return True


def render_table(name: str, results: dict[str, dict[str, Any]], truth: list[TruthRow]) -> str:
    """Return one ablation table containing frame, command, hit, and per-truth boundary metrics."""
    headers = ["Setting", "Frame accuracy", "Recall music", "Recall speech", "Recall silence",
               "Commands detected", "Positive take hits"] + [
                   f"{label[5:]} {start:.2f}-{end:.2f} S/E error" for start, end, label in truth]
    rows: list[list[str]] = []
    for setting, _, _ in SETTINGS:
        row = results[setting]
        metrics = row["frame_metrics"]
        rows.append([setting, f"{metrics['overall_accuracy']:.6f}",
                     *[f"{metrics['per_class'][kind]['recall']:.6f}" for kind in FRAME_KINDS],
                     f"{row['commands_detected']}/{row['commands_total']}",
                     f"{row['positive_hits']}/{row['positive_total']}",
                     *[f"{error['start_error_s']:.3f}/{error['end_error_s']:.3f}"
                       if error["predicted_id"] is not None else "MISS"
                       for error in row["boundary_errors"]["per_take"]]])
    lines = [f"## {name}", "", "| " + " | ".join(headers) + " |",
             "| " + " | ".join(["---"] * len(headers)) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def run_ablation(root: Path = ROOT) -> str:
    """Return and save both four-setting memo tables using existing score caches exclusively."""
    fixture = root / "tests" / "fixtures" / "test_memo"
    inputs = [("memo2", root / "data" / "memo2.wav", root / "outputs" / "memo2_scores.csv",
               root / "data" / "memo2_labels.txt", None),
              ("memo1", fixture, fixture / "window_scores.csv", fixture / "truth_labels.txt", fixture / "rms_db.json")]
    for _, _, cache, _, _ in inputs:
        if not cache.is_file():
            raise FileNotFoundError(f"Required score cache missing; refusing to rescore: {cache}")
    report = ["# Protected speech / edge trim ablation", "",
              "Cached scores only; AST construction is explicitly blocked. Fixed thresholds: "
              "protect_speech_run_s=1.0 and trim_singing_min=0.0018. No threshold tuning.",
              "UNCLEAR frames are excluded. Command detection counts truth commands overlapping predicted speech; "
              "oracle attachments are regenerated per setting. Positive hits require matching section and >=50% truth overlap; rejected truth is excluded from that denominator.",
              "S/E cells are absolute start/end errors in seconds from the existing section-agnostic maximum-overlap matcher, including unlabeled/rejected output. MISS denotes no overlapping prediction.",
              "Memo1 take truth uses the existing test_real_memo.py expected spans; legacy cmd:chorus A/B/C annotations retain their oracle variants. "
              "target=next and rejection semantics are unchanged.", ""]
    for name, source, cache, truth_path, rms in inputs:
        checksum = hashlib.sha256(cache.read_bytes()).hexdigest()
        truth = read_audacity_labels(truth_path)
        takes = MEMO1_TAKES if name == "memo1" else [row for row in truth if row[2].startswith("take:")]
        output = root / "outputs" / f"{name}_eval"
        results = {}
        for setting, protect, trim in SETTINGS:
            cfg = KeeperConfig(protect_speech_runs=protect, edge_trim=trim)
            results[setting] = measure_case(source, cache, truth, takes,
                                            output / "ablation" / setting.replace(" ", "_"), cfg, rms)
        if hashlib.sha256(cache.read_bytes()).hexdigest() != checksum:
            raise AssertionError("Ablation modified its input score cache")
        (output / "ablation.json").write_text(json.dumps({"score_sha256": checksum, "truth_takes": takes,
                                                          "settings": results}, indent=2) + "\n", encoding="utf-8")
        rendered = render_table(name, results, takes)
        report += [rendered, ""]
        if name == "memo1":
            report += ["Not worse than none (all listed metrics and each boundary error): "
                       + ", ".join(f"{setting}={not_worse(results[setting], results['none'])}"
                                   for setting in ("protect only", "trim only", "both")) + ".", ""]
        warnings = [f"- {setting}: {warning}" for setting, result in results.items() for warning in result["warnings"]]
        report += ["Warnings:", "", *(warnings or ["- None."]), ""]
        (output / "ablation.md").write_text(rendered + "\n", encoding="utf-8")
    text = "\n".join(report).rstrip() + "\n"
    (root / "outputs" / "ablation_report.md").write_text(text, encoding="utf-8")
    return text


def main() -> int:
    """Return zero after printing the memo2 and memo1 cached ablation report."""
    print(run_ablation(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
