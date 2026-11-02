"""Compare two learned frame classifiers with current hand rules using whole-memo holdouts only."""

import argparse
import hashlib
import json
import sys
import warnings
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
import sklearn
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier, export_text

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.audacity_labels import TruthRow, parse_command_truth, read_audacity_labels
from eval.evaluate import frame_accuracy, take_boundary_error
from keeper.audio_io import load_audio, rms_db_per_frame
from keeper.config import FRAME_KINDS, KeeperConfig
from keeper.cut import cut_takes
from keeper.frames import frame_labels_from_scores, frame_scores_from_windows
from keeper.regions import absorb_short, merge_frames
from keeper.songmap import build_songmap
from keeper.trim import trim_takes
from keeper.types import CommandLabel, FrameLabel
from keeper.unlabeled import find_unlabeled
from scripts.ablate_memos import MEMO1_TAKES, normalize_oracles, positive_hits
from scripts.eval_memo import _table, _validate_truth, oracle_commands


ROOT = Path(__file__).resolve().parents[1]
FEATURES = ["speech", "singing_group", "instrument_group", "Singing", "Humming", "Music",
            "rms_above_floor", "rms_above_median", "speech_prev", "speech_next",
            "singing_group_prev", "singing_group_next"]


@dataclass
class Memo:
    """One complete cached memo, including unscored frames needed for contiguous downstream predictions."""

    name: str
    source: str
    scores: pd.DataFrame
    frames: pd.DataFrame
    rms: np.ndarray
    duration: float
    truth: list[TruthRow]
    takes: list[TruthRow]
    score_path: Path
    score_sha256: str


def make_frame_table(scores: pd.DataFrame, rms: np.ndarray, duration: float, truth: list[TruthRow]) -> pd.DataFrame:
    """Return twelve label-free frame features with midpoint targets and unclear exclusion flags."""
    cfg = KeeperConfig()
    missing = {"start", "end", "speech", "singing", "instrument", "Singing", "Humming", "Music"} - set(scores)
    if missing:
        raise ValueError("Missing cached score features: " + ", ".join(sorted(missing)))
    if rms.ndim != 1 or len(rms) != ceil(duration / cfg.hop_s) or not np.isfinite(rms).all() or not len(rms):
        raise ValueError("Finite RMS frames must exactly cover the nonempty memo")
    _validate_truth(truth, duration)
    frame = frame_scores_from_windows(scores, duration, cfg.hop_s).rename(columns={"singing": "singing_group", "instrument": "instrument_group"})
    frame = frame.drop(columns="music")
    for raw in ("Singing", "Humming", "Music"):
        projected = scores[["start", "end"]].copy()
        projected["singing"], projected["speech"], projected["instrument"] = scores[raw], 0.0, 0.0
        frame[raw] = frame_scores_from_windows(projected, duration, cfg.hop_s)["singing"]
    frame["rms_above_floor"] = rms - np.percentile(rms, 10)
    frame["rms_above_median"] = rms - np.median(rms)
    for name in ("speech", "singing_group"):
        frame[name + "_prev"] = frame[name].shift(1, fill_value=frame[name].iloc[0])
        frame[name + "_next"] = frame[name].shift(-1, fill_value=frame[name].iloc[-1])
    activity = [row for row in truth if not row[2].startswith(("cmd:", "take:"))]
    frame["target"] = [next((kind for start, end, kind in activity if start <= midpoint < end), "silence")
                       for midpoint in (frame["start"] + frame["end"]) / 2]
    frame["scored"] = frame["target"].isin(FRAME_KINDS)
    if not np.isfinite(frame[FEATURES].to_numpy()).all():
        raise ValueError("Frame features must all be finite")
    return frame


