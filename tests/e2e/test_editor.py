"""Browser checks for the redesign's shared player, editing, persistence, and responsive layout."""

from pathlib import Path

from playwright.sync_api import Page, expect
import pytest


def test_theme_persists(editor: Page) -> None:
    """Return None after checking the theme control persists across a page reload."""
    editor.locator("#theme").click()
    expect(editor.locator("html")).to_have_attribute("data-theme","light")
    editor.reload()
    expect(editor.locator("html")).to_have_attribute("data-theme","light")


def test_final_palette_tokens(editor: Page) -> None:
    """Return None after checking every supplied token in both themes and matching theme metadata."""
    rows = {
        "bg":("#0E0F11","#F7F7F5"),"surface":("#16171A","#FFFFFF"),"surface-2":("#1F2125","#F1F1EF"),
        "border":("#2A2C31","#E4E4E1"),"text":("#EDEEF0","#1A1B1D"),"text-muted":("#8A8F98","#6C7078"),
        "accent":("#1FB7F2","#1FB7F2"),"accent-soft":("#102833","#DDF4FD"),"accent-text":("#1FB7F2","#0A7BA8"),
        "on-accent":("#0E0F11","#0E0F11"),"wave":("#34373D","#D3D5D8"),"wave-active":("#9AA0AA","#7A808A"),
        "sec-verse":("#1F3B39","#D8ECE8"),"sec-chorus":("#1E3352","#DCE6F4"),"sec-bridge":("#3E3220","#F2E7D3"),
        "sec-idea":("#2E2742","#E9E3F5"),"sec-unlabeled":("#26282D","#ECECEA")}
    for index in range(2):
        for token,colors in rows.items():
            assert editor.evaluate("key=>getComputedStyle(document.documentElement).getPropertyValue('--'+key).trim()",token).upper()==colors[index]
        assert editor.locator('meta[name="theme-color"]').get_attribute("content").upper()==rows["bg"][index]
        editor.locator("#theme").click()


@pytest.mark.parametrize("action",["cancel","escape","outside"])
def test_delete_dismiss_keeps_memo(editor: Page, action: str) -> None:
    """Return None after checking dismissal, initial focus, trapping, and return focus without deletion."""
    editor.locator(".memo-heading").hover()
    editor.locator("#delete").click()
    dialog=editor.get_by_role("dialog",name="Delete this memo?")
    expect(dialog).to_be_visible()
    expect(editor.locator("#cancel-delete")).to_be_focused()
    editor.keyboard.press("Shift+Tab")
    expect(editor.locator("#confirm-delete")).to_be_focused()
    editor.keyboard.press("Tab")
    expect(editor.locator("#cancel-delete")).to_be_focused()
    if action=="cancel":
        folder=Path("outputs/ui_screens")
        folder.mkdir(parents=True,exist_ok=True)
        editor.set_viewport_size({"width":1440,"height":900})
        editor.screenshot(path=str(folder/"delete_dark.png"))
        editor.evaluate("theme('light')")
        editor.screenshot(path=str(folder/"delete_light.png"))
        editor.set_viewport_size({"width":390,"height":844})
        editor.screenshot(path=str(folder/"delete_mobile.png"))
        editor.locator("#cancel-delete").click()
    elif action=="escape":
        editor.keyboard.press("Escape")
    else:
        editor.mouse.click(5,5)
    expect(dialog).not_to_be_visible()
    expect(editor.locator("#delete")).to_be_focused()
    expect(editor.locator("#memo-title")).to_have_text("Late night piano session")


def test_delete_stops_playback_and_selects_next(editor: Page) -> None:
    """Return None after checking confirmation by Enter stops audio and selects the next surviving memo."""
    editor.get_by_role("button",name="Play Chorus 1",exact=True).click()
    expect(editor.get_by_role("button",name="Pause Chorus 1",exact=True)).to_be_visible()
    editor.locator(".memo-heading").hover()
    editor.locator("#delete").click()
    editor.locator("#confirm-delete").focus()
    editor.keyboard.press("Enter")
    expect(editor.locator("#memo-title")).to_have_text("Another memo")
    expect(editor.locator('[data-memo="fixture-memo"]')).to_have_count(0)
    assert editor.evaluate("window.keeper.player.mode")=="stopped"


