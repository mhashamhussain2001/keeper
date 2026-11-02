"""Offline verification that the production timing probe uploads the requested M4A only."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import time_upload


def test_timing_upload_uses_m4a_and_records_stage_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking source selection, private access retention, and timestamp-derived timings."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data/test_2.m4a").write_bytes(b"requested-m4a")
    (tmp_path / "data/memo2.wav").write_bytes(b"do-not-upload-wav")
    history = [{"state": state, "stage": stage, "updated_at": f"2026-11-01T00:00:{second:02d}+00:00"}
               for state, stage, second in [("queued", "queued", 0), ("running", "loading audio", 2), ("done", "done", 7)]]
    status = {"state": "done", "stage": "done", "history": history}
    api = Mock(side_effect=[{"memo_id": "random-private-memo"}, status, {"title": "Memo"}])
    monkeypatch.setattr(time_upload.seed_modal, "api_request", api)
    report = time_upload.measure("https://example.modal.run", tmp_path)
    body = api.call_args_list[0].args[1]
    assert b"requested-m4a" in body and b"do-not-upload-wav" not in body
    assert b'filename="test_2.m4a"' in body and b"audio/mp4" in body
    assert [row["seconds"] for row in report["stages"]] == [2, 5]
    assert (tmp_path / "outputs/logic_v2_upload/owner.json").is_file()
    assert report["input"] == "data/test_2.m4a" and report["state"] == "done"
