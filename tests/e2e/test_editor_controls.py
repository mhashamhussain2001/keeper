"""Interaction regressions for shared icons, full-height grid, zoom, menus, and filing."""

from pathlib import Path

from playwright.sync_api import Page, expect


def test_grid_full_height_and_zoom_alignment(editor: Page) -> None:
    """Return None after checking one grid fills empty and multi-lane canvas bodies at multiple zooms."""
    for zoom in (1,3):
        editor.evaluate("zoom=>{state.canvasZoom=zoom;state.canvas.clips=[];renderCanvas();}",zoom)
        for clips in ([],[{"take_id":"verse-1","start":0,"lane":0},{"take_id":"chorus-1","start":12,"lane":2},{"take_id":"bridge-1","start":40,"lane":5}]):
            editor.evaluate("clips=>{state.canvas.clips=clips;renderCanvas();document.getElementById('canvas-scroll').scrollLeft=120}",clips)
            assert editor.evaluate("Math.abs(document.getElementById('canvas-grid').getBoundingClientRect().bottom-document.getElementById('canvas-board').getBoundingClientRect().bottom)<1")
            assert editor.evaluate("Math.abs(parseFloat(getComputedStyle(document.getElementById('canvas-board')).getPropertyValue('--major-step'))-canvasScale()*15)<.1")


def test_svg_icons_empty_state_and_font(editor: Page) -> None:
    """Return None after checking icon geometry, shared targets, disabled controls, and sentence typography."""
    for selector in ("#loop svg","#prev svg","#next svg","#stop svg","#upload svg","#theme svg"):
        box=editor.locator(selector).bounding_box()
        assert box["width"]>0 and box["height"]>0
    expect(editor.locator("#prev")).to_be_disabled()
    expect(editor.locator("#next")).to_be_disabled()
    for width in (1440,390):
        editor.set_viewport_size({"width":width,"height":900 if width==1440 else 844})
        for theme_name in ("dark","light"):
            editor.evaluate("name=>theme(name)",theme_name)
            boxes=[editor.locator(selector).bounding_box() for selector in ("#loop","#prev","#next","#transport-export")]
            assert max(box["y"]+box["height"]/2 for box in boxes)-min(box["y"]+box["height"]/2 for box in boxes)<1
            assert all(box["width"]==36 and box["height"]==36 for box in boxes)
    editor.evaluate("emptyMemo()")
    for selector in ("#record-play","#transport-play","#play-through","[data-zoom]"):
        for node in editor.locator(selector).all():
            expect(node).to_be_disabled()
            expect(node).to_have_attribute("aria-disabled","true")
    assert editor.locator("#memo-meta").evaluate("n=>getComputedStyle(n).fontFamily").startswith("Figtree")
    assert editor.locator("#memo-meta").evaluate("n=>getComputedStyle(n).fontSize")=="15px"


def test_ctrl_wheel_anchor_and_custom_scrollbars(editor: Page) -> None:
    """Return None after checking cursor time stays anchored without page zoom/scroll and scroll rails work."""
    for kind,node_id in (("record","record-scroll"),("canvas","canvas-scroll")):
        before=editor.evaluate("kind=>{const n=document.getElementById(kind==='record'?'record-scroll':'canvas-scroll');return{zoom:state[kind+'Zoom'],x:(n.scrollLeft+120)/state[kind+'Zoom'],y:scrollY,page:visualViewport.scale}}",kind)
        editor.locator('#'+node_id).evaluate("n=>n.dispatchEvent(new WheelEvent('wheel',{ctrlKey:true,deltaY:-60,clientX:n.getBoundingClientRect().left+120,bubbles:true,cancelable:true}))")
        after=editor.evaluate("kind=>{const n=document.getElementById(kind==='record'?'record-scroll':'canvas-scroll');return{zoom:state[kind+'Zoom'],x:(n.scrollLeft+120)/state[kind+'Zoom'],y:scrollY,page:visualViewport.scale}}",kind)
        assert after["zoom"]>before["zoom"] and abs(after["x"]-before["x"])<1
        assert after["y"]==before["y"] and after["page"]==before["page"]
    rail=editor.get_by_role("scrollbar",name="Scroll canvas",exact=True)
    rail.focus()
    editor.keyboard.press("End")
    assert float(rail.get_attribute("aria-valuenow"))>0


def test_sidebar_persistence_and_all_section_groups(editor: Page) -> None:
    """Return None after checking data-driven sections, collapsible rail persistence, and mobile behavior."""
    for name in ("Intro","Verse","Pre-chorus","Chorus","Bridge","Hook","Outro","Ideas","Takes","Coda"):
        expect(editor.get_by_role("button",name=name,exact=True)).to_be_visible()
        expect(editor.locator(".group-title",has_text=name).first).to_be_attached()
    editor.locator("#sidebar-toggle").click()
    expect(editor.locator("html")).to_have_class("sidebar-collapsed")
    editor.reload()
    expect(editor.locator("html")).to_have_class("sidebar-collapsed")
    editor.set_viewport_size({"width":390,"height":844})
    expect(editor.locator("#sidebar-toggle")).to_be_hidden()
    assert editor.evaluate("document.documentElement.scrollWidth<=innerWidth")


