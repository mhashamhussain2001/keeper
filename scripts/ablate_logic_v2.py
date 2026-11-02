"""Evaluate v1/v2 cutting with shared prompt-v2 Spark caches and cached AST scores only."""

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from eval.audacity_labels import parse_command_truth, read_audacity_labels
from eval.evaluate import frame_accuracy, take_boundary_error
from keeper.config import KeeperConfig
from keeper.labels import MockLabelParser, MuseSparkLabelParser
import keeper.pipeline as pipeline
from scripts import ablate_memos
from scripts.ablate_memos import MEMO1_TAKES, normalize_oracles, positive_hits
from scripts.eval_memo import ROOT, _table
from scripts.spark_eval import command_report


def scope_target_report(truth: list[tuple[float, float, str]], records: list[dict[str, Any]]) -> tuple[str, dict[str, int]]:
    """Return command-level scope/target accuracy, counting undetected truth commands as wrong."""
    rows, scope_ok, target_ok = [], 0, 0
    normalized, _ = normalize_oracles(truth)
    for start, end, text in normalized:
        if not text.startswith("cmd:"):
            continue
        command = parse_command_truth(text)
        candidates = [record for record in records if min(end, record["end"]) > max(start, record["start"])]
        matched = max(candidates, key=lambda record: min(end, record["end"]) - max(start, record["start"]), default=None)
        scope = "last_attempt" if any(phrase in (command.note or "").lower() for phrase in ("last one", "last take", "last version")) else "whole"
        target = command.target or "previous"
        parsed = matched["parsed"] if matched else {}
        scope_ok += parsed.get("scope", "whole") == scope if matched else False
        target_ok += parsed.get("target") == target
        rows.append([f"{start:.3f} {text}", scope, parsed.get("scope", "MISSED"), target, parsed.get("target", "MISSED")])
    counts = {"scope_correct": scope_ok, "target_correct": target_ok, "total": len(rows)}
    return _table(["Truth command", "Truth scope", "Spark scope", "Truth target", "Spark target"], rows) + \
        f"\n\nScope accuracy: {scope_ok}/{len(rows)}; target accuracy: {target_ok}/{len(rows)} (missed commands count wrong).", counts


