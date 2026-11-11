"""Exercise the local web API in-process with no sockets, models, or extra dependencies."""

import asyncio
import io
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote, unquote

import numpy as np
import pandas as pd
import pytest
import soundfile as sf
from starlette.types import Message, Scope

import app.server as server
import app.processing as processing
import keeper.pipeline as pipeline
from keeper.types import SongMap, Take
from app.metadata import owner_fingerprint, save_meta


_runner: asyncio.Runner
OWNER_A = "11111111-1111-4111-8111-111111111111"
OWNER_B = "22222222-2222-4222-8222-222222222222"


def test_branding_and_public_icons() -> None:
    """Return None after verifying the canvas title and anonymous icon responses."""
    import re

    response = request("GET", "/", owner=None)
    html = response.body.decode("utf-8")
    assert "<title>Keeper · Your song canvas</title>" in html
    targets = re.findall(r'<link rel="(?:icon|alternate icon|apple-touch-icon)" href="([^"]+)"', html)
    assert set(targets) == {"/favicon.svg", "/favicon-32.png", "/favicon.ico", "/apple-touch-icon.png"}
    for path in targets + ["/favicon-192.png", "/favicon-512.png"]:
        assert (server.settings.static_root / path.lstrip("/")).is_file()
        icon = request("GET", path, owner=None)
        assert icon.status == 200
        expected = "image/svg+xml" if path.endswith(".svg") else "image/vnd.microsoft.icon" if path.endswith(".ico") else "image/png"
        accepted = {"image/x-icon", "image/vnd.microsoft.icon"} if path.endswith(".ico") else {expected}
        assert icon.headers["content-type"].split(";")[0] in accepted
        assert icon.headers["cache-control"] == "public, max-age=86400"


@pytest.fixture(scope="module", autouse=True)
def api_runtime() -> Iterator[None]:
    """Yield an ASGI runtime initialized before per-test network blocking."""
    global _runner
    # Windows creates a private loopback socket pair for event-loop wakeups.
    # Initialize it before the socket guard; API requests remain in-process.
    with asyncio.Runner() as _runner:
        _runner.get_loop()
        yield


@dataclass
class Response:
    """HTTP response captured from the ASGI app without an HTTP client dependency."""

    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        """Return the decoded response JSON."""
        return json.loads(self.body)


def request(method: str, path: str, body: bytes = b"", content_type: str = "", owner: str | None = OWNER_A) -> Response:
    """Return an in-process ASGI response, preserving decoded traversal paths for tests."""
    messages: list[Message] = []
    path, _, query = path.partition("?")
    scope: Scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1", "method": method, "scheme": "http",
        "path": unquote(path), "raw_path": path.encode(), "query_string": query.encode(), "root_path": "",
        "headers": [(b"content-type", content_type.encode()), (b"content-length", str(len(body)).encode())],
        "client": ("127.0.0.1", 1234), "server": ("testserver", 80),
    }
    if owner is not None:
        scope["headers"].append((b"x-keeper-owner", owner.encode()))

    async def receive() -> Message:
        """Return the complete request body as one ASGI message."""
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Message) -> None:
        """Return None after capturing an ASGI response message."""
        messages.append(message)

    _runner.run(server.app(scope, receive, send))
    start = next(message for message in messages if message["type"] == "http.response.start")
    return Response(start["status"], {key.decode(): value.decode() for key, value in start["headers"]},
                    b"".join(message.get("body", b"") for message in messages))