def load_memos(root: Path) -> list[Memo]:
    """Return memo1's numeric fixture and memo2's cached scores plus decoded RMS, never scoring AST."""
    fixture = root / "tests" / "fixtures" / "test_memo"
    caches = [fixture / "window_scores.csv", root / "outputs" / "memo2_scores.csv"]
    for path in caches:
        if not path.is_file():
            raise FileNotFoundError(f"Required cache missing; refusing to rescore: {path}")
    memos = []
    for index, name in enumerate(("memo1", "memo2")):
        score_path = caches[index]
        digest = hashlib.sha256(score_path.read_bytes()).hexdigest()
        scores = pd.read_csv(score_path)
        if name == "memo1":
            duration = float(json.loads((fixture / "meta.json").read_text())["duration_s"])
            rms = np.asarray(json.loads((fixture / "rms_db.json").read_text()), dtype=float)[:ceil(duration / KeeperConfig().hop_s)]
            truth_path, source = fixture / "truth_labels.txt", fixture
        else:
            source = root / "data" / "memo2.wav"
            audio = load_audio(source, 16000)
            duration = len(audio) / 16000
            rms = rms_db_per_frame(audio, 16000, KeeperConfig().hop_s)
            truth_path = root / "data" / "memo2_labels.txt"
        truth = read_audacity_labels(truth_path)
        frames = make_frame_table(scores, rms, duration, truth)
        takes = MEMO1_TAKES if name == "memo1" else [row for row in truth if row[2].startswith("take:")]
        memos.append(Memo(name, str(source), scores, frames, rms, duration, truth, takes, score_path, digest))
    return memos


def evaluate_labels(memo: Memo, labels: list[str], destination: Path) -> dict[str, Any]:
    """Return frame and downstream oracle metrics using unchanged region, cutting, and trim defaults."""
    cfg = KeeperConfig()
    if len(labels) != len(memo.frames):
        raise ValueError("Predictions must include every frame, including unclear intervals")
    regions = merge_frames(cast(list[FrameLabel], labels), cfg.hop_s)
    regions[-1].end = memo.duration
    scored_truth = [row for row in memo.truth if row[2] != "unclear"]
    excluded = [(start, end) for start, end, kind in memo.truth if kind == "unclear"]
    raw_metrics = frame_accuracy(regions, scored_truth, cfg.hop_s, exclude_intervals=excluded)
    regions = absorb_short(regions, cfg)
    metrics = frame_accuracy(regions, scored_truth, cfg.hop_s, exclude_intervals=excluded)
    normalized, variants = normalize_oracles(memo.truth)
    _, audit = oracle_commands(normalized, regions)
    commands = {}
    for row in audit:
        if row["status"] != "applied":
            continue
        command = parse_command_truth(row["text"])
        section = (command.section or "unsorted") if command.intent == "file" else "rejected" if command.intent == "reject" else "unsorted"
        variant = variants.get((row["start"], row["end"], row["text"]))
        commands[row["speech_start"]] = CommandLabel(command.intent in ("file", "keep", "reject"), section,
            variant, command.note, 1.0, heard=row["text"], target=command.target, intent=command.intent)
    takes, warnings_out = cut_takes(regions, commands, memo.source, cfg)
    takes.extend(find_unlabeled(regions, [take for take, _ in takes], memo.source, cfg))
    takes, trim_warnings = trim_takes(takes, frame_scores_from_windows(memo.scores, memo.duration, cfg.hop_s), cfg)
    warnings_out.extend(trim_warnings)
    warnings_out.extend(f"label-before-take not supported: {command.heard}" for command in commands.values() if command.target == "next")
    songmap = build_songmap(takes, memo.source)
    songmap.parser = "oracle"
    hits, total = positive_hits(songmap, memo.takes)
    destination.mkdir(parents=True, exist_ok=True)
    songmap.to_json(destination / "songmap.json")
    (destination / "regions.json").write_text(json.dumps([region.to_dict() for region in regions], indent=2) + "\n")
    final = [next(region.kind for region in regions if region.start <= time < region.end)
             for time in (memo.frames["start"] + memo.frames["end"]) / 2]
    return {"raw_frame_metrics": raw_metrics, "frame_metrics": metrics,
            "commands_detected": sum(row["speech_start"] is not None for row in audit), "commands_total": len(audit),
            "positive_hits": hits, "positive_total": total, "boundary_errors": take_boundary_error(songmap, memo.takes),
            "oracle_audit": audit, "warnings": warnings_out, "predicted_frames": labels, "final_frames": final}


