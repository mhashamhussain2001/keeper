"""Exercise Chromium audio rendering, native-quality downloads, and mixed Canvas playback."""

import io
import json
import wave
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import numpy as np
import pytest
import soundfile as sf
from playwright.sync_api import Page, expect


def seed_native_source(editor: Page) -> list[str]:
    """Return recording fetch URLs after routing a stereo 44.1 kHz original and seeding two half-second clips."""
    stream = io.BytesIO()
    samples = np.column_stack((np.full(44100, .2, dtype=np.float32), np.full(44100, .1, dtype=np.float32)))
    sf.write(stream, samples, 44100, format="WAV", subtype="PCM_16")
    requests: list[str] = []

    def recording(route: Any) -> None:
        """Return None after fulfilling the original audio request with native format headers."""
        requests.append(route.request.url)
        route.fulfill(body=stream.getvalue(), headers={"Content-Type": "audio/wav", "X-Audio-Sample-Rate": "44100", "X-Audio-Channels": "2"})

    editor.route("**/api/memos/fixture-memo/recording", recording)
    editor.evaluate("""() => {
      player.clear();
      for (const take of state.takes.filter(t=>['verse-1','chorus-1'].includes(t.id))) {take.start=0;take.end=.5;}
      state.canvas.clips=[{take_id:'verse-1',start:0,lane:0},{take_id:'chorus-1',start:.25,lane:1}];renderCanvas();
    }""")
    return requests


def wav_download(editor: Page, label: str) -> tuple[bytes, str]:
    """Return WAV bytes and suggested filename downloaded through the visible Export menu."""
    editor.locator("#export").click()
    with editor.expect_download() as downloaded:
        editor.get_by_role("menuitem", name=label, exact=True).click()
    download = downloaded.value
    return Path(download.path()).read_bytes(), download.suggested_filename


def test_menu_empty_keyboard_and_mobile(editor: Page) -> None:
    """Return None after checking empty-state explanations and shared keyboard behavior in desktop and phone menus."""
    editor.locator("#export").focus()
    editor.keyboard.press("Space")
    arrangement = editor.get_by_role("menuitem", name="Arrangement (WAV)", exact=True)
    expect(arrangement).to_have_attribute("aria-disabled", "true")
    expect(arrangement).to_have_attribute("title", "Add takes to the canvas first.")
    editor.keyboard.press("ArrowDown")
    expect(editor.get_by_role("menuitem", name="All takes (ZIP)", exact=True)).to_be_focused()
    editor.keyboard.press("Escape")
    expect(editor.locator("#export")).to_be_focused()
    editor.set_viewport_size({"width": 390, "height": 844})
    editor.locator("#transport-export").click()
    expect(editor.get_by_role("menu")).to_be_visible()
    box = editor.get_by_role("menu").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    editor.keyboard.press("Escape")
    editor.evaluate("state.takes=[];renderTakes()")
    editor.locator("#transport-export").click()
    expect(editor.get_by_role("menuitem", name="All takes (ZIP)", exact=True)).to_have_attribute("aria-disabled", "true")


def test_arrangement_native_wav_duration_and_overlap_sum(editor: Page) -> None:
    """Return None after checking native stereo WAV headers, duration, fades and true summing in overlap samples."""
    requests = seed_native_source(editor)
    content, name = wav_download(editor, "Arrangement (WAV)")
    assert name == "Late night piano session - arrangement.wav"
    with wave.open(io.BytesIO(content)) as wav:
        assert wav.getframerate() == 44100 and wav.getnchannels() == 2 and wav.getsampwidth() == 2
        assert abs(wav.getnframes()/wav.getframerate() - 1.25) < .05
    samples, rate = sf.read(io.BytesIO(content), dtype="float32", always_2d=True)
    assert samples[int(.1*rate), 0] == pytest.approx(.2, abs=2/32768)
    assert samples[int(.3*rate), 0] == pytest.approx(.4, abs=3/32768)
    assert samples[int(.3*rate), 1] == pytest.approx(.2, abs=3/32768)
    assert np.all(samples[0] == 0) and np.max(np.abs(samples[int(.76*rate):])) == 0
    assert editor.evaluate("keeper.lastExport.fallback") is False
    assert len(requests) == 1


def test_playthrough_sums_overlaps_and_reuses_native_decode(editor: Page) -> None:
    """Return None after checking every Canvas source plays in parallel and playback/export fetch and decode once."""
    requests = seed_native_source(editor)
    editor.evaluate("""() => {
      window.scheduled=[];window.decoded=0;
      const schedule=KeeperAudio.scheduleClip;
      KeeperAudio.scheduleClip=(context,buffer,options)=>{scheduled.push({...options,rate:buffer.sampleRate});return schedule(context,buffer,options)};
      const decode=OfflineAudioContext.prototype.decodeAudioData;
      OfflineAudioContext.prototype.decodeAudioData=function(...args){decoded++;return decode.apply(this,args)};
    }""")
    editor.locator("#play-through").click()
    editor.wait_for_function("player.mode==='playing'")
    schedule = editor.evaluate("scheduled")
    assert len(schedule) == 2 and schedule[1]["when"] - schedule[0]["when"] == pytest.approx(.25)
    assert all(clip["duration"] == .5 and clip["rate"] == 44100 for clip in schedule)
    editor.locator("#stop").click()
    wav_download(editor, "Arrangement (WAV)")
    assert len(requests) == 1 and editor.evaluate("decoded") == 1


