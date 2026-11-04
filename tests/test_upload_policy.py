"""Verify configurable admission, rolling quotas, token storage, headers and private logging."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import io
import json
import logging
import os
from pathlib import Path
import subprocess
from typing import Any
from uuid import uuid1, uuid4

import numpy as np
import pytest
import soundfile as sf

import app.processing as processing
import app.server as server
import app.uploads as uploads
from app.metadata import owner_fingerprint, owner_token, owns, save_meta
from app.policy import Policy, policy
from app.quotas import QuotaExceeded, reserve_upload
from tests.test_server import MusicScorer, api_runtime, memo, request, upload


def audio_bytes(duration_s: int = 1, rate: int = 16000) -> bytes:
    """Return a small synthetic PCM WAV without using personal recordings."""
    stream = io.BytesIO()
    sf.write(stream, np.full(duration_s * rate, .1, dtype=np.float32), rate, format="WAV")
    return stream.getvalue()


def send_audio(token: str, duration_s: int = 1, rate: int = 16000) -> Any:
    """Return an upload response using a generated, never-displayed client filename."""
    return upload(uuid4().hex + ".wav", audio_bytes(duration_s, rate), owner=token)


def finish_upload(memo_id: str) -> None:
    """Return None after making a synthetic admitted job terminal without invoking models."""
    path = server.settings.outputs_root / memo_id / "status.json"
    path.write_text(json.dumps({"state": "done", "events": []}))


def test_public_config_and_security_headers(memo: Path) -> None:
    """Return None after checking public policy and privacy headers on successes and errors."""
    config = request("GET", "/api/config", owner=None)
    assert config.json() == {"uploads_enabled": True, "max_audio_minutes": 30.0,
                             "max_upload_mb": 150.0, "retention_days": 14}
    for path in ["/api/config", "/api/memos/missing", "/robots.txt", "/missing-page"]:
        result = request("GET", path, owner=None)
        assert result.headers["x-robots-tag"] == "noindex, nofollow"
        assert result.headers["referrer-policy"] == "no-referrer"
        assert "access-control-allow-origin" not in result.headers
        if path.startswith("/api/"):
            assert result.headers["cache-control"] == "private, no-store"
            assert result.headers["x-content-type-options"] == "nosniff"
    assert b"Disallow: /" in request("GET", "/robots.txt").body


def test_production_docs_flag_disables_all_schema_routes() -> None:
    """Return None after checking a production-configured app has no documentation or schema routes."""
    root = Path(__file__).resolve().parents[1]
    code = "from app.server import app; assert not any(route.path in {'/docs','/redoc','/openapi.json','/docs/oauth2-redirect'} for route in app.routes)"
    result = subprocess.run([str(root / ".venv/Scripts/python.exe"), "-c", code], cwd=root,
                            env={**os.environ, "KEEPER_DOCS_ENABLED": "0"}, capture_output=True)
    assert result.returncode == 0


def test_unexpected_errors_have_safe_logs_and_headers(
    memo: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Return None after checking server errors retain security headers without logging exception contents."""
    marker = uuid4().hex

    def broken() -> None:
        """Return no storage because this synthetic failure contains a private marker."""
        raise RuntimeError(marker)

    monkeypatch.setattr(server, "reload_storage", broken)
    response = request("GET", "/api/memos", owner=None)
    assert response.status == 500 and response.headers["x-content-type-options"] == "nosniff"
    if marker in caplog.text or marker.encode() in response.body:
        pytest.fail("Sensitive exception content escaped the error boundary", pytrace=False)