@pytest.fixture
def memo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return a temporary memo directory and point the API at isolated storage."""
    monkeypatch.setattr(server, "settings", server.Settings(
        outputs_root=tmp_path / "outputs", uploads_root=tmp_path / "uploads",
    ))
    monkeypatch.setattr(processing, "load_dotenv", lambda: False)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    directory = server.settings.outputs_root / "demo"
    clips = directory / "clips"
    clips.mkdir(parents=True)
    sf.write(clips / "chorus a.wav", np.zeros(1600, dtype=np.float32), 16000)
    take = Take("chorus-a", "chorus", "A", 0.0, 0.1, "memo.wav", None, "clips/chorus a.wav")
    SongMap("memo.wav", {"chorus": [take]}).to_json(directory / "songmap.json")
    (directory / "regions.json").write_text(json.dumps({"silence_gate_db": -45.0, "regions": [
        {"start": 0.0, "end": 0.2, "kind": "music", "mean_scores": {}},
    ]}), encoding="utf-8")
    save_meta(directory, {"title": "My memo", "original_filename": "memo.wav", "created_at": "2026-11-01T00:00:00+00:00", "owner": OWNER_A, "showcase": False})
    return directory


def test_memo_list_ignores_incomplete_outputs(memo: Path) -> None:
    """Return None after checking only memo folders with song maps appear in sorted order."""
    (memo.parent / "incomplete").mkdir()
    assert [row["id"] for row in request("GET", "/api/memos").json()] == ["demo"]


def test_detail_exposes_saved_commands_without_cache_internals(memo: Path) -> None:
    """Return None after checking command analysis is read-only and protected by memo ownership."""
    (memo / "regions.json").write_text(json.dumps({"regions": [
        {"start": 0, "end": .1, "kind": "music"}, {"start": .1, "end": .2, "kind": "speech"}]}))
    cache = json.dumps({"secret-cache-key": {"start": .1, "end": .2,
        "parsed": {"heard": "Chorus", "intent": "file", "section": "chorus", "target": "previous", "confidence": .99},
        "attempts": [{"raw_content": "internal"}]}})
    (memo / "spark_cache.json").write_text(cache)
    response = request("GET", "/api/memos/demo")
    assert response.status == 200
    assert response.json()["analysis"]["commands"][0]["heard"] == "Chorus"
    assert b"secret-cache-key" not in response.body and b"internal" not in response.body
    assert (memo / "spark_cache.json").read_text() == cache
    assert request("GET", "/api/memos/demo", owner=OWNER_B).status == 404


def test_older_memo_without_regions_still_opens(memo: Path) -> None:
    """Return None after checking absent analysis artifacts do not break listing or playback duration."""
    (memo / "regions.json").unlink()
    assert request("GET", "/api/memos").json()[0]["duration_s"] == .1
    detail = request("GET", "/api/memos/demo").json()
    assert detail["regions"] == [] and detail["analysis"] == {"commands": []}
    assert detail["duration_s"] == .1


def test_missing_outputs_list_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking a new installation lists no memos without creating storage."""
    monkeypatch.setattr(server, "settings", server.Settings(outputs_root=tmp_path / "absent"))
    response = request("GET", "/api/memos")
    assert response.status == 200 and response.json() == []


@pytest.mark.parametrize("absolute", [False, True])
def test_memo_detail_normalizes_clip_paths(memo: Path, absolute: bool) -> None:
    """Return None after checking detail shape, region duration, and portable clip URLs."""
    if absolute:
        songmap = SongMap.from_json(memo / "songmap.json")
        songmap.sections["chorus"][0].clip_path = str(memo / "clips" / "chorus a.wav")
        songmap.to_json(memo / "songmap.json")
    response = request("GET", "/api/memos/demo")
    assert response.status == 200
    detail = response.json()
    assert set(detail) == {"songmap", "regions", "duration_s", "meta", "peaks", "analysis"}
    assert detail["duration_s"] == 0.2
    assert detail["regions"][0]["kind"] == "music"
    take = detail["songmap"]["sections"]["chorus"][0]
    assert take["clip_path"] == "clips/chorus a.wav"
    assert take["clip_url"] == "/api/memos/demo/clips/clips/chorus%20a.wav"
    assert "clip_url" not in json.loads((memo / "songmap.json").read_text())["sections"]["chorus"][0]


