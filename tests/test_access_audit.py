"""Require an explicit access classification and enforce isolation for every HTTP route."""

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import pytest
import soundfile as sf

from app.metadata import save_meta
import app.server as server
from tests.test_server import api_runtime, memo, request


ACCESS_CLASSES = {
    ("GET", "/openapi.json"): "public", ("HEAD", "/openapi.json"): "public",
    ("GET", "/docs"): "public", ("HEAD", "/docs"): "public",
    ("GET", "/docs/oauth2-redirect"): "public", ("HEAD", "/docs/oauth2-redirect"): "public",
    ("GET", "/redoc"): "public", ("HEAD", "/redoc"): "public",
    ("*", ""): "public", ("GET", "/robots.txt"): "public", ("GET", "/api/config"): "public",
    ("GET", "/api/memos"): "public", ("POST", "/api/memos"): "owner-only",
    ("GET", "/api/memos/{memo_id}"): "showcase-readable",
    ("GET", "/api/memos/{memo_id}/status"): "showcase-readable",
    ("GET", "/api/memos/{memo_id}/recording"): "showcase-readable",
    ("GET", "/api/memos/{memo_id}/clips/{path:path}"): "showcase-readable",
    ("PATCH", "/api/memos/{memo_id}"): "owner-only",
    ("DELETE", "/api/memos/{memo_id}"): "owner-only",
    ("PATCH", "/api/memos/{memo_id}/takes/{take_id}"): "owner-only",
    ("GET", "/api/memos/{memo_id}/canvas"): "owner-only",
    ("PUT", "/api/memos/{memo_id}/canvas"): "owner-only",
    ("GET", "/api/memos/{memo_id}/export/stems"): "owner-only",
}


def test_every_route_is_audited() -> None:
    """Return None after rejecting any HTTP route or static mount without an access classification."""
    actual = {(method, route.path) for route in server.app.routes
              for method in (getattr(route, "methods", None) or {"*"})}
    assert not actual - ACCESS_CLASSES.keys()
    assert set(ACCESS_CLASSES.values()) == {"public", "showcase-readable", "owner-only"}


@pytest.fixture()
def audit_memo(memo: Path) -> tuple[Path, str]:
    """Return an isolated complete memo and a freshly generated browser identity."""
    token = str(uuid4())
    save_meta(memo, {"title": "Audit memo", "owner": token, "showcase": False,
                     "created_at": "2026-11-03T12:00:00+00:00"})
    server.settings.uploads_root.mkdir(parents=True, exist_ok=True)
    source = server.settings.uploads_root / "audit.wav"
    sf.write(source, np.zeros(16000, dtype=np.float32), 16000)
    (memo / "upload.json").write_text(json.dumps({"filename": source.name}))
    (memo / "status.json").write_text(json.dumps({"state": "done", "events": []}))
    return memo, token


ROUTE_CASES = [
    ("GET", "", None, "showcase-readable"),
    ("GET", "/status", None, "showcase-readable"),
    ("GET", "/recording", None, "showcase-readable"),
    ("GET", "/clips/clips/chorus%20a.wav", None, "showcase-readable"),
    ("GET", "/canvas", None, "owner-only"),
    ("GET", "/export/stems", None, "owner-only"),
    ("PATCH", "", {"title": "Updated memo"}, "owner-only"),
    ("PATCH", "/takes/chorus-a", {"section": "verse"}, "owner-only"),
    ("PUT", "/canvas", {"sections": [], "clips": []}, "owner-only"),
    ("DELETE", "", None, "owner-only"),
]


@pytest.mark.parametrize("method,suffix,body,access", ROUTE_CASES)
def test_route_identity_matrix(
    audit_memo: tuple[Path, str], method: str, suffix: str, body: dict[str, Any] | None, access: str,
) -> None:
    """Return None after checking anonymous, other-browser and owner access without revealing private IDs."""
    _, token = audit_memo
    path = "/api/memos/demo" + suffix
    encoded = json.dumps(body).encode() if body is not None else b""
    for identity in [None, str(uuid4()), "invalid-identity"]:
        result = request(method, path, encoded, "application/json", owner=identity)
        assert result.status == 404
    assert request(method, path, encoded, "application/json", owner=token).status == 200


@pytest.mark.parametrize("method,suffix,body,access", ROUTE_CASES)
def test_showcase_read_and_write_matrix(
    audit_memo: tuple[Path, str], method: str, suffix: str, body: dict[str, Any] | None, access: str,
) -> None:
    """Return None after allowing public showcase reads and rejecting showcase mutations even by owners."""
    directory, token = audit_memo
    metadata = json.loads((directory / "meta.json").read_text())
    metadata["showcase"] = True
    save_meta(directory, metadata)
    encoded = json.dumps(body).encode() if body is not None else b""
    path = "/api/memos/demo" + suffix
    public = request(method, path, encoded, "application/json", owner=None)
    assert public.status in ({200} if access == "showcase-readable" else {404} if method == "GET" else {403, 404})
    owned = request(method, path, encoded, "application/json", owner=token)
    assert owned.status == (200 if method == "GET" else 403)


@pytest.mark.parametrize("bad_id", ["..", "%2e%2e", "%2foutside", "a%5cb", "C:%5coutside", "x" * 5000])
def test_malformed_ids_and_clip_paths(audit_memo: tuple[Path, str], bad_id: str) -> None:
    """Return None after checking malformed memo IDs and traversal clips cannot serve outside files."""
    _, token = audit_memo
    for suffix in ["", "/status", "/recording", "/canvas", "/export/stems"]:
        assert request("GET", "/api/memos/" + bad_id + suffix, owner=token).status in {400, 404}
    assert request("GET", "/api/memos/demo/clips/" + bad_id + ".wav", owner=token).status in {400, 404}


def test_lists_are_browser_scoped_and_static_storage_is_hidden(audit_memo: tuple[Path, str]) -> None:
    """Return None after checking browser-scoped lists and denying direct storage-folder requests."""
    directory, token = audit_memo
    other = str(uuid4())
    assert [row["id"] for row in request("GET", "/api/memos", owner=token).json()] == ["demo"]
    assert request("GET", "/api/memos", owner=other).json() == []
    metadata = json.loads((directory / "meta.json").read_text())
    metadata["showcase"] = True
    save_meta(directory, metadata)
    assert [row["id"] for row in request("GET", "/api/memos", owner=other).json()] == ["demo"]
    for path in ["/outputs/demo/meta.json", "/uploads/audit.wav", "/data/uploads/audit.wav"]:
        assert request("GET", path).status == 404
