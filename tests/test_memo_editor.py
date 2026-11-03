"""Owner-only take filing and arrangement persistence using in-process API requests."""

import json
from pathlib import Path

from tests.test_server import OWNER_B, api_runtime, memo, request


def test_move_take_stable_id_and_renumber(memo: Path) -> None:
    """Return None after checking section filing persists while clip and canvas identities stay stable."""
    response=request("PATCH","/api/memos/demo/takes/chorus-a",json.dumps({"section":"verse"}).encode(),"application/json")
    assert response.status==200
    take=response.json()["sections"]["verse"][0]
    assert (take["id"],take["variant"],take["section"])==("chorus-a","Take 1","verse")
    assert take["clip_path"]=="clips/chorus a.wav"
    assert json.loads((memo/"songmap.json").read_text())["sections"]["verse"][0]==take
    assert request("PATCH","/api/memos/demo/takes/chorus-a",b'{"section":"chorus"}',"application/json",owner=OWNER_B).status==404


def test_canvas_owner_validation_and_roundtrip(memo: Path) -> None:
    """Return None after checking valid arrangements persist and unknown takes or invalid spans fail."""
    assert request("GET","/api/memos/demo/canvas").json()=={"sections":[],"clips":[]}
    value={"sections":[{"name":"Verse","start":0,"end":16}],"clips":[{"take_id":"chorus-a","start":4,"lane":2}]}
    assert request("PUT","/api/memos/demo/canvas",json.dumps(value).encode(),"application/json").status==200
    assert request("GET","/api/memos/demo/canvas").json()==value
    assert request("GET","/api/memos/demo/canvas",owner=OWNER_B).status==404
    assert request("PUT","/api/memos/demo/canvas",json.dumps(value).encode(),"application/json",owner=OWNER_B).status==404
    value["clips"][0]["take_id"]="missing"
    assert request("PUT","/api/memos/demo/canvas",json.dumps(value).encode(),"application/json").status==422
    value["sections"][0]["end"]=-1
    assert request("PUT","/api/memos/demo/canvas",json.dumps(value).encode(),"application/json").status==422