def test_clip_serving_and_missing_paths(memo: Path) -> None:
    """Return None after checking WAV bytes, media type, and missing/non-WAV path errors."""
    response = request("GET", "/api/memos/demo/clips/clips/chorus%20a.wav")
    assert response.status == 200 and response.headers["content-type"] == "audio/wav"
    assert response.body == (memo / "clips" / "chorus a.wav").read_bytes()
    for path in ["/api/memos/missing", "/api/memos/demo/clips/missing.wav",
                 "/api/memos/demo/clips/songmap.json"]:
        assert request("GET", path).status == 404


@pytest.mark.parametrize("path", ["../../secret.wav", "..\\..\\secret.wav", "../demo-other/secret.wav"])
def test_clip_traversal_is_rejected(memo: Path, path: str) -> None:
    """Return None after checking encoded slash, Windows slash, and sibling traversal return 404."""
    sf.write(memo.parent.parent / "secret.wav", np.zeros(1600, dtype=np.float32), 16000)
    sibling = memo.parent / "demo-other"
    sibling.mkdir()
    (sibling / "secret.wav").write_bytes(b"must not escape")
    response = request("GET", f"/api/memos/demo/clips/{quote(path, safe='')}")
    assert response.status == 404


def test_absolute_clip_traversal_is_rejected(memo: Path) -> None:
    """Return None after checking absolute paths outside the memo cannot be served."""
    outside = memo.parent / "outside.wav"
    sf.write(outside, np.zeros(1600, dtype=np.float32), 16000)
    assert request("GET", f"/api/memos/demo/clips/{quote(str(outside), safe='')}").status == 404


def upload(filename: str, audio: bytes, title: str | None = None, owner: str | None = OWNER_A,
           content_type: str = "audio/wav") -> Response:
    """Return the multipart upload response for the supplied filename and audio bytes."""
    boundary = "keeper-test-upload"
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n").encode() + audio + b"\r\n"
    if title is not None:
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="title"\r\n\r\n{title}\r\n'.encode()
    body += f"--{boundary}--\r\n".encode()
    return request("POST", "/api/memos", body, f"multipart/form-data; boundary={boundary}", owner=owner)


class MusicScorer:
    """Offline classifier stub for running the actual upload pipeline and clip exporter."""

    def score_windows(self, y: np.ndarray, sr: int, win_s: float, hop_s: float) -> pd.DataFrame:
        """Return one music window covering the entire synthetic memo."""
        return pd.DataFrame([{"start": 0.0, "end": len(y) / sr,
                              "singing": 0.95, "speech": 0.01, "instrument": 0.05}])