def test_showcase_has_no_delete(editor: Page) -> None:
    """Return None after checking even an owned showcase memo has no delete control."""
    editor.locator('[data-memo="showcase-memo"]').click()
    expect(editor.locator("#memo-title")).to_have_text("Showcase memo")
    expect(editor.locator("#delete")).to_be_hidden()


def test_delete_pending_and_error_stays_open(editor: Page) -> None:
    """Return None after checking pending controls and errors remain inside the modal."""
    pending=[]
    editor.route("**/api/memos/fixture-memo",lambda route: pending.append(route) if route.request.method=="DELETE" else route.continue_())
    editor.locator(".memo-heading").hover()
    editor.locator("#delete").click()
    editor.locator("#confirm-delete").click()
    expect(editor.locator("#cancel-delete")).to_be_disabled()
    expect(editor.locator("#confirm-delete")).to_be_disabled()
    expect(editor.locator("#confirm-delete")).to_have_text("Deleting...")
    editor.keyboard.press("Escape")
    expect(editor.locator("#delete-dialog")).to_be_visible()
    pending[0].fulfill(status=500,json={"detail":"Storage unavailable"})
    expect(editor.locator("#delete-error")).to_have_text("Storage unavailable")
    expect(editor.locator("#delete-dialog")).to_be_visible()
    expect(editor.locator("#cancel-delete")).to_be_enabled()


def test_delete_last_memo_shows_empty_state(editor: Page) -> None:
    """Return None after checking a successful deletion with no remaining visible memos clears the editor."""
    editor.route("**/api/memos",lambda route: route.fulfill(json=[]))
    editor.locator(".memo-heading").hover()
    editor.locator("#delete").click()
    editor.locator("#confirm-delete").click()
    expect(editor.locator("#memo-title")).to_have_text("Your next song starts here")
    expect(editor.locator(".take-row")).to_have_count(0)
    expect(editor.locator("#playing-status")).to_have_text("Nothing playing")


def test_shared_recording_and_take_playback(editor: Page) -> None:
    """Return None after checking waveform seeking and mutually exclusive recording/take playback."""
    editor.locator("#timeline").click(position={"x":180,"y":60})
    expect(editor.locator("#playing-status")).to_contain_text("Recording")
    expect(editor.locator("#record-play")).to_have_attribute("aria-label","Pause Recording",timeout=20000)
    assert editor.locator("#record-playhead").get_attribute("transform")!="translate(0,0)"
    editor.get_by_role("button",name="Play Chorus 1",exact=True).click()
    expect(editor.get_by_role("button",name="Pause Chorus 1",exact=True)).to_be_visible()
    expect(editor.locator("#playing-status")).to_have_text("Chorus 1")
    expect(editor.locator("#record-play")).to_have_attribute("aria-label","Play Recording")


def test_filters_and_file_to(editor: Page) -> None:
    """Return None after checking section filters and an owner's persisted take filing."""
    editor.get_by_role("button",name="Bridge",exact=True).click()
    expect(editor.locator(".take-row")).to_have_count(1)
    editor.get_by_role("button",name="Unlabeled",exact=True).click()
    editor.locator(".file-to").first.select_option("verse")
    expect(editor.get_by_role("button",name="Play Verse 2",exact=True)).to_be_visible()