def test_single_take_native_wav_and_stems_download(editor: Page) -> None:
    """Return None after checking selected-take WAV cuts and real server ZIP entries and heard text."""
    seed_native_source(editor)
    editor.evaluate("state.selected=0;renderCanvas()")
    content, name = wav_download(editor, "This take (WAV)")
    assert name == "Verse 1.wav"
    with wave.open(io.BytesIO(content)) as wav:
        assert wav.getframerate() == 44100 and wav.getnchannels() == 2
        assert wav.getnframes() == 22050
    editor.locator("#export").click()
    with editor.expect_download() as downloaded:
        editor.get_by_role("menuitem", name="All takes (ZIP)", exact=True).click()
    assert downloaded.value.suggested_filename.endswith(" - stems.zip")
    with ZipFile(downloaded.value.path()) as archive:
        assert "song_canvas.txt" in archive.namelist()
        assert "Make" not in archive.read("song_canvas.txt").decode("utf-8")
        assert "That last one, chorus B" in archive.read("song_canvas.txt").decode("utf-8")
        assert len([name for name in archive.namelist() if name.endswith(".wav")]) == 14


def test_decode_failure_uses_explicit_preview_fallback(editor: Page) -> None:
    """Return None after checking original decode errors produce a usable preview WAV and explanatory toast."""
    editor.evaluate("state.canvas.clips=[{take_id:'verse-1',start:0,lane:0}];renderCanvas()")
    editor.route("**/api/memos/fixture-memo/recording", lambda route: route.fulfill(body=b"invalid", headers={"X-Audio-Sample-Rate": "44100"}))
    content, _ = wav_download(editor, "Arrangement (WAV)")
    with wave.open(io.BytesIO(content)) as wav:
        assert wav.getframerate() == 16000 and wav.getnchannels() == 1
    expect(editor.locator("#move-toast")).to_contain_text("16 kHz preview")
    assert editor.evaluate("keeper.lastExport.fallback") is True
    expect(editor.locator("#export")).to_be_enabled()


def test_export_confirm_and_worker_fallback(editor: Page) -> None:
    """Return None after checking long-arrangement cancellation and working main-thread WAV encoding."""
    requests = seed_native_source(editor)
    editor.evaluate("state.canvas.clips[1].start=601;renderCanvas()")
    editor.once("dialog", lambda dialog: dialog.dismiss())
    editor.locator("#export").click()
    editor.get_by_role("menuitem", name="Arrangement (WAV)", exact=True).click()
    assert requests == []
    editor.evaluate("state.canvas.clips[1].start=.25;window.Worker=undefined;renderCanvas()")
    content, _ = wav_download(editor, "Arrangement (WAV)")
    assert content[:4] == b"RIFF"


def test_memory_failure_fallback_and_total_failure_leave_editor_usable(editor: Page) -> None:
    """Return None after checking encoding allocation failures retry previews and final failures release controls."""
    seed_native_source(editor)
    editor.evaluate("""() => {
      const write=KeeperAudio.writeWav;
      KeeperAudio.writeWav=buffer=>{if(buffer.sampleRate!==16000)throw Error('Out of memory');return write(buffer)};
    }""")
    content, _ = wav_download(editor, "Arrangement (WAV)")
    with wave.open(io.BytesIO(content)) as wav:
        assert wav.getframerate() == 16000
    expect(editor.locator("#move-toast")).to_contain_text("Out of memory")
    editor.evaluate("() => {KeeperAudio.renderMix=()=>{throw Error('No render memory')}}")
    editor.locator("#export").click()
    editor.get_by_role("menuitem", name="Arrangement (WAV)", exact=True).click()
    expect(editor.locator("#move-toast")).to_contain_text("Export failed: No render memory")
    expect(editor.locator("#export")).to_be_enabled()
    editor.get_by_role("button", name="Chorus", exact=True).click()
    expect(editor.get_by_role("button", name="Play Chorus 2", exact=True)).to_be_visible()


def test_two_minute_render_benchmark_and_export_screenshots(editor: Page) -> None:
    """Return None after recording native stereo two-minute render metrics and desktop/mobile export menu screenshots."""
    seed_native_source(editor)
    editor.evaluate("state.canvas.clips=Array.from({length:239},(_,i)=>({take_id:i%2?'verse-1':'chorus-1',start:i*.5,lane:i%2}));renderCanvas()")
    content, _ = wav_download(editor, "Arrangement (WAV)")
    metrics = editor.evaluate("keeper.lastExport")
    assert metrics["duration"] == 120 and metrics["sampleRate"] == 44100 and metrics["channels"] == 2
    assert metrics["bytes"] == len(content) == 21168044
    folder = Path("outputs/ui_screens")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "export_benchmark.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    editor.evaluate("""() => {
      for(const take of state.takes){const saved=Object.values(state.data.songmap.sections).flat().find(row=>row.id===take.id);take.start=saved.start;take.end=saved.end;}
      state.canvas.clips=[{take_id:'verse-1',start:0,lane:0},{take_id:'chorus-1',start:12,lane:1}];
      state.selected=0;toast.hidden=true;renderRecording();renderCanvas();
    }""")
    for theme in ("dark", "light"):
        editor.set_viewport_size({"width": 1440, "height": 900})
        editor.evaluate("name=>theme(name)", theme)
        editor.locator("#export").click()
        editor.screenshot(path=str(folder / f"export_menu_{theme}.png"))
        editor.keyboard.press("Escape")
    editor.set_viewport_size({"width": 390, "height": 844})
    editor.evaluate("theme('dark')")
    editor.locator("#transport-export").click()
    editor.screenshot(path=str(folder / "export_menu_mobile.png"))
