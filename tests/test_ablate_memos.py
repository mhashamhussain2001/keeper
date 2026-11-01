"""Cached-only oracle ablations, fixture command normalization, and default decisions."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from keeper.config import KeeperConfig
from scripts.ablate_memos import measure_case, normalize_oracles, not_worse


def test_legacy_fixture_commands_retain_oracle_variants() -> None:
    """Return None after checking old section/variant truth converts to the current command grammar."""
    truth = [(8.25, 9.21, "cmd:chorus A"), (0.0, 4.71, "music")]
    normalized, variants = normalize_oracles(truth)
    assert normalized == [(8.25, 9.21, "cmd:file section=chorus"), (0.0, 4.71, "music")]
    assert variants == {(8.25, 9.21, "cmd:file section=chorus"): "A"}


def test_ablation_rebuilds_oracles_and_never_scores(tmp_path: Path) -> None:
    """Return None after checking each flag setting maps its own speech regions from cached numeric data."""
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "meta.json").write_text('{"duration_s": 8.0}', encoding="utf-8")
    rms = fixture / "rms.json"
    rms.write_text(json.dumps([-20.0] * 16), encoding="utf-8")
    starts = np.arange(16) * 0.5
    cache = fixture / "scores.csv"
    pd.DataFrame({"start": starts, "end": starts + 0.5, "singing": 0.9, "instrument": 0.1,
                  "speech": np.where(starts >= 7.0, 0.9, 0.1)}).to_csv(cache, index=False)
    original = cache.read_bytes()
    truth = [(0.0, 7.0, "music"), (7.0, 8.0, "speech"), (7.1, 7.8, "cmd:file section=chorus")]
    takes = [(0.0, 7.0, "take:chorus")]
    baseline = measure_case(fixture, cache, truth, takes, tmp_path / "none",
                            KeeperConfig(protect_speech_runs=False, edge_trim=False), rms)
    protected = measure_case(fixture, cache, truth, takes, tmp_path / "protect",
                             KeeperConfig(protect_speech_runs=True, edge_trim=False), rms)
    assert baseline["commands_detected"] == 0 and baseline["positive_hits"] == 0
    assert protected["commands_detected"] == 1 and protected["positive_hits"] == 1
    assert baseline["frame_metrics"]["overall_accuracy"] == 7 / 8
    assert protected["frame_metrics"]["overall_accuracy"] == 1.0
    assert not_worse(protected, baseline)
    assert not not_worse(baseline, protected)
    assert cache.read_bytes() == original


def test_missing_cache_never_falls_back_to_ast(tmp_path: Path) -> None:
    """Return None after checking missing caches fail before scoring or creating output directories."""
    with pytest.raises(FileNotFoundError, match="refusing to rescore"):
        measure_case(tmp_path, tmp_path / "missing.csv", [], [], tmp_path / "result", KeeperConfig())
    assert not (tmp_path / "result").exists()