def test_upload_runs_pipeline_offline_and_returns_playable_unlabeled_take(
    memo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return None after checking sanitized uploads run the real pipeline with an empty mock parser."""
    monkeypatch.setattr(pipeline, "ASTScorer", MusicScorer)
    monkeypatch.setattr(server, "start_processing", lambda memo_id: processing.process_memo(memo_id, server.settings))
    audio = io.BytesIO()
    y = (0.1 * np.sin(2 * np.pi * 220 * np.arange(64000) / 16000)).astype(np.float32)
    sf.write(audio, y, 16000, format="WAV", subtype="PCM_16")
    response = upload("../../My Draft!.WAV", audio.getvalue())
    assert response.status == 201
    memo_id = response.json()["memo_id"]
    assert len(memo_id) == 32 and all(char in "0123456789abcdef" for char in memo_id)
    assert (server.settings.uploads_root / f"{memo_id}.wav").read_bytes() == audio.getvalue()
    assert json.loads((memo.parent / memo_id / "mock_labels.json").read_text()) == {}
    detail = request("GET", f"/api/memos/{memo_id}").json()
    assert detail["duration_s"] == 4.0
    assert set(detail["songmap"]["sections"]) == {"unlabeled"}
    take = detail["songmap"]["sections"]["unlabeled"][0]
    assert request("GET", take["clip_url"]).body[:4] == b"RIFF"
    assert memo_id in [row["id"] for row in request("GET", "/api/memos").json()]


@pytest.mark.parametrize("legacy_showcase", [None, False, True])
def test_original_filename_stays_private_in_every_response(
    memo: Path, monkeypatch: pytest.MonkeyPatch, legacy_showcase: bool | None,
) -> None:
    """Return None after checking uploads and legacy private/showcase artifacts cannot expose an address."""
    from zipfile import ZipFile

    monkeypatch.setattr(pipeline, "ASTScorer", MusicScorer)
    monkeypatch.setattr(server, "start_processing", lambda memo_id: processing.process_memo(memo_id, server.settings))
    audio = io.BytesIO()
    sf.write(audio, np.full((4 * 44100, 2), .1, dtype=np.float32), 44100, format="WAV")
    response = upload("123 Example Street 4.m4a", audio.getvalue())
    assert response.status == 201 and b"Example Street" not in response.body
    memo_id = response.json()["memo_id"]
    directory = memo.parent / memo_id
    status_path = directory / "status.json"
    stored_status = json.loads(status_path.read_text())
    received = next(event["data"] for event in stored_status["events"] if event["type"] == "received")
    assert received == {"duration_s": 4.0, "sample_rate": 44100, "channels": 2}
    if legacy_showcase is not None:
        for event in stored_status["events"]:
            event["data"]["filename"] = "123 Example Street 4.m4a"
        stored_status["events"][0]["filename"] = "123 Example Street 4.m4a"
        status_path.write_text(json.dumps(stored_status))
        metadata = json.loads((directory / "meta.json").read_text())
        metadata["showcase"] = legacy_showcase
        save_meta(directory, metadata)
        songmap = json.loads((directory / "songmap.json").read_text())
        songmap["source_file"] = "123 Example Street 4.m4a"
        for takes in songmap["sections"].values():
            for take in takes:
                take["source_file"] = "123 Example Street 4.m4a"
        (directory / "songmap.json").write_text(json.dumps(songmap))
    paths = ["/api/memos", f"/api/memos/{memo_id}", f"/api/memos/{memo_id}/status", f"/api/memos/{memo_id}/canvas"]
    for path in paths:
        reply = request("GET", path)
        assert reply.status == 200
        assert b"Example Street" not in reply.body and b"original_filename" not in reply.body
        assert b'"filename"' not in reply.body and b'"source_file"' not in reply.body
    detail = request("GET", f"/api/memos/{memo_id}").json()
    take = next(take for takes in detail["songmap"]["sections"].values() for take in takes)
    for path in [take["clip_url"], f"/api/memos/{memo_id}/recording", f"/api/memos/{memo_id}/export/stems"]:
        reply = request("GET", path)
        assert reply.status == 200 and "Example Street" not in unquote(str(reply.headers))
        if path.endswith("/stems"):
            with ZipFile(io.BytesIO(reply.body)) as archive:
                assert "Example Street" not in str(archive.namelist())
                assert b"Example Street" not in archive.read("song_canvas.txt")
    if legacy_showcase:
        for path in paths[:3]:
            assert b"Example Street" not in request("GET", path, owner=None).body
    else:
        reply = request("PATCH", f"/api/memos/{memo_id}/takes/{take['id']}", b'{"section":"verse"}', "application/json")
        assert reply.status == 200 and b"Example Street" not in reply.body and b'"source_file"' not in reply.body
    if legacy_showcase is not None:
        assert "Example Street" in status_path.read_text()  # Scrubbing must not rewrite history.
    assert "123 Example Street 4.m4a" in (directory / "upload.json").read_text()


def test_upload_pipeline_failure_returns_json_without_listing_partial_memo(
    memo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return None after checking background failures provide status errors and hide partial results."""
    def fail_pipeline(*args: Any, **kwargs: Any) -> None:
        """Return no result because this pipeline run simulates a missing local AST model."""
        raise RuntimeError("AST model is not cached")

    monkeypatch.setattr(processing, "run_pipeline", fail_pipeline)
    monkeypatch.setattr(server, "start_processing", lambda memo_id: processing.process_memo(memo_id, server.settings))
    audio = io.BytesIO()
    sf.write(audio, np.zeros(1600, dtype=np.float32), 16000, format="WAV")
    response = upload("draft.wav", audio.getvalue())
    assert response.status == 201
    status = request("GET", f"/api/memos/{response.json()['memo_id']}/status").json()
    assert status["state"] == "failed" and "AST model is not cached" in status["error"]
    assert [row["id"] for row in request("GET", "/api/memos").json()] == ["demo"]


def test_upload_rejects_unsupported_files(memo: Path) -> None:
    """Return None after checking unsupported extensions fail before pipeline processing."""
    response = upload("notes.txt", b"text", content_type="text/plain")
    assert response.status == 400 and "audio file" in response.json()["detail"]


@pytest.mark.parametrize("content_type", ["audio/x-m4a", "application/octet-stream"])
def test_upload_accepts_real_m4a(memo: Path, monkeypatch: pytest.MonkeyPatch, content_type: str) -> None:
    """Return None after checking real AAC voice memos decode and queue with iOS content types."""
    from unittest.mock import Mock

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is required to generate a real M4A voice memo")
    source = memo.parent / "voice.m4a"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=16000:duration=0.25", "-ac", "1",
                    "-c:a", "aac", str(source)], check=True, capture_output=True, timeout=30)
    start = Mock()
    monkeypatch.setattr(server, "start_processing", start)
    response = upload(source.name, source.read_bytes(), content_type=content_type)
    assert response.status == 201
    memo_id = response.json()["memo_id"]
    manifest = json.loads((memo.parent / memo_id / "upload.json").read_text())
    assert 0.0 < manifest["duration_s"] < 1.0
    assert (server.settings.uploads_root / manifest["filename"]).read_bytes() == source.read_bytes()
    start.assert_called_once_with(memo_id)