def test_only_version_four_tokens_and_hashes_at_rest(memo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking token versions, backward-compatible reads and write-time hashing."""
    token = str(uuid4())
    legacy = {"title": "Legacy", "owner": token, "showcase": False,
              "created_at": datetime.now(timezone.utc).isoformat()}
    (memo / "meta.json").write_text(json.dumps(legacy))
    assert request("GET", "/api/memos/demo", owner=token).status == 200
    assert owns(legacy, token) and owner_token(str(uuid1())) is None
    assert owner_token("invalid") is None
    assert request("PATCH", "/api/memos/demo", b'{"title":"Updated"}', "application/json", owner=token).status == 200
    stored = json.loads((memo / "meta.json").read_text())
    assert stored["owner"] == owner_fingerprint(token) and owns(stored, token)
    monkeypatch.setattr(server, "start_processing", lambda identity: None)
    result = send_audio(str(uuid4()))
    assert result.status == 201
    uploaded = json.loads((memo.parent / result.json()["memo_id"] / "meta.json").read_text())
    assert len(uploaded["owner"]) == 64
    assert send_audio(str(uuid1())).status == 400


def test_metadata_only_limits_and_partial_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after rejecting a tiny thirty-one-minute WAV without invoking full decoding."""
    import librosa

    def forbid_decode(*args: Any, **kwargs: Any) -> None:
        """Return no audio because upload admission must inspect only metadata."""
        raise AssertionError("Unexpected full audio decode")

    monkeypatch.setattr(librosa, "load", forbid_decode)
    monkeypatch.setattr(uploads.shutil, "which", lambda command: None)
    source = tmp_path / "generated.wav"
    source.write_bytes(audio_bytes(31 * 60, 1))
    with pytest.raises(ValueError, match="Up to 30 minutes and 150 MB"):
        uploads.validate_audio(source)
    assert not source.exists()
    destination = tmp_path / "partial.wav"
    with pytest.raises(ValueError, match="Up to"):
        uploads.save_upload(io.BytesIO(b"x" * 128), destination, Policy(max_upload_mb=0.00001))
    assert not destination.exists()
    destination.write_bytes(b"preserve existing file")
    with pytest.raises(FileExistsError):
        uploads.save_upload(io.BytesIO(b"new"), destination)
    assert destination.read_bytes() == b"preserve existing file"


def test_ffprobe_metadata_path_and_environment_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking the native metadata probe and environment-configured admission limits."""
    source = tmp_path / "probe.wav"
    source.write_bytes(audio_bytes())
    monkeypatch.setattr(uploads.shutil, "which", lambda command: "ffprobe")
    calls = []

    def probe(args: list[str], **options: Any) -> subprocess.CompletedProcess[bytes]:
        """Return only synthetic native audio metadata while retaining probe options for verification."""
        calls.append((args, options))
        return subprocess.CompletedProcess(args, 0, json.dumps({"streams": [{"sample_rate": "48000", "channels": 2,
                                                                           "duration": "1200"}]}).encode())

    monkeypatch.setattr(uploads.subprocess, "run", probe)
    assert uploads.validate_audio(source) == 1200.0
    assert calls[0][1]["capture_output"] and "-show_entries" in calls[0][0]
    monkeypatch.setenv("KEEPER_MAX_AUDIO_MINUTES", "12")
    monkeypatch.setenv("KEEPER_MAX_UPLOAD_MB", "25")
    assert policy().max_audio_minutes == 12 and policy().max_upload_mb == 25
    with pytest.raises(ValueError, match="Up to 12 minutes and 25 MB"):
        uploads.validate_audio(source)


def test_kill_switch_and_busy_owner_cleanup(memo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking paused uploads, one active job and removal of refused source files."""
    monkeypatch.setattr(server, "start_processing", lambda identity: None)
    token = str(uuid4())
    monkeypatch.setenv("KEEPER_UPLOADS_ENABLED", "0")
    assert send_audio(token).status == 503
    assert not request("GET", "/api/config").json()["uploads_enabled"]
    monkeypatch.setenv("KEEPER_UPLOADS_ENABLED", "1")
    first = send_audio(token)
    assert first.status == 201
    before = {path.name for path in server.settings.uploads_root.iterdir()}
    refused = send_audio(token)
    assert refused.status == 429 and int(refused.headers["retry-after"]) > 0
    assert {path.name for path in server.settings.uploads_root.iterdir()} == before
    finish_upload(first.json()["memo_id"])
    assert send_audio(token).status == 201


def test_daily_count_minutes_global_budget_and_queue(memo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking independent browser quotas, global capacity and anonymous showcase access."""
    monkeypatch.setattr(server, "start_processing", lambda identity: None)
    token, other = str(uuid4()), str(uuid4())
    for _ in range(5):
        response = send_audio(token)
        assert response.status == 201
        finish_upload(response.json()["memo_id"])
    assert send_audio(token).status == 429
    queued = send_audio(other)
    assert queued.status == 201
    third_token = str(uuid4())
    third = send_audio(third_token)
    # Read queue counts with each browser's real identity, without ever exposing other memo IDs.
    assert request("GET", f"/api/memos/{queued.json()['memo_id']}/status", owner=other).json()["queue_ahead"] == 0
    assert request("GET", f"/api/memos/{third.json()['memo_id']}/status", owner=third_token).json()["queue_ahead"] == 1
    monkeypatch.setenv("KEEPER_DAILY_MINUTES", "0")
    exhausted = send_audio(str(uuid4()))
    assert exhausted.status == 429 and "Demo capacity reached" in exhausted.json()["detail"]
    metadata = json.loads((memo / "meta.json").read_text())
    metadata["showcase"] = True
    save_meta(memo, metadata)
    assert request("GET", "/api/memos/demo", owner=None).status == 200
    assert any(row["id"] == "demo" for row in request("GET", "/api/memos", owner=None).json())
    monkeypatch.setenv("KEEPER_DAILY_MINUTES", "240")
    monkeypatch.setenv("KEEPER_OWNER_DAILY_MINUTES", "0.01")
    assert send_audio(str(uuid4())).status == 429


def test_reservations_are_locked_rolling_and_not_raw_tokens(memo: Path) -> None:
    """Return None after racing reservations and advancing the rolling twenty-four-hour budget."""
    token = str(uuid4())
    limits = Policy(owner_daily_uploads=1)

    def reserve(index: int) -> bool:
        """Return whether this competing reservation acquired the single daily slot."""
        try:
            reserve_upload(server.settings, token, 60.0, f"job-{index}", limits, now=100000.0)
            return True
        except QuotaExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(8))) == 1
    ledger = server.settings.outputs_root / ".quota" / "usage.json"
    if token in ledger.read_text():
        pytest.fail("Raw browser identity found in quota storage", pytrace=False)
    reserve_upload(server.settings, token, 60.0, "next-day", limits, now=100000.0 + 86401)
    assert len(json.loads(ledger.read_text())) == 1


def test_private_upload_and_replay_strings_never_enter_logs(
    memo: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Return None after capturing upload, replay and processing-error logs without displaying private values."""
    token = str(uuid4())
    private_name, private_heard = uuid4().hex + ".wav", uuid4().hex
    monkeypatch.setattr(server, "start_processing", lambda identity: None)
    caplog.set_level(logging.DEBUG)
    result = upload(private_name, audio_bytes(), owner=token)
    assert result.status == 201
    identity = result.json()["memo_id"]
    directory = memo.parent / identity
    (directory / "status.json").write_text(json.dumps({"state": "done", "events": [
        {"type": "spark_result", "data": {"heard": private_heard}}]}))
    assert request("GET", f"/api/memos/{identity}/status", owner=token).status == 200

    def fail(*args: Any, **kwargs: Any) -> None:
        """Return no result because this failure deliberately contains private strings."""
        raise RuntimeError(token + private_name + private_heard)

    monkeypatch.setattr(processing, "run_pipeline", fail)
    processing.process_memo(identity, server.settings)
    if any(value in caplog.text for value in [token, private_name, private_heard]):
        pytest.fail("Sensitive data reached application logs", pytrace=False)
    assert logging.getLogger("uvicorn.access").disabled