def evaluate(name: str, root: Path = ROOT, output_name: str = "logic_v2") -> tuple[str, dict[str, Any]]:
    """Return and save four cached-AST Spark settings, command diagnostics, and all truth-take errors."""
    fixture = root / "tests/fixtures/test_memo"
    audio = root / "data" / ("test.wav" if name == "memo1" else "memo2.wav")
    scores = fixture / "window_scores.csv" if name == "memo1" else root / "outputs/memo2_scores.csv"
    truth = read_audacity_labels(fixture / "truth_labels.txt" if name == "memo1" else root / "data/memo2_labels.txt")
    takes = MEMO1_TAKES if name == "memo1" else [row for row in truth if row[2].startswith("take:")]
    if not scores.is_file() or not audio.is_file():
        raise FileNotFoundError("Real audio and cached AST scores required; refusing to rescore")
    checksum = hashlib.sha256(scores.read_bytes()).hexdigest()
    duration = json.loads((fixture / "meta.json").read_text())["duration_s"] if name == "memo1" else None
    output = root / "outputs" / name / output_name
    output.mkdir(parents=True, exist_ok=True)
    empty = output / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    parser = MuseSparkLabelParser(output.parent / "spark_cache.json", KeeperConfig().max_spark_calls)
    results, report = {}, [f"# {name}: logic v2", "", "Same prompt v2 and cached AST for every setting; edge trim on, thresholds unchanged. "
        "V1 disables target-next and last-attempt cutting; v2 enables both. Protection is evaluated separately. "
        "Boundary errors use the existing section-agnostic overlap matcher; positive hits require matching section and 50% truth overlap. "
        "Unlabeled output remains unscored. Memo1 is cropped to its fixture duration.", ""]
    with patch.object(pipeline, "ASTScorer", side_effect=AssertionError("Never rescore during ablation")):
        for protect in (False, True):
            for enabled in (False, True):
                key = f"v{2 if enabled else 1}, protect {'on' if protect else 'off'}"
                cfg = KeeperConfig(protect_speech_runs=protect, edge_trim=True,
                                   support_target_next=enabled, support_last_attempt=enabled)
                destination = output / key.replace(", ", "_").replace(" ", "_")
                _, preview, _ = pipeline.run_pipeline(audio, MockLabelParser(empty), destination / "preview", cfg,
                                                     scores_csv=scores, duration_s=duration, no_export=True)
                first = len(parser.records)
                songmap, regions, warnings = pipeline.run_pipeline(audio, parser, destination, cfg,
                                                                  scores_csv=scores, duration_s=duration)
                records = sorted(parser.records[first:], key=lambda row: row["start"])
                metrics = frame_accuracy(regions, [row for row in truth if row[2] != "unclear"], cfg.hop_s,
                                         exclude_intervals=[(s, e) for s, e, kind in truth if kind == "unclear"])
                hits, total = positive_hits(songmap, takes)
                detected = sum(any(region.kind == "speech" and min(end, region.end) > max(start, region.start)
                                   for region in preview) for start, end, label in truth if label.startswith("cmd:"))
                comparison, accuracy = command_report(truth, records, name)
                scopes, scope_counts = scope_target_report(truth, records)
                targeted = []
                for start, end, label in takes:
                    section = label[5:].split()[0]
                    overlaps = [take for take in songmap.sections.get(section, [])
                                if min(end, take.end) - max(start, take.start) >= (end - start) / 2]
                    best = max(overlaps, key=lambda take: min(end, take.end) - max(start, take.start), default=None)
                    targeted.append({"truth": label, "start": start, "end": end, "hit": best is not None,
                                     "predicted_start": best.start if best else None, "predicted_end": best.end if best else None,
                                     "start_error_s": abs(best.start - start) if best else None,
                                     "end_error_s": abs(best.end - end) if best else None})
                results[key] = {"frame_metrics": metrics, "commands_detected": detected,
                                "commands_total": sum(label.startswith("cmd:") for _, _, label in truth),
                                "positive_hits": hits, "positive_total": total,
                                "boundary_errors": take_boundary_error(songmap, takes), "warnings": warnings,
                                "intent_accuracy": accuracy, "scope_target": scope_counts, "section_hits": targeted,
                                "records": records, "takes": songmap.to_dict()}
                report += [f"## {key}", "", comparison, "", scopes, "", "Warnings: " + ("; ".join(warnings) or "none"), ""]
    original = ablate_memos.SETTINGS
    try:
        ablate_memos.SETTINGS = [(key, False, True) for key in results]
        table = ablate_memos.render_table(name, results, takes)
    finally:
        ablate_memos.SETTINGS = original
    report += [table, "", "Intent accuracy: " + ", ".join(f"{key}={row['intent_accuracy']:.6f}" for key, row in results.items()), ""]
    section_rows = [[key, row["truth"], f"{row['start']:.2f}-{row['end']:.2f}", "HIT" if row["hit"] else "MISS",
                     f"{row['start_error_s']:.3f}/{row['end_error_s']:.3f}" if row["hit"] else "—"]
                    for key, entry in results.items() for row in entry["section_hits"]]
    report += [_table(["Setting", "Truth section", "Truth interval", "Section hit", "S/E error (s)"], section_rows), ""]
    attempts = [attempt for record in parser.records if not record["cached"] for attempt in record["attempts"]]
    accounting = {"new_calls": parser.calls_made, "new_tokens": sum(row["total_tokens"] for row in attempts),
                  "cached_regions": sum(row["cached"] for row in parser.records)}
    report += ["Accounting: " + json.dumps(accounting)]
    if hashlib.sha256(scores.read_bytes()).hexdigest() != checksum:
        raise AssertionError("Input AST cache changed")
    saved = {"settings": results, "accounting": accounting, "score_sha256": checksum, "table": table}
    text = "\n".join(report) + "\n"
    (output / "report.md").write_text(text, encoding="utf-8")
    (output / "results.json").write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
    return text, saved


def main() -> int:
    """Return zero after printing both memo reports and saving all measured settings."""
    for memo in ("memo1", "memo2"):
        text, _ = evaluate(memo)
        print(text, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
