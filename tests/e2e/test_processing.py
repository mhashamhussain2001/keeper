"""Scripted observations exercise the live log, replay, and editor reveal without inference."""

import json
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page, expect


def scripted_events() -> list[dict[str, Any]]:
    """Return a representative ordered event stream containing the parser's exact heard text."""
    values = [
        ("received", {"filename": "ideas.wav", "duration_s": 376.0, "sample_rate": 16000}),
        ("loading_done", {"duration_s": 376.0}),
        ("ast_start", {"n_windows": 752, "window_s": 2, "hop_s": .5, "device": "T4 GPU"}),
        ("ast_progress", {"windows_done": 384}),
        ("regions_found", {"n_singing": 2, "n_speech": 2, "total_singing_s": 372.0,
                           "speech_regions": [{"start": 39, "end": 41}, {"start": 375, "end": 376}]}),
        ("spark_result", {"start": 375.0, "end": 376.0, "heard": "That last one, chorus B", "intent": "file",
                          "section": "chorus", "target": "previous", "scope": "whole", "confidence": .96, "cached": False}),
        ("spark_result", {"start": 39.0, "end": 41.0, "heard": "Just thinking about the next line", "intent": "none",
                          "section": None, "target": "previous", "scope": "whole", "confidence": .91, "cached": True}),
        ("take_cut", {"take_id": "verse-1", "name": "Verse 1", "section": "verse", "start": 22.0, "end": 38.0}),
        ("done", {"n_takes": 14, "n_sections": 11, "elapsed_s": 19.2}),
    ]
    return [{"t": float(i), "type": kind, "data": data} for i, (kind, data) in enumerate(values)]


def mock_status(page: Page, events: list[dict[str, Any]]) -> None:
    """Return None after routing stored events for both replay and scripted upload polling."""
    page.route("**/api/memos/fixture-memo/status", lambda route: route.fulfill(json={"state": "done", "events": events}))


def fingerprint(page: Page) -> dict[str, Any]:
    """Return the editor's rendered rows, take count, waveform, and audio state for equivalence checks."""
    return page.evaluate("""() => ({rows:[...document.querySelectorAll('.take-row')].map(r=>r.outerHTML),
      count:document.getElementById('take-count').textContent, wave:document.getElementById('timeline').innerHTML,
      audio:{mode:player.mode,generation:player.generation}})""")


def test_scripted_upload_poll_log_and_screenshots(editor: Page) -> None:
    """Return None after exercising a real file input, 1s polling, exact observations, and both screenshot frames."""
    editor.set_viewport_size({"width": 1440, "height": 1000})
    events = scripted_events()
    editor.route("**/api/memos", lambda route: route.fulfill(json={"memo_id": "fixture-memo"}, status=201)
                 if route.request.method == "POST" else route.continue_())
    polls: list[float] = []

    def status(route: Any) -> None:
        """Return None after advancing the scripted job only when the client polls."""
        from time import monotonic

        polls.append(monotonic())
        done = len(polls) >= 3
        route.fulfill(json={"state": "done" if done else "running", "events": events if done else events[:6]})

    editor.route("**/api/memos/fixture-memo/status", status)
    # Browser-decodable, genuine short WAV; model results here deliberately come from the script.
    import io
    import numpy as np
    import soundfile as sf

    stream = io.BytesIO()
    sf.write(stream, np.full(16000, .2, dtype=np.float32), 16000, format="WAV")
    editor.locator("#file").set_input_files({"name": "ideas.wav", "mimeType": "audio/wav", "buffer": stream.getvalue()})
    expect(editor.locator("#memo-meta")).to_have_text("ideas.wav")
    expect(editor.locator(".listening-entry[data-event=spark_result]")).to_have_count(1)
    expect(editor.locator(".listening-entry[data-event=spark_result] .listening-process")).to_contain_text('"That last one, chorus B"')
    expect(editor.locator(".listening-voice").first).to_be_visible()
    assert editor.locator(".listening-pin").count() >= 1
    folder = Path("outputs/ui_screens"); folder.mkdir(parents=True, exist_ok=True)
    editor.screenshot(path=str(folder / "processing_mid_log.png"))
    editor.wait_for_function("KeeperProcessing.active?.phase==='chopping'")
    editor.wait_for_function("[...document.querySelectorAll('.chop-clone')].some(n=>n.getAnimations().some(a=>a.currentTime>a.effect.getTiming().delay+100&&a.currentTime<a.effect.getTiming().delay+460))")
    editor.screenshot(path=str(folder / "processing_mid_reveal.png"))
    editor.wait_for_function("KeeperProcessing.active===null")
    expect(editor.locator("#take-count")).to_have_text("14")
    assert all(.85 < b - a < 2.5 for a, b in zip(polls, polls[1:]))
    expect(editor.locator("#timeline .recording-band")).to_have_count(14)
    assert not editor.locator(".chop-clone").count()