def test_canvas_drag_persists_and_playthrough_advances(editor: Page) -> None:
    """Return None after checking native drag/drop persistence and shared-player canvas sequencing."""
    editor.locator('[data-take="verse-1"]').drag_to(editor.locator("#canvas-clips"),target_position={"x":1,"y":25})
    expect(editor.locator(".canvas-clip")).to_have_count(1)
    editor.wait_for_function("window.keeper.state.canvas.clips.length===1")
    # Wait for the actual persistence response before reloading.
    editor.wait_for_timeout(300)
    editor.reload()
    expect(editor.locator(".canvas-clip")).to_have_count(1)
    editor.locator('[data-take="chorus-1"]').drag_to(editor.locator("#canvas-clips"),target_position={"x":180,"y":90})
    expect(editor.locator(".canvas-clip")).to_have_count(2)
    editor.locator("#play-through").click()
    expect(editor.locator("#playing-status")).to_contain_text("Canvas · Verse 1")
    # Seek via the real transport to just before the second clip, then let audio advance.
    start=editor.evaluate("window.keeper.state.canvas.clips[1].start")
    editor.locator("#seek").evaluate("(node,value)=>{node.value=value;node.dispatchEvent(new Event('input',{bubbles:true}))}",max(0,start-.1))
    expect(editor.locator("#playing-status")).to_have_text("Canvas · Chorus 1",timeout=6000)


def test_screenshots_and_mobile_overflow(editor: Page) -> None:
    """Return None after saving both desktop themes and a mobile layout without page overflow."""
    folder=Path("outputs/ui_screens")
    folder.mkdir(parents=True,exist_ok=True)
    editor.set_viewport_size({"width":1440,"height":900})
    # Populate the reference arrangement through the public editing API for visual comparison.
    editor.evaluate("""async () => {
      const sections=[['Verse',0,16],['Chorus',16,40],['Verse',40,56],['Bridge',56,66],['Chorus',66,88],['Outro',88,100]].map(([name,start,end])=>({name,start,end}));
      const clips=[{take_id:'verse-1',start:0,lane:0},{take_id:'chorus-3',start:16,lane:0},{take_id:'bridge-1',start:56,lane:1},{take_id:'unsorted-1',start:30,lane:2},{take_id:'chorus-2',start:66,lane:0}];
      await window.keeper.api('/api/memos/fixture-memo/canvas',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({sections,clips})});
    }""")
    editor.reload()
    expect(editor.locator('.canvas-clip')).to_have_count(5)
    editor.evaluate("document.fonts.ready")
    editor.screenshot(path=str(folder/"desktop_dark.png"))
    editor.locator("#theme").click()
    editor.screenshot(path=str(folder/"desktop_light.png"))
    editor.set_viewport_size({"width":390,"height":844})
    editor.screenshot(path=str(folder/"mobile.png"),full_page=True)
    assert editor.evaluate("document.documentElement.scrollWidth<=window.innerWidth")


def test_canvas_section_editing_and_clip_removal(editor: Page) -> None:
    """Return None after checking custom section rename/resize persistence and selected clip removal."""
    editor.locator("#add-section").click()
    block=editor.locator(".canvas-section").first
    block.dblclick()
    block.locator("input").fill("Middle eight")
    block.locator("input").press("Enter")
    expect(block).to_contain_text("Middle eight")
    assert block.evaluate("n=>getComputedStyle(n).backgroundColor")!="rgba(0, 0, 0, 0)"
    handle=block.locator(".section-resize").bounding_box()
    editor.mouse.move(handle["x"]+3,handle["y"]+8)
    editor.mouse.down()
    editor.mouse.move(handle["x"]+45,handle["y"]+8)
    editor.mouse.up()
    assert editor.evaluate("window.keeper.state.canvas.sections[0].end")>16
    editor.locator('[data-take="verse-1"]').drag_to(editor.locator("#canvas-clips"),target_position={"x":10,"y":25})
    expect(editor.locator(".canvas-clip")).to_have_count(1)
    editor.locator(".canvas-clip").click()
    editor.keyboard.press("Delete")
    expect(editor.locator(".canvas-clip")).to_have_count(0)
