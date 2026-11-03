"""Local-only browser fixture with generated audio and no model or external service calls."""

import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import time
from typing import Iterator

import numpy as np
import pytest
import soundfile as sf
from playwright.sync_api import Page, sync_playwright


@pytest.fixture(autouse=True)
def offline() -> None:
    """Return None; browser tests enforce local-only requests through their page route instead."""


@pytest.fixture()
def page() -> Iterator[Page]:
    """Yield an isolated Chromium page, closing Playwright's loop before in-process ASGI tests run."""
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page()
        try:
            yield page
        finally:
            browser.close()


@pytest.fixture()
def editor_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Yield a loopback app serving a seeded memo with generated PCM audio and cached artifacts."""
    root = Path(__file__).resolve().parents[2]
    folder = tmp_path_factory.mktemp("keeper-editor")
    memo = folder / "outputs" / "fixture-memo"
    uploads = folder / "uploads"
    memo.mkdir(parents=True)
    uploads.mkdir()
    owner = "11111111-1111-4111-8111-111111111111"
    metadata = {"title": "Late night piano session", "created_at": "2026-11-01T20:00:00Z", "original_filename": "fixture.wav", "owner": owner, "showcase": False}
    (memo / "meta.json").write_text(json.dumps(metadata))
    sections = {}
    spans = [("verse",22,38),("chorus",60,82),("chorus",114,120),("chorus",348,372),
             ("bridge",294,304),("unsorted",260,278),("unlabeled",130,150),("unlabeled",161,187),("unlabeled",310,320),
             ("intro",0,4),("pre-chorus",42,45),("hook",190,194),("outro",373,375),("coda",198,200)]
    audio = np.zeros(376*16000, dtype=np.float32)
    peaks = np.random.default_rng(1).uniform(.02,.1,800)
    for section,start,end in spans:
        number = len(sections.get(section,[]))+1
        take_id = f"{section}-{number}"
        samples = (np.sin(np.arange((end-start)*16000)*2*np.pi*220/16000)*.025).astype(np.float32)
        audio[start*16000:end*16000] = samples
        sf.write(memo / f"{take_id}.wav", samples, 16000)
        peaks[int(start/376*800):int(end/376*800)] = np.random.default_rng(start).uniform(.3,.9,int(end/376*800)-int(start/376*800))
        sections.setdefault(section,[]).append({"id":take_id,"section":section,"variant":f"Take {number}","start":start,"end":end,"source_file":"fixture.wav","clip_path":f"{take_id}.wav","note":None,"heard":"That last one, chorus B" if section=="chorus" and number==3 else None})
    sf.write(uploads / "fixture.wav", audio, 16000)
    (memo / "upload.json").write_text(json.dumps({"filename":"fixture.wav"}))
    (memo / "songmap.json").write_text(json.dumps({"source_file":"fixture.wav","sections":sections,"parser":"mock"}))
    (memo / "regions.json").write_text(json.dumps({"regions":[{"start":0,"end":375,"kind":"music"},{"start":375,"end":376,"kind":"speech"}]}))
    (memo / "peaks.json").write_text(json.dumps({"peaks":peaks.tolist()}))
    for memo_id, name, created, showcase in [("second-memo", "Another memo", "2026-10-31T20:00:00Z", False),
                                              ("showcase-memo", "Showcase memo", "2026-10-30T20:00:00Z", True)]:
        destination = memo.parent / memo_id
        shutil.copytree(memo, destination)
        (destination / "meta.json").write_text(json.dumps({**metadata, "title": name, "created_at": created, "showcase": showcase}))
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1",0))
        port = port_socket.getsockname()[1]
    env = {**os.environ,"KEEPER_OUTPUTS_DIR":str(folder / "outputs"),"KEEPER_UPLOADS_DIR":str(uploads)}
    # Model/network construction is disabled in the server subprocess, including future upload tests.
    code = "import uvicorn,app.server as s; s.start_processing=lambda memo_id: None; uvicorn.run(s.app,host='127.0.0.1',port="+str(port)+",log_level='error')"
    process = subprocess.Popen([str(root / ".venv/Scripts/python.exe"),"-c",code],cwd=root,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            try:
                with socket.create_connection(("127.0.0.1",port),timeout=.3):
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError("Local editor server did not start")
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture()
def editor(page: object, editor_url: str) -> object:
    """Return a page restricted to the local server with deterministic ownership and an empty canvas."""
    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(editor_url) else route.abort())
    page.add_init_script("localStorage.setItem('keeper-owner','11111111-1111-4111-8111-111111111111')")
    page.request.put(editor_url+"/api/memos/fixture-memo/canvas",headers={"X-Keeper-Owner":"11111111-1111-4111-8111-111111111111"},data={"sections":[],"clips":[]})
    page.goto(editor_url)
    page.locator(".take-row").first.wait_for()
    return page
