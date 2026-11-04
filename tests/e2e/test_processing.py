"""Scripted observations exercise the live log, replay, and editor reveal without inference."""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page, expect


def scripted_events() -> list[dict[str, Any]]:
    """Return a representative ordered event stream containing the parser's exact heard text."""
    values = [
        ("received", {"duration_s": 376.0, "sample_rate": 44100, "channels": 2}),
        ("loading_done", {"duration_s": 376.0}),
        ("ast_start", {"n_windows": 752, "window_s": 2, "hop_s": .5, "device": "T4 GPU"}),
        *[("ast_progress", {"windows_done": done}) for done in [0, 96, 192, 288, 384, 480, 576, 672, 752]],
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
    allow_done = False

    def status(route: Any) -> None:
        """Return None after advancing the scripted job only when the client polls."""
        from time import monotonic

        polls.append(monotonic())
        done = len(polls) >= 3 and allow_done
        route.fulfill(json={"state": "done" if done else "running", "events": events if done else events[:-3]})

    editor.route("**/api/memos/fixture-memo/status", status)
    # Browser-decodable, genuine short WAV; model results here deliberately come from the script.
    import io
    import numpy as np
    import soundfile as sf

    stream = io.BytesIO()
    sf.write(stream, np.full(16000, .2, dtype=np.float32), 16000, format="WAV")
    editor.locator("#file").set_input_files({"name": "123 Example Street 4.m4a", "mimeType": "audio/mp4", "buffer": stream.getvalue()})
    expect(editor.locator("#memo-meta")).to_have_text(re.compile(r"[A-Z][a-z]{2} \d{1,2} · \d{1,2}:\d{2} [AP]M"))
    expect(editor.locator(".listening-entry[data-event=spark_result] .listening-process").first).to_contain_text('"That last one, chorus B"')
    expect(editor.locator(".listening-voice").first).to_be_visible()
    assert "Example Street" not in editor.content()
    expect(editor.locator(".listening-entry[data-event=received] .listening-process")).to_have_text("input   6:16 · 44100 Hz stereo")
    expect(editor.locator(".listening-entry[data-event=ast_start]")).to_have_count(1)
    expect(editor.locator(".listening-entry[data-event=ast_progress]")).to_have_count(0)
    assert editor.locator(".listening-pin").count() >= 1
    folder = Path("outputs/ui_screens"); folder.mkdir(parents=True, exist_ok=True)
    editor.screenshot(path=str(folder / "processing_mid_log.png"))
    allow_done = True
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
    editor.wait_for_function("document.querySelectorAll('.listening-entry').length===8")
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


def test_ast_row_updates_only_at_real_milestones(editor: Page) -> None:
    """Return None after checking one stable progress row, exact counts, quarter captions and settled metadata."""
    editor.emulate_media(reduced_motion="reduce")
    editor.evaluate("""events => {
      window.polishSession=new KeeperProcessing.Listening('polish',state.data.peaks,true);
      polishSession.feed({state:'running',events:events.slice(0,3)});
      window.polishEvents=events.slice(0,3);
    }""", scripted_events())
    row = editor.locator(".listening-entry[data-event=ast_start]")
    expect(row.locator(".listening-process")).to_have_text("ast     0 / 752 windows")
    initial = row.locator(".listening-voice").inner_text()
    captions = []
    previous_caption = ""
    for done in [0, 96, 192, 288, 384, 480, 576, 672, 752]:
        editor.evaluate("""done => {
          polishEvents.push({t:done,type:'ast_progress',data:{windows_done:done}});
          polishSession.feed({state:'running',events:polishEvents});
        }""", done)
        expect(row).to_have_count(1)
        expect(editor.locator(".listening-entry[data-event=ast_progress]")).to_have_count(0)
        expect(row.locator(".listening-voice")).to_have_text(initial)
        expect(row.get_by_role("progressbar")).to_have_attribute("aria-valuenow", str(done))
        caption = row.locator(".listening-caption").inner_text()
        if done in [192, 384, 576]:
            assert caption and caption != previous_caption
            captions.append(caption)
        else:
            assert caption == previous_caption
        previous_caption = caption
        if done == 96:
            expect(row.locator(".listening-process")).to_have_text("ast     96 / 752 windows")
            folder = Path("outputs/ui_screens"); folder.mkdir(parents=True, exist_ok=True)
            editor.screenshot(path=str(folder / "processing_ast_progress.png"))
        if done < 752:
            expect(row.locator(".listening-process")).to_have_text(f"ast     {done} / 752 windows")
    assert len(set(captions)) == 3
    expect(row.locator(".listening-process")).to_have_text("ast     752 windows · 2 s window · 0.5 s hop · T4 GPU")
    assert editor.evaluate("document.querySelector('.listening-progress-fill').style.transform") == "scaleX(1)"
    editor.evaluate("polishSession.cancel()")


def test_personality_punctuation_repeat_window_and_native_input(editor: Page) -> None:
    """Return None after checking deterministic nonrepeating templates and exact observed heard text."""
    result = editor.evaluate("""() => {
      const events=Array.from({length:60},(_,i)=>({type:i%7===0?'take_cut':'spark_result',
        data:i%7===0?{name:'Verse 1',start:0,end:1}:{heard:'Okay, that is a verse. , ! ?  ',intent:'file',section:'verse',start:0,end:1}}));
      const render=id=>{const voice=KeeperVoice.createPersonality(id);return events.map((event,i)=>voice(event,i));};
      return {events,first:render('memo-a'),repeat:render('memo-a'),other:render('memo-b'),
        exact:KeeperVoice.process(events[1]),
        native:KeeperVoice.process({type:'received',data:{filename:'123 Example Street 4.m4a',duration_s:376,sample_rate:44100,channels:2}}),
        legacy:KeeperVoice.process({type:'received',data:{filename:'123 Example Street 4.m4a',duration_s:376,sample_rate:16000}})};
    }""")
    assert result["first"] == result["repeat"] and result["first"] != result["other"]
    previous: dict[str, str] = {}
    for index, line in enumerate(result["first"]):
        kind = result["events"][index]["type"]
        assert line != previous.get(kind) and line not in result["first"][max(0, index - 3):index]
        previous[kind] = line
        if kind == "spark_result":
            assert '"Okay, that is a verse".' in line
    assert '"Okay, that is a verse. , ! ?  "' in result["exact"]
    assert result["native"] == "input   6:16 · 44100 Hz stereo" and result["legacy"] == "input   6:16"


def test_legacy_replay_does_not_render_stored_filename(editor: Page) -> None:
    """Return None after checking even raw legacy replay events never put uploaded addresses in the DOM."""
    events = scripted_events()
    events[0]["data"] = {"filename": "123 Example Street 4.m4a", "duration_s": 376.0, "sample_rate": 16000}
    mock_status(editor, events)
    editor.evaluate("void KeeperProcessing.replay()")
    expect(editor.locator(".listening-entry[data-event=received] .listening-process")).to_have_text("input   6:16")
    assert "Example Street" not in editor.content()
    editor.keyboard.press("Escape")
    editor.wait_for_function("KeeperProcessing.active===null")
    assert "Example Street" not in editor.content()