@pytest.mark.parametrize("content_type", ["audio/x-m4a", "application/octet-stream", "text/plain"])
def test_upload_rejects_fake_m4a(memo: Path, monkeypatch: pytest.MonkeyPatch, content_type: str) -> None:
    """Return None after checking a renamed text file fails decoding before processing starts."""
    from unittest.mock import Mock

    start = Mock()
    monkeypatch.setattr(server, "start_processing", start)
    response = upload("fake.m4a", b"This is text, not audio.", content_type=content_type)
    assert response.status == 400
    assert response.json()["detail"].startswith("Cannot read this audio file; upload valid audio")
    assert "Up to 30 minutes and 150 MB" in response.json()["detail"]
    start.assert_not_called()
    assert not list(server.settings.uploads_root.iterdir())


@pytest.mark.parametrize("content_type", ["audio/x-m4a", "audio/m4a", "audio/mp4", "audio/aac",
                                         "audio/x-caf", "audio/custom", "application/octet-stream"])
def test_upload_accepts_audio_content_type_without_known_extension(
    memo: Path, monkeypatch: pytest.MonkeyPatch, content_type: str,
) -> None:
    """Return None after checking audio MIME types and generic binary uploads reach the decoder."""
    monkeypatch.setattr(server, "start_processing", lambda memo_id: None)
    audio = io.BytesIO()
    sf.write(audio, np.zeros(1600, dtype=np.float32), 16000, format="WAV")
    assert upload("voice.unknown", audio.getvalue(), content_type=content_type).status == 201


@pytest.mark.parametrize("extension", [".m4a", ".mp4", ".aac", ".caf", ".mp3", ".wav",
                                       ".aif", ".aiff", ".flac", ".ogg", ".opus", ".webm"])
def test_upload_accepts_audio_extension_with_unreliable_content_type(
    memo: Path, monkeypatch: pytest.MonkeyPatch, extension: str,
) -> None:
    """Return None after checking each picker extension permits valid audio despite its reported MIME type."""
    monkeypatch.setattr(server, "start_processing", lambda memo_id: None)
    audio = io.BytesIO()
    sf.write(audio, np.zeros(1600, dtype=np.float32), 16000, format="WAV")
    assert upload("voice" + extension.upper(), audio.getvalue(), content_type="text/plain").status == 201


def test_static_home_page() -> None:
    """Return None after checking the buildless home page is served by the app."""
    response = request("GET", "/")
    assert response.status == 200 and "text/html" in response.headers["content-type"]
    assert b"Play through" in response.body and b"Recording" in response.body and b"Canvas" in response.body