def test_section_menu_keyboard_retype_and_tooltip_dismissal(editor: Page) -> None:
    """Return None after checking section menus and one tooltip cannot remain after hover or scroll."""
    editor.set_viewport_size({"width":1440,"height":900})
    editor.locator("#add-section").click()
    editor.get_by_role("menuitem",name="Intro",exact=True).click()
    block=editor.locator(".canvas-section").first
    block.focus()
    editor.keyboard.press("Enter")
    editor.get_by_role("menuitem",name="Chorus",exact=True).click()
    expect(block).to_contain_text("Chorus")
    assert editor.evaluate("state.canvas.sections[0].type")=="chorus"
    editor.get_by_role("button",name="Chorus",exact=True).click()
    row=editor.locator('[data-take="chorus-3"]')
    row.hover()
    expect(editor.locator("#take-tooltip")).to_be_visible()
    editor.mouse.move(5,5)
    expect(editor.locator("#take-tooltip")).to_be_hidden()
    row.hover()
    expect(editor.locator("#take-tooltip")).to_be_visible()
    editor.locator("#take-list").evaluate("n=>{n.scrollTop+=30;n.dispatchEvent(new Event('scroll'))}")
    expect(editor.locator("#take-tooltip")).to_be_hidden()


def test_move_rename_undo_and_playback_preserved(editor: Page) -> None:
    """Return None after checking names survive filing, active playback survives, and Undo restores section."""
    editor.get_by_role("button",name="Actions for Chorus 1",exact=True).click()
    editor.get_by_role("menuitem",name="Rename take...",exact=True).click()
    editor.locator("#name-dialog-input").fill("Quiet refrain")
    editor.get_by_role("button",name="Save",exact=True).click()
    expect(editor.get_by_role("button",name="Play Quiet refrain",exact=True)).to_be_visible()
    editor.get_by_role("button",name="Play Quiet refrain",exact=True).click()
    expect(editor.get_by_role("button",name="Pause Quiet refrain",exact=True)).to_be_visible()
    editor.get_by_role("button",name="Actions for Quiet refrain",exact=True).click()
    editor.get_by_role("menuitem",name="Move to Verse",exact=True).click()
    expect(editor.locator('[data-take="chorus-1"]')).to_have_attribute("data-section","verse")
    expect(editor.get_by_role("button",name="Pause Quiet refrain",exact=True)).to_be_visible()
    editor.get_by_role("button",name="Undo",exact=True).click()
    expect(editor.locator('[data-take="chorus-1"]')).to_have_attribute("data-section","chorus")


def test_drag_between_groups_and_showcase_readonly(editor: Page) -> None:
    """Return None after checking native grip drops re-file and owned showcases hide take editing."""
    editor.set_viewport_size({"width":1440,"height":1400})
    editor.locator('[data-take="unlabeled-1"] .handle').drag_to(editor.locator('[data-group="chorus"] .group-title'))
    expect(editor.locator('[data-take="unlabeled-1"]')).to_have_attribute("data-section","chorus")
    editor.locator('[data-memo="showcase-memo"]').click()
    expect(editor.locator("#memo-title")).to_have_text("Showcase memo")
    expect(editor.locator(".take-more")).to_have_count(0)
    assert editor.locator(".take-row").first.get_attribute("draggable")=="false"


def test_canvas_playhead_and_context_navigation(editor: Page) -> None:
    """Return None after checking canvas playhead movement and next/previous honor the active context."""
    editor.evaluate("state.canvas.clips=[{take_id:'verse-1',start:0,lane:0},{take_id:'chorus-1',start:20,lane:1}];renderCanvas()")
    editor.locator("#canvas-ruler").click(position={"x":3,"y":10})
    expect(editor.locator("#playing-status")).to_contain_text("Canvas")
    expect(editor.locator("#canvas-playhead")).to_be_visible()
    editor.wait_for_function("player.mode==='playing'")
    first=editor.locator("#canvas-playhead").evaluate("n=>n.getBoundingClientRect().left")
    editor.wait_for_timeout(200)
    assert editor.locator("#canvas-playhead").evaluate("n=>n.getBoundingClientRect().left")>first
    editor.locator("#next").click()
    expect(editor.locator("#playing-status")).to_have_text("Canvas · Chorus 1")
    editor.get_by_role("button",name="Chorus",exact=True).click()
    editor.get_by_role("button",name="Play Chorus 2",exact=True).click()
    editor.locator("#next").click()
    expect(editor.locator("#playing-status")).to_have_text("Chorus 3")
    editor.locator("#prev").click()
    expect(editor.locator("#playing-status")).to_have_text("Chorus 2")
    editor.locator("#record-play").click()
    editor.wait_for_function("player.mode==='playing'&&player.current.context==='Recording'")
    editor.locator("#next").click()
    assert editor.evaluate("player.position")>=10


def test_extra_screenshots(editor: Page) -> None:
    """Return None after saving the sidebar rail and keyboard-accessible section menu."""
    folder=Path("outputs/ui_screens")
    editor.set_viewport_size({"width":1440,"height":900})
    editor.locator("#sidebar-toggle").click()
    editor.wait_for_timeout(180)
    editor.screenshot(path=str(folder/"sidebar_collapsed.png"))
    editor.locator("#add-section").click()
    editor.get_by_role("menuitem",name="Verse",exact=True).click()
    editor.locator(".canvas-section").first.focus()
    editor.keyboard.press("Enter")
    expect(editor.get_by_role("menu")).to_be_visible()
    menu=editor.get_by_role("menu").bounding_box()
    assert menu["y"]>=8 and menu["y"]+menu["height"]<=900
    editor.screenshot(path=str(folder/"section_menu.png"))