@pytest.mark.parametrize("skip", ["Escape", "Space", "click", "reduced", "none"])
def test_replay_repeatability_skip_and_final_dom(editor: Page, skip: str) -> None:
    """Return None after comparing deterministic copy and exact final editor DOM, including audio state."""
    events = scripted_events(); mock_status(editor, events)
    before = fingerprint(editor)
    if skip == "reduced": editor.emulate_media(reduced_motion="reduce")
    editor.evaluate("void KeeperProcessing.replay()")
    editor.wait_for_function("document.querySelectorAll('.listening-entry').length===9")
    first = editor.locator(".listening-voice").all_text_contents()
    if skip in ("Escape", "Space"):
        editor.keyboard.press(skip)
    elif skip == "click":
        editor.locator("h1").click()
    editor.wait_for_function("KeeperProcessing.active===null")
    assert fingerprint(editor) == before
    assert editor.locator(".chop-clone").count() == 0
    assert editor.evaluate("document.getAnimations().length") == 0
    editor.emulate_media(reduced_motion="reduce")
    editor.evaluate("KeeperProcessing.replay()")
    assert editor.locator(".listening-voice").all_text_contents() == first


def test_query_replay_toggle_and_mobile(editor: Page, editor_url: str) -> None:
    """Return None after checking URL/menu replay, persisted process-only mode, mobile bounds, and many-take limits."""
    mock_status(editor, scripted_events())
    editor.goto(editor_url + "/?reveal=1")
    expect(editor.locator("#listening-log")).to_be_visible()
    editor.locator("#process-only").check()
    expect(editor.locator(".listening-voice").first).to_be_hidden()
    expect(editor.locator(".listening-process").first).to_be_visible()
    editor.keyboard.press("Escape")
    editor.wait_for_function("KeeperProcessing.active===null")
    editor.set_viewport_size({"width": 390, "height": 844})
    editor.locator("#memo-more").click()
    editor.get_by_role("menuitem", name="Replay reveal").click()
    expect(editor.locator("#process-only")).to_be_checked()
    folder = Path("outputs/ui_screens")
    editor.wait_for_function("document.querySelectorAll('.listening-entry').length>=4")
    editor.screenshot(path=str(folder / "processing_mobile.png"))
    assert editor.evaluate("document.documentElement.scrollWidth<=innerWidth")
    editor.wait_for_function("KeeperProcessing.active===null")
    expect(editor.locator("#take-count")).to_have_text("14")


def test_large_reveal_rejected_and_playing_audio(editor: Page) -> None:
    """Return None after checking the ten-flight limit, rejected destination, duration, and uninterrupted audio."""
    editor.set_viewport_size({"width": 1440, "height": 1000})
    editor.evaluate("""() => {
      for(let i=0;i<6;i++) state.takes.push({...state.takes[0],id:'extra-'+i,name:'Extra '+i,section:i===5?'rejected':'verse'});
      renderTakes();renderRecording();playRecording();
    }""")
    editor.wait_for_function("player.mode==='playing'")
    generation = editor.evaluate("player.generation")
    result = editor.evaluate("""async events => {
      const session=new KeeperProcessing.Listening(state.memo,state.data.peaks,true);
      let maxClones=0;
      const observer=new MutationObserver(()=>maxClones=Math.max(maxClones,document.querySelectorAll('.chop-clone').length));
      observer.observe(document.body,{childList:true});
      session.feed({state:'done',events});session.flush();
      const started=performance.now();await session.reveal();observer.disconnect();
      return {elapsed:performance.now()-started,maxClones};
    }""", scripted_events())
    assert result["elapsed"] < 4500 and 0 < result["maxClones"] <= 10
    expect(editor.locator('[data-group="rejected"]')).not_to_have_attribute("open", "")
    expect(editor.locator(".take-row")).to_have_count(20)
    expect(editor.locator("#take-count")).to_have_text("19")
    assert editor.evaluate("player.mode") == "playing" and editor.evaluate("player.generation") == generation
    assert editor.locator(".chop-clone").count() == 0


def test_failed_upload_retains_real_failure_log(editor: Page) -> None:
    """Return None after checking a failed job leaves its real error and personality line readable."""
    editor.route("**/api/memos", lambda route: route.fulfill(json={"memo_id": "fixture-memo"}, status=201)
                 if route.request.method == "POST" else route.continue_())
    editor.route("**/api/memos/fixture-memo/status", lambda route: route.fulfill(json={"state": "failed", "error": "Decoder unavailable",
                 "events": [{"t": .1, "type": "failed", "data": {"message": "Decoder unavailable"}}]}))
    editor.locator("#file").set_input_files({"name": "memo.wav", "mimeType": "audio/wav", "buffer": b"scripted-upload"})
    expect(editor.locator("#status")).to_have_text("Decoder unavailable")
    expect(editor.locator(".listening-process")).to_have_text("failed  Decoder unavailable")
    expect(editor.locator(".listening-voice")).to_be_visible()
    expect(editor.locator("#upload")).to_be_enabled()