def fit_models(train: pd.DataFrame) -> tuple[Any, DecisionTreeClassifier]:
    """Return standardized multinomial logistic regression and a fixed depth-three balanced tree."""
    scored = train.loc[train["scored"]]
    if set(scored["target"]) != set(FRAME_KINDS):
        raise ValueError("Each training memo must contain all three target classes")
    logistic = make_pipeline(StandardScaler(), LogisticRegression(solver="lbfgs", C=1.0, max_iter=5000,
                                                                 class_weight="balanced", random_state=0))
    tree = DecisionTreeClassifier(max_depth=3, class_weight="balanced", random_state=0)
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        logistic.fit(scored[FEATURES], scored["target"])
    tree.fit(scored[FEATURES], scored["target"])
    return logistic, tree


def explain_models(logistic: Any, tree: DecisionTreeClassifier) -> tuple[str, dict[str, Any]]:
    """Return tree rules, per-class standardized coefficients, and training-only scaling parameters."""
    classifier = logistic.named_steps["logisticregression"]
    scaler = logistic.named_steps["standardscaler"]
    coefficients = {kind: {"intercept": float(classifier.intercept_[index]),
                           **dict(zip(FEATURES, map(float, classifier.coef_[index])))}
                    for index, kind in enumerate(classifier.classes_)}
    rules = export_text(tree, feature_names=FEATURES, class_names=list(tree.classes_), decimals=6)
    rows = [[feature, *[f"{coefficients[kind][feature]:.6f}" for kind in FRAME_KINDS]] for feature in [*FEATURES, "intercept"]]
    text = "Tree rules (raw feature units):\n\n```text\n" + rules + "```\n\n" + \
           "Logistic coefficients (standardized feature units; intercept is dimensionless):\n\n" + _table(["Feature", *FRAME_KINDS], rows)
    return text, {"tree_rules": rules, "coefficients": coefficients,
                  "scaler_mean": dict(zip(FEATURES, map(float, scaler.mean_))),
                  "scaler_scale": dict(zip(FEATURES, map(float, scaler.scale_))),
                  "iterations": classifier.n_iter_.tolist()}


def render_fold(train: Memo, test: Memo, cases: dict[str, dict[str, Any]], explanation: str) -> str:
    """Return one held-out memo's frame metrics, confusion matrices, downstream comparison, and model explanations."""
    lines = [f"## Train {train.name} -> test {test.name}", "", "Frame metrics after common region merging/short-region absorption:", "",
             _table(["Method", "Accuracy", "Recall music", "Recall speech", "Recall silence", "Before absorption accuracy"], [
                 [name, f"{row['frame_metrics']['overall_accuracy']:.6f}",
                  *[f"{row['frame_metrics']['per_class'][kind]['recall']:.6f}" for kind in FRAME_KINDS],
                  f"{row['raw_frame_metrics']['overall_accuracy']:.6f}"] for name, row in cases.items()]), "",
             "Confusion matrices: truth rows, prediction columns; class order music, speech, silence.", ""]
    for name, row in cases.items():
        matrix = row["frame_metrics"]["confusion_matrix"]
        lines += [f"{name}:", "", _table(["Truth / predicted", *FRAME_KINDS], [[kind, *[matrix[kind][guess] for guess in FRAME_KINDS]] for kind in FRAME_KINDS]), ""]
    lines += ["Downstream oracle comparison; S/E errors are absolute seconds:", "",
              _table(["Method", "Commands detected", "Positive take hits", "Boundary matches/misses/extras",
                      *[f"{label[5:]} {start:.2f}-{end:.2f} S/E" for start, end, label in test.takes]], [
                  [name, f"{row['commands_detected']}/{row['commands_total']}", f"{row['positive_hits']}/{row['positive_total']}",
                   f"{len(test.takes) - row['boundary_errors']['misses']}/{row['boundary_errors']['misses']}/{row['boundary_errors']['extras']}",
                   *[f"{error['start_error_s']:.3f}/{error['end_error_s']:.3f}" if error["predicted_id"] else "MISS"
                     for error in row["boundary_errors"]["per_take"]]] for name, row in cases.items()]), "", explanation, "", "Warnings:", ""]
    lines += [f"- {name}: {'; '.join(row['warnings']) or 'none'}" for name, row in cases.items()]
    return "\n".join(lines)