@pytest.mark.parametrize("parser_name", ["mock", "spark"])
def test_status_lifecycle_and_frontend_json(memo: Path, monkeypatch: pytest.MonkeyPatch, parser_name: str) -> None:
    """Return None after checking stages, parser selection, heard propagation, and status/detail shapes."""
    monkeypatch.setattr(pipeline, "ASTScorer", MusicScorer)
    if parser_name == "spark":
        monkeypatch.setenv("BASETEN_API_KEY", "synthetic-test-key")
        monkeypatch.setenv("KEEPER_COMMAND_FREE", "0")
    monkeypatch.setattr(server, "start_processing", lambda memo_id: processing.process_memo(memo_id, server.settings))
    audio = io.BytesIO()
    sf.write(audio, np.full(64000, 0.1, dtype=np.float32), 16000, format="WAV")
    memo_id = upload("stages.wav", audio.getvalue()).json()["memo_id"]
    result = request("GET", f"/api/memos/{memo_id}/status")
    assert result.status == 200
    status = result.json()
    assert status["state"] == "done" and status["error"] is None and status["parser"] == parser_name
    assert [row["stage"] for row in status["history"]] == ["queued", "loading audio", "scoring audio (AST)",
        f"reading commands ({'Spark' if parser_name == 'spark' else 'mock'})", "cutting takes", "exporting clips", "done"]
    assert all(row["updated_at"] for row in status["history"])
    detail = request("GET", f"/api/memos/{memo_id}").json()
    assert detail["songmap"]["parser"] == parser_name
    assert "heard" in detail["songmap"]["sections"]["unlabeled"][0]


