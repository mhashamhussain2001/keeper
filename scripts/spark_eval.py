"""Cached-AST Spark/oracle evaluation with protection off/on and auditable API accounting."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import keeper.pipeline as pipeline
from eval.audacity_labels import parse_command_truth, read_audacity_labels
from eval.evaluate import frame_accuracy, take_boundary_error
from keeper.config import KeeperConfig
from keeper.labels import MockLabelParser, MuseSparkLabelParser
from keeper.types import SongMap
from scripts.ablate_memos import MEMO1_TAKES, normalize_oracles, positive_hits, render_table
from scripts.eval_memo import ROOT, _table, oracle_commands


def detail_report(metrics: dict[str, Any], songmap: SongMap, takes: list[tuple[float, float, str]]) -> str:
    """Return precision, confusion, unclear, boundary-count, rejected, and unscored-extra diagnostics."""
    kinds = list(metrics["per_class"])
    matrix = metrics["confusion_matrix"]
    boundary = take_boundary_error(songmap, takes)
    predicted = [take for section in songmap.sections.values() for take in section]
    rejected = [(start, end, label) for start, end, label in takes if label[5:].split()[0] in ("reject", "rejected")]
    lines = ["Precision: " + ", ".join(f"{kind}={metrics['per_class'][kind]['precision']:.6f}" for kind in kinds),
             "", "Confusion matrix (truth rows, predicted columns):", "",
             _table(["Truth / prediction", *kinds], [[kind, *[matrix[kind][guess] for guess in kinds]] for kind in kinds]), "",
             f"UNCLEAR: {sum(metrics['excluded_predictions'].values())} frames; predictions: " +
             ", ".join(f"{kind}={metrics['excluded_predictions'][kind]}" for kind in kinds) + ".",
             f"Secondary boundaries: {len(takes) - boundary['misses']} matches; {boundary['misses']} misses; {boundary['extras']} extras.",
             "Rejected truth takes still produced: " + ("; ".join(
                 f"{label} {start:.3f}-{end:.3f}: " + (", ".join(f"{take.id} [{take.section}]" for take in predicted
                 if min(end, take.end) > max(start, take.start)) or "NO") for start, end, label in rejected) or "none"),
             "Extras (unscored, unlabeled): " + ("; ".join(f"{take.id} {take.start:.3f}-{take.end:.3f}"
                 for take in predicted if take.section == "unlabeled") or "none")]
    return "\n".join(lines)


def command_report(truth: list[tuple[float, float, str]], records: list[dict[str, Any]], memo: str) -> tuple[str, float]:
    """Return truth-command and unmatched-speech comparisons plus intent accuracy."""
    rows = []
    used: set[int] = set()
    correct, total, section_ok, section_total = 0, 0, 0, 0
    normalized, _ = normalize_oracles(truth)
    for start, end, text in normalized:
        if not text.startswith("cmd:"):
            continue
        command = parse_command_truth(text)
        best = max(range(len(records)), key=lambda i: max(0.0, min(end, records[i]["end"]) - max(start, records[i]["start"])), default=None)
        matched = records[best] if best is not None and min(end, records[best]["end"]) > max(start, records[best]["start"]) else None
        if matched is not None:
            used.add(best)
        data = matched["parsed"] if matched else {}
        intent_match = data.get("intent") == command.intent
        section_match = data.get("section") == command.section if command.intent == "file" else None
        correct += intent_match
        total += 1
        if command.intent == "file":
            section_total += 1
            section_ok += bool(section_match)
        rows.append([text, f"{matched['start']:.3f}-{matched['end']:.3f}" if matched else "MISSED",
                     data.get("heard", ""), data.get("intent", "missing"), data.get("section"), data.get("note"),
                     data.get("confidence", 0), intent_match, section_match if section_match is not None else "N/A"])
    for index, record in enumerate(records):
        if index in used:
            continue
        expected = "noise" if memo == "memo1" and min(record["end"], 21.0) > max(record["start"], 19.0) else "none"
        data = record["parsed"]
        match = data["intent"] == expected
        total += 1
        correct += match
        rows.append([f"unannotated speech: {expected}", f"{record['start']:.3f}-{record['end']:.3f}",
                     data["heard"], data["intent"], data["section"], data["note"], data["confidence"], match, "N/A"])
    attempts = [attempt for record in records if not record["cached"] for attempt in record["attempts"]]
    refusals = sum("refusal" in (attempt["failure_reason"] or "").lower() for attempt in attempts)
    tokens = sum(attempt["total_tokens"] for attempt in attempts)
    latency = sum(attempt["latency_s"] for attempt in attempts) / len(attempts) if attempts else 0.0
    retries = sum(record["retries"] for record in records if not record["cached"])
    text = _table(["Truth", "Speech region", "Spark heard", "Intent", "Section", "Note", "Confidence", "Intent match", "Section match"], rows)
    return text + f"\n\nIntent accuracy: {correct}/{total} = {correct / total if total else 0:.6f}. " + \
        f"Section accuracy (truth file intents): {section_ok}/{section_total} = {section_ok / section_total if section_total else 0:.6f}.\n" + \
        f"Calls made: {len(attempts)}; cached regions: {sum(record['cached'] for record in records)}; retries: {retries}; refusals: {refusals}; " + \
        f"total tokens this run: {tokens}; mean call latency: {latency:.3f} s.\n", correct / total if total else 0.0


def evaluate_spark(name: str, root: Path = ROOT) -> str:
    """Return both protection-setting reports using shared per-memo Spark cache and no AST inference."""
    if name not in ("memo1", "memo2"):
        raise ValueError("Spark evaluation currently supports memo1 and memo2")
    fixture = root / "tests" / "fixtures" / "test_memo"
    source = root / "data" / ("test.wav" if name == "memo1" else "memo2.wav")
    cache = fixture / "window_scores.csv" if name == "memo1" else root / "outputs" / "memo2_scores.csv"
    if not cache.is_file() or not source.is_file():
        raise FileNotFoundError("Audio and cached AST scores are required; refusing AST scoring")
    truth = read_audacity_labels(fixture / "truth_labels.txt" if name == "memo1" else root / "data" / "memo2_labels.txt")
    takes = MEMO1_TAKES if name == "memo1" else [row for row in truth if row[2].startswith("take:")]
    output = root / "outputs" / name
    output.mkdir(parents=True, exist_ok=True)
    parser = MuseSparkLabelParser(output / "spark_cache.json", KeeperConfig().max_spark_calls)
    duration = float(json.loads((fixture / "meta.json").read_text())["duration_s"]) if name == "memo1" else None
    results = {}
    report = [f"# {name} Spark vs oracle", "", "edge_trim=True; fixed thresholds. Oracle and Spark both use cached AST scores. "
              "Memo1 uses its declared fixture duration with real audio cropped to that duration. Direction and scope use the current KeeperConfig cut flags. "
              "No-command speech is truth none; unmatched memo1 speech overlapping 19-21 s is truth noise. "
              "A region overlapping command truth keeps command priority even if it also includes chair noise.", ""]
    with patch.object(pipeline, "ASTScorer", side_effect=AssertionError("Never rescore AST")):
        for protect in (False, True):
            cfg = KeeperConfig(protect_speech_runs=protect, edge_trim=True)
            folder = output / ("protect_on" if protect else "protect_off")
            folder.mkdir(exist_ok=True)
            empty = folder / "empty.json"
            empty.write_text("{}", encoding="utf-8")
            _, preview, _ = pipeline.run_pipeline(source, MockLabelParser(empty), folder / "preview", cfg,
                                                 scores_csv=cache, no_export=True, duration_s=duration)
            normalized, _ = normalize_oracles(truth)
            commands, _ = oracle_commands(normalized, preview)
            if name == "memo1":
                fixture_commands = json.loads((fixture / "mock_commands.json").read_text())
                # Chronological command ordering matches the fixture's A/B/C oracle variants.
                for index, key in enumerate(commands):
                    commands[key]["variant"] = list(fixture_commands.values())[index]["variant"]
            oracle_file = folder / "oracle.json"
            oracle_file.write_text(json.dumps(commands), encoding="utf-8")
            for mode in ("oracle", "spark"):
                first_record = len(parser.records)
                active = MockLabelParser(oracle_file) if mode == "oracle" else parser
                songmap, regions, warnings = pipeline.run_pipeline(source, active, folder / mode, cfg,
                                                                   scores_csv=cache, no_export=False, duration_s=duration)
                records = parser.records[first_record:]
                metrics = frame_accuracy(regions, [row for row in truth if row[2] != "unclear"], cfg.hop_s,
                                         exclude_intervals=[(s, e) for s, e, kind in truth if kind == "unclear"])
                hits, total = positive_hits(songmap, takes)
                entry = {"frame_metrics": metrics, "commands_detected": sum(any(r.kind == "speech" and min(e, r.end) > max(s, r.start) for r in preview)
                          for s, e, kind in truth if kind.startswith("cmd:")), "commands_total": sum(row[2].startswith("cmd:") for row in truth),
                         "positive_hits": hits, "positive_total": total, "boundary_errors": take_boundary_error(songmap, takes), "warnings": warnings,
                         "takes": [take.to_dict() for section in songmap.sections.values() for take in section]}
                key = f"{mode}, protect {'on' if protect else 'off'}"
                results[key] = entry
                report += [f"## {key}", "", f"Frame accuracy: {metrics['overall_accuracy']:.6f}; recalls: " +
                           ", ".join(f"{kind}={row['recall']:.6f}" for kind, row in metrics["per_class"].items()), ""]
                report += [detail_report(metrics, songmap, takes), ""]
                if mode == "spark":
                    comparison, accuracy = command_report(truth, records, name)
                    entry["intent_accuracy"] = accuracy
                    entry["records"] = records
                    report += ["Spark vs truth commands:", "", comparison, ""]
                report += ["Warnings: " + ("; ".join(warnings) if warnings else "none"), ""]
    # The shared table formatter expects four names; map its four slots to these four actual runs.
    from scripts import ablate_memos
    original = ablate_memos.SETTINGS
    try:
        ablate_memos.SETTINGS = [(key, False, False) for key in results]
        report += [render_table(name, results, takes), "", "Spark intent accuracies: " +
                   ", ".join(f"{key}={row['intent_accuracy']:.6f}" for key, row in results.items() if "intent_accuracy" in row)]
    finally:
        ablate_memos.SETTINGS = original
    attempts = [a for r in parser.records if not r["cached"] for a in r["attempts"]]
    report += ["", f"TOTAL new API calls: {parser.calls_made}; total new tokens: {sum(a['total_tokens'] for a in attempts)}; "
               f"cached regions: {sum(r['cached'] for r in parser.records)}."]
    (output / "spark_results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    text = "\n".join(report) + "\n"
    (output / "spark_report.md").write_text(text, encoding="utf-8")
    return text