def run_experiment(root: Path = ROOT, output: Path | None = None) -> str:
    """Return and save exactly two whole-memo holdouts using cached AST scores and unchanged pipeline defaults."""
    memos = load_memos(root)
    destination = output or root / "outputs" / "learned_frames"
    destination.mkdir(parents=True, exist_ok=True)
    report = ["# Learned frame classifier: leave-one-memo-out experiment", "",
              f"scikit-learn {sklearn.__version__}; cached AST scores only; no AST or Spark calls, clip exports, or production changes.", "",
              "Exactly two folds: train memo2 -> test memo1, then train memo1 -> test memo2. "
              "No random frame splits: adjacent 0.5 s frames share overlapping 2 s AST windows and neighboring-frame features, so random splits leak information.", "",
              "Features: speech, singing group, instrument group, raw Singing/Humming/Music; RMS minus the memo's 10th-percentile RMS and median RMS; previous/next speech and singing-group values. "
              "Scores use existing overlapping-window averaging. Neighbor features use the full timeline before exclusions and repeat the endpoint at file edges. "
              "RMS centering uses each full unlabeled recording, including the held-out memo; this is an offline, noncausal experiment. StandardScaler fits only the training memo's scored frames.", "",
              "Midpoint targets map singing/instrument to music; gaps are silence; cmd:/take: annotations never affect targets. "
              "Unclear frames are dropped from training and metrics but predicted for contiguous downstream processing.", "",
              "Fixed models: standardized multinomial logistic regression (lbfgs, C=1, balanced weights, max_iter=5000, seed=0); "
              "decision tree (depth=3, balanced weights, seed=0). No tuning, feature selection, random splits, or combined-memo refit.", "",
              "Hand rules use current voting/mode filtering. Models use direct class predictions, without hand-rule voting/mode filtering. "
              "All methods then use the same merge/absorb, cut, unlabeled recovery, and edge trim defaults (protect_speech_runs=False, edge_trim=True, trim_singing_min=0.0018). "
              "Primary frame comparisons follow this common region processing; pre-absorption accuracy is also shown.", "",
              "Oracles attach truth commands to maximum-overlap speech regions; latest command wins collisions. Current intent mapping: "
              "file -> named section, keep -> unsorted, reject -> rejected, none -> non-command. target=next remains previous with a warning. "
              "Positive hits require same section and >=50% truth coverage, excluding rejected truth. Boundaries independently match maximum overlap across all sections, including unlabeled/rejected; misses have no overlap, extras are unused predictions. "
              "Memo1 uses numeric fixture RMS/duration and its existing A/B/C boundary expectations; memo2 decodes audio for RMS only.", "",
              "## Data", "", _table(["Memo", "Duration (s)", "Frames", "Scored", "Unclear", "Targets music/speech/silence", "RMS p10 / median (dBFS)"], [
                  [memo.name, f"{memo.duration:.6f}", len(memo.frames), int(memo.frames["scored"].sum()), int((~memo.frames["scored"]).sum()),
                   "/".join(str(int((memo.frames['target'] == kind).sum())) for kind in FRAME_KINDS),
                   f"{np.percentile(memo.rms, 10):.3f}/{np.median(memo.rms):.3f}"] for memo in memos]), ""]
    results: dict[str, Any] = {"features": FEATURES, "defaults": {"protect_speech_runs": False, "edge_trim": True}, "folds": {}}
    for memo in memos:
        memo.frames.loc[memo.frames["scored"], ["start", "end", *FEATURES, "target"]].to_csv(destination / f"{memo.name}_frames.csv", index=False)
    for train, test in ((memos[1], memos[0]), (memos[0], memos[1])):
        logistic, tree = fit_models(train.frames)
        hand = frame_labels_from_scores(test.scores, test.rms, KeeperConfig())[0]
        predictions = {"hand rules": hand, "logistic regression": logistic.predict(test.frames[FEATURES]).tolist(),
                       "decision tree": tree.predict(test.frames[FEATURES]).tolist()}
        cases = {name: evaluate_labels(test, labels, destination / test.name / name.replace(" ", "_")) for name, labels in predictions.items()}
        explanation, parameters = explain_models(logistic, tree)
        report += [render_fold(train, test, cases, explanation), ""]
        audit = test.frames[["start", "end", "target", "scored"]].copy()
        for name, row in cases.items():
            audit[name], audit[name + " final"] = row["predicted_frames"], row["final_frames"]
        audit.to_csv(destination / f"{test.name}_predictions.csv", index=False)
        results["folds"][test.name] = {"training_memo": train.name, "parameters": parameters, "cases": cases}
    report += ["## Interpretation", ""]
    for name, fold in results["folds"].items():
        cases = fold["cases"]
        baseline = cases["hand rules"]
        report.append(f"- Held-out {name}: hand-rule accuracy {baseline['frame_metrics']['overall_accuracy']:.6f}, positive hits "
                      f"{baseline['positive_hits']}/{baseline['positive_total']}; " + "; ".join(
                          f"{method} accuracy {cases[method]['frame_metrics']['overall_accuracy']:.6f}, positive hits "
                          f"{cases[method]['positive_hits']}/{cases[method]['positive_total']}"
                          for method in ("logistic regression", "decision tree")) + ".")
    both_lose_hits = all(fold["cases"][method]["positive_hits"] < fold["cases"]["hand rules"]["positive_hits"]
                         for fold in results["folds"].values() for method in ("logistic regression", "decision tree"))
    first = results["folds"]["memo1"]["cases"]
    tree_gain_with_hit_loss = (first["decision tree"]["frame_metrics"]["overall_accuracy"] > first["hand rules"]["frame_metrics"]["overall_accuracy"]
                              and first["decision tree"]["positive_hits"] < first["hand rules"]["positive_hits"])
    findings = "Both learned methods lose positive take hits against the hand rules on both held-out memos. " if both_lose_hits else "Positive take-hit comparisons are shown separately for each held-out memo. "
    if tree_gain_with_hit_loss:
        findings += "The tree's memo1 frame-accuracy gain does not preserve command-based take filing. "
    report += ["", findings + "Section-agnostic boundary matches can still exist for unlabeled takes, so boundary matches are not positive section hits.", "",
               "The two RMS features differ by a file-level constant within each training memo. StandardScaler makes their training columns identical, "
               "which explains their equal logistic coefficients; the requested feature set was retained without selection or tuning.", "",
               "Only two recordings are available, and memo1 supplies just one short training recording. "
               "These held-out results describe transfer between these memos, not a production validation. "
               "Frame accuracy alone does not establish command/take quality; inspect the downstream hits, misses, extras, and boundaries. "
               "No model was selected or integrated into Keeper.", "", "Input score SHA-256 (verified unchanged after the experiment):", ""]
    for memo in memos:
        if hashlib.sha256(memo.score_path.read_bytes()).hexdigest() != memo.score_sha256:
            raise AssertionError("Experiment changed its score cache")
        report.append(f"- {memo.name}: `{memo.score_sha256}`")
    text = "\n".join(report) + "\n"
    (destination / "report.md").write_text(text, encoding="utf-8")
    (destination / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return text


def main() -> int:
    """Return zero after printing the cached-only learned-frame experiment report."""
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--out", type=Path)
    args = cli.parse_args()
    print(run_experiment(output=args.out), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
