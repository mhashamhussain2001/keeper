"""Exercise the local web API in-process with no sockets, models, or extra dependencies."""

import asyncio
import io
import json
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
import keeper.pipeline as pipeline
from keeper.types import SongMap, Take


_runner: asyncio.Runner


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


def request(method: str, path: str, body: bytes = b"", content_type: str = "") -> Response:
    """Return an in-process ASGI response, preserving decoded traversal paths for tests."""
    messages: list[Message] = []
    scope: Scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1", "method": method, "scheme": "http",
        "path": unquote(path), "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": [(b"content-type", content_type.encode()), (b"content-length", str(len(body)).encode())],
        "client": ("127.0.0.1", 1234), "server": ("testserver", 80),
    }

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
    directory = server.settings.outputs_root / "demo"
    clips = directory / "clips"
    clips.mkdir(parents=True)
    sf.write(clips / "chorus a.wav", np.zeros(1600, dtype=np.float32), 16000)
    take = Take("chorus-a", "chorus", "A", 0.0, 0.1, "memo.wav", None, "clips/chorus a.wav")
    SongMap("memo.wav", {"chorus": [take]}).to_json(directory / "songmap.json")
    (directory / "regions.json").write_text(json.dumps({"silence_gate_db": -45.0, "regions": [
        {"start": 0.0, "end": 0.2, "kind": "music", "mean_scores": {}},
    ]}), encoding="utf-8")
    return directory


def test_memo_list_ignores_incomplete_outputs(memo: Path) -> None:
    """Return None after checking only memo folders with song maps appear in sorted order."""
    (memo.parent / "incomplete").mkdir()
    assert request("GET", "/api/memos").json() == ["demo"]


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
    assert set(detail) == {"songmap", "regions", "duration_s"}
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


def upload(filename: str, audio: bytes) -> Response:
    """Return the multipart upload response for the supplied filename and audio bytes."""
    boundary = "keeper-test-upload"
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: audio/wav\r\n\r\n").encode() + audio + f"\r\n--{boundary}--\r\n".encode()
    return request("POST", "/api/memos", body, f"multipart/form-data; boundary={boundary}")


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
    audio = io.BytesIO()
    y = (0.1 * np.sin(2 * np.pi * 220 * np.arange(64000) / 16000)).astype(np.float32)
    sf.write(audio, y, 16000, format="WAV", subtype="PCM_16")
    response = upload("../../My Draft!.WAV", audio.getvalue())
    assert response.status == 201
    memo_id = response.json()["memo_id"]
    assert memo_id.startswith("my-draft-") and "/" not in memo_id
    assert (server.settings.uploads_root / f"{memo_id}.wav").read_bytes() == audio.getvalue()
    assert json.loads((memo.parent / memo_id / "mock_labels.json").read_text()) == {}
    detail = request("GET", f"/api/memos/{memo_id}").json()
    assert detail["duration_s"] == 4.0
    assert set(detail["songmap"]["sections"]) == {"unlabeled"}
    take = detail["songmap"]["sections"]["unlabeled"][0]
    assert request("GET", take["clip_url"]).body[:4] == b"RIFF"
    assert memo_id in request("GET", "/api/memos").json()


def test_upload_pipeline_failure_returns_json_without_listing_partial_memo(
    memo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return None after checking pipeline failures provide JSON errors and hide partial results."""
    def fail_pipeline(*args: Any, **kwargs: Any) -> None:
        """Return no result because this pipeline run simulates a missing local AST model."""
        raise RuntimeError("AST model is not cached")

    monkeypatch.setattr(server, "run_pipeline", fail_pipeline)
    response = upload("draft.wav", b"dummy audio")
    assert response.status == 500 and "AST model is not cached" in response.json()["detail"]
    assert request("GET", "/api/memos").json() == ["demo"]


def test_upload_rejects_unsupported_files(memo: Path) -> None:
    """Return None after checking unsupported extensions fail before pipeline processing."""
    response = upload("notes.txt", b"text")
    assert response.status == 400 and "audio file" in response.json()["detail"]


def test_static_home_page() -> None:
    """Return None after checking the buildless home page is served by the app."""
    response = request("GET", "/")
    assert response.status == 200 and "text/html" in response.headers["content-type"]
    assert b"Play selected back to back" in response.body