def test_upload_returns_before_local_thread_finishes(memo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking a real background thread lets the upload return before processing completes."""
    from threading import Event
    entered, finish = Event(), Event()
    threads = []

    def blocked_pipeline(*args: Any, **kwargs: Any) -> None:
        """Return None after allowing the test to release its simulated long pipeline."""
        entered.set()
        if not finish.wait(5):
            raise RuntimeError("Test did not release background processing")

    def start(memo_id: str) -> None:
        """Return None after recording the real background thread for deterministic cleanup."""
        threads.append(processing.start_local_processing(memo_id, server.settings))

    monkeypatch.setattr(processing, "run_pipeline", blocked_pipeline)
    monkeypatch.setattr(server, "start_processing", start)
    audio = io.BytesIO()
    sf.write(audio, np.zeros(1600, dtype=np.float32), 16000, format="WAV")
    try:
        result = upload("background.wav", audio.getvalue())
        assert result.status == 201 and entered.wait(2) and threads[0].is_alive()
        memo_id = result.json()["memo_id"]
        assert request("GET", f"/api/memos/{memo_id}/status").json()["state"] == "running"
    finally:
        finish.set()
        for thread in threads:
            thread.join(5)
    assert request("GET", f"/api/memos/{memo_id}/status").json()["state"] == "done"


@pytest.mark.parametrize("kind", ["invalid", "empty", "size", "duration"])
def test_upload_validation(memo: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    """Return None after checking corrupt, empty, oversized, and over-duration audio cannot be queued."""
    import app.uploads as uploads
    from unittest.mock import Mock
    start = Mock()
    monkeypatch.setattr(server, "start_processing", start)
    audio = io.BytesIO()
    sf.write(audio, np.zeros(32000, dtype=np.float32), 16000, format="WAV")
    content = audio.getvalue()
    if kind == "invalid":
        content = b"not a wav"
    elif kind == "empty":
        content = b""
    elif kind == "size":
        monkeypatch.setenv("KEEPER_MAX_UPLOAD_MB", str(100 / (1024 * 1024)))
    else:
        monkeypatch.setenv("KEEPER_MAX_AUDIO_MINUTES", str(1 / 60))
    response = upload("bad.wav", content)
    assert response.status == 400 and isinstance(response.json()["detail"], str)
    start.assert_not_called()
    assert [row["id"] for row in request("GET", "/api/memos").json()] == ["demo"]


def test_environment_storage_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Return None after checking storage roots come from environment variables without changing local defaults."""
    monkeypatch.setenv("KEEPER_OUTPUTS_DIR", str(tmp_path / "mounted-outputs"))
    monkeypatch.setenv("KEEPER_UPLOADS_DIR", str(tmp_path / "mounted-uploads"))
    settings = server.Settings()
    assert settings.outputs_root == tmp_path / "mounted-outputs"
    assert settings.uploads_root == tmp_path / "mounted-uploads"


def test_status_legacy_and_missing(memo: Path) -> None:
    """Return None after checking legacy demos are done and nonexistent jobs return 404."""
    assert request("GET", "/api/memos/demo/status").json()["state"] == "done"
    assert request("GET", "/api/memos/absent/status").status == 404


@pytest.mark.parametrize("owner", [None, OWNER_B, "invalid-token"])
def test_nonowners_cannot_access_any_memo_artifact(memo: Path, owner: str | None) -> None:
    """Return None after checking the list and every read/mutation route enforce browser ownership."""
    assert request("GET", "/api/memos", owner=owner).json() == []
    for path in ["/api/memos/demo", "/api/memos/demo/status", "/api/memos/demo/clips/clips/chorus%20a.wav"]:
        assert request("GET", path, owner=owner).status == 404
    assert request("DELETE", "/api/memos/demo", owner=owner).status == 404
    assert request("PATCH", "/api/memos/demo", b'{"title":"Stolen"}', "application/json", owner=owner).status == 404
    assert memo.is_dir()


def test_showcase_is_public_but_owner_edits_stay_private(memo: Path) -> None:
    """Return None after checking showcases are listed/readable without granting mutation or revealing tokens."""
    from app.metadata import set_showcase
    set_showcase(server.settings, "demo", True)
    response = request("GET", "/api/memos", owner=None)
    assert response.json()[0]["title"] == "My memo" and not response.json()[0]["owned"]
    assert OWNER_A.encode() not in response.body
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "X-Keeper-Owner"
    assert request("GET", "/api/memos/demo", owner=None).status == 200
    assert request("GET", "/api/memos/demo/status", owner=OWNER_B).status == 200
    assert request("GET", "/api/memos/demo/clips/clips/chorus%20a.wav", owner=None).status == 200
    assert request("DELETE", "/api/memos/demo", owner=OWNER_B).status == 403
    assert request("DELETE", "/api/memos/demo", owner=OWNER_A).status == 403
    assert memo.is_dir()
    assert request("PATCH", "/api/memos/demo", b'{"title":"No"}', "application/json", owner=None).status == 404
    set_showcase(server.settings, "demo", False)
    assert request("GET", "/api/memos", owner=None).json() == []


def test_owner_rename_validation_and_metadata_preservation(memo: Path) -> None:
    """Return None after checking owner renames preserve identity/privacy and reject empty or privileged fields."""
    before = json.loads((memo / "meta.json").read_text())
    response = request("PATCH", "/api/memos/demo", b'{"title":"  New chorus  "}', "application/json")
    assert response.status == 200 and response.json()["title"] == "New chorus"
    after = json.loads((memo / "meta.json").read_text())
    assert {key: value for key, value in after.items() if key != "title"} == {key: value for key, value in before.items() if key != "title"}
    assert request("GET", "/api/memos").json()[0]["title"] == "New chorus"
    for body in [b'{"title":" "}', b'{"title":"x","showcase":true}', b'{"title":"x","owner":null}']:
        assert request("PATCH", "/api/memos/demo", body, "application/json").status == 422


def test_delete_removes_owner_artifacts_and_original_upload(memo: Path) -> None:
    """Return None after checking recursive deletion stays inside storage and removes the matching source."""
    server.settings.uploads_root.mkdir()
    source = server.settings.uploads_root / "demo.wav"
    source.write_bytes(b"source")
    other = server.settings.uploads_root / "other.wav"
    other.write_bytes(b"leave")
    (memo / "upload.json").write_text(json.dumps({"filename": "demo.wav"}))
    response = request("DELETE", "/api/memos/demo")
    assert response.status == 200 and response.json() == {"deleted": True}
    assert not memo.exists() and not source.exists() and other.read_bytes() == b"leave"
    assert request("GET", "/api/memos").json() == []
    assert request("GET", "/api/memos/demo/status").status == 404


def test_active_memo_cannot_be_deleted_or_recreated_by_a_worker(memo: Path) -> None:
    """Return None after checking deletion is deferred while a processing worker may still write."""
    from app.storage import write_status
    write_status(memo, "running", "scoring audio (AST)")
    assert request("DELETE", "/api/memos/demo").status == 409 and memo.exists()


def test_upload_records_local_title_private_owner_and_peaks(memo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking local titles, random IDs, private metadata, and 800 waveform peaks."""
    monkeypatch.setattr(pipeline, "ASTScorer", MusicScorer)
    monkeypatch.setattr(server, "start_processing", lambda memo_id: processing.process_memo(memo_id, server.settings))
    audio = io.BytesIO()
    sf.write(audio, np.full(64000, 0.1, dtype=np.float32), 16000, format="WAV")
    title = "Memo, Nov 1, 1:23 PM"
    response = upload("original.wav", audio.getvalue(), title)
    memo_id = response.json()["memo_id"]
    metadata = json.loads((memo.parent / memo_id / "meta.json").read_text())
    assert metadata["owner"] == owner_fingerprint(OWNER_A) and metadata["title"] == title and metadata["original_filename"] == "original.wav"
    assert metadata["showcase"] is False and metadata["created_at"]
    detail = request("GET", f"/api/memos/{memo_id}").json()
    assert len(detail["peaks"]) == 800 and max(detail["peaks"]) > 0
    assert upload("original.wav", audio.getvalue(), owner=None).status == 400


def test_owner_memos_sort_before_showcases_by_creation_time(memo: Path) -> None:
    """Return None after checking newest owned memos precede another owner's showcase."""
    import shutil
    for name, owner, created, showcase in [("newer", OWNER_A, "2026-11-02T00:00:00+00:00", False),
                                           ("public", None, "2026-11-03T00:00:00+00:00", True),
                                           ("other", OWNER_B, "2026-11-04T00:00:00+00:00", False)]:
        destination = memo.parent / name
        shutil.copytree(memo, destination)
        save_meta(destination, {"title": name, "original_filename": "hidden.wav", "created_at": created, "owner": owner, "showcase": showcase})
    assert [row["id"] for row in request("GET", "/api/memos").json()] == ["newer", "demo", "public"]


def test_ownerless_legacy_memo_is_not_claimed_by_anonymous_callers(memo: Path) -> None:
    """Return None after checking migrated owner=null does not grant anonymous or new-browser access."""
    metadata = json.loads((memo / "meta.json").read_text())
    metadata["owner"] = None
    save_meta(memo, metadata)
    assert request("GET", "/api/memos", owner=None).json() == []
    assert request("GET", "/api/memos/demo", owner=None).status == 404
    assert request("GET", "/api/memos/demo", owner=OWNER_A).status == 404


def test_command_free_splits_takes_at_short_pause(memo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after checking a two-second pause separates two takes without any spoken command."""
    monkeypatch.setattr(pipeline, "ASTScorer", MusicScorer)
    monkeypatch.setattr(server, "start_processing", lambda memo_id: processing.process_memo(memo_id, server.settings))
    tone = np.full(5 * 16000, 0.1, dtype=np.float32)
    audio = io.BytesIO()
    sf.write(audio, np.concatenate([tone, np.zeros(2 * 16000, dtype=np.float32), tone]), 16000, format="WAV")
    memo_id = upload("two.wav", audio.getvalue()).json()["memo_id"]
    assert len(request("GET", f"/api/memos/{memo_id}").json()["songmap"]["sections"]["unlabeled"]) == 2
