"""Offline checks for the fixed memo2 seed source and stage timing report."""

from pathlib import Path
import json
from typing import Any

import pytest

from scripts import seed_modal


def test_seed_uploads_only_memo2_and_measures_stages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking multipart source, polling, completed listing, and timestamp differences."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "memo2.wav").write_bytes(b"memo2-audio")
    uploads = data / "uploads"
    uploads.mkdir()
    (uploads / "private.wav").write_bytes(b"must-not-upload")
    monkeypatch.setattr(seed_modal, "ROOT", tmp_path)
    monkeypatch.setattr(seed_modal, "sleep", lambda seconds: None)
    calls = []
    history = [{"state": state, "stage": stage, "updated_at": f"2026-11-01T00:00:{second:02d}+00:00"}
               for state, stage, second in [("queued", "queued", 0), ("running", "loading audio", 2),
                                            ("running", "scoring audio (AST)", 3), ("done", "done", 7)]]
    states = iter([{"state": "queued", "stage": "queued"},
                   {"state": "done", "stage": "done", "history": history, "gpu": "Tesla T4", "parser": "spark"}])

    def api(url: str, body: bytes | None = None, content_type: str | None = None) -> Any:
        """Return fixture API responses while recording exactly what would be uploaded."""
        calls.append((url, body, content_type))
        if body is not None:
            return {"memo_id": "memo2-seed"}
        return next(states) if url.endswith("/status") else ["memo2-seed"]

    monkeypatch.setattr(seed_modal, "api_request", api)
    report = seed_modal.seed("https://example.modal.run")
    assert b"memo2-audio" in calls[0][1] and b"must-not-upload" not in calls[0][1]
    assert report["listed"] and report["state"] == "done" and report["gpu"] == "Tesla T4"
    assert [row["seconds"] for row in report["stages"]] == [2.0, 1.0, 4.0]
    assert (tmp_path / "outputs" / "modal_seed_report.json").is_file()


def test_seed_keeps_failed_stage_timings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking failed processing preserves its timing report and still raises."""
    from unittest.mock import Mock

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "memo2.wav").write_bytes(b"memo2-audio")
    monkeypatch.setattr(seed_modal, "ROOT", tmp_path)
    status = {"state": "failed", "stage": "failed", "error": "Error code: 401", "gpu": "Tesla T4",
              "parser": "spark", "history": [
                  {"state": "queued", "stage": "queued", "updated_at": "2026-11-01T00:00:00+00:00"},
                  {"state": "failed", "stage": "failed", "updated_at": "2026-11-01T00:01:00+00:00"}]}
    monkeypatch.setattr(seed_modal, "api_request", Mock(side_effect=[{"memo_id": "memo2-failed"}, status, []]))
    with pytest.raises(RuntimeError, match="401"):
        seed_modal.seed("https://example.modal.run")
    saved = json.loads((tmp_path / "outputs" / "modal_seed_report.json").read_text())
    assert saved["state"] == "failed" and not saved["listed"]
    assert saved["stages"] == [{"stage": "queued", "seconds": 60.0}]
