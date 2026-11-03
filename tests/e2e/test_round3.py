"""Memo actions, collision-free labels, saved command analysis, and clip peak rendering."""

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect


def open_rename(editor: Page) -> None:
    """Return None after opening inline memo title editing from its actions menu."""
    editor.locator('#memo-more').click()
    editor.get_by_role('menuitem', name='Rename memo...', exact=True).click()
    expect(editor.locator('#rename-input')).to_be_focused()
    expect(editor.locator('#memo-title')).to_be_hidden()


def test_inline_memo_rename(editor: Page) -> None:
    """Return None after checking Enter, Escape, blur, persistence, and the single memo menu."""
    expect(editor.locator('#memo-actions')).to_be_hidden()
    open_rename(editor)
    editor.locator('#rename-input').fill('Draft chorus')
    editor.keyboard.press('Enter')
    expect(editor.locator('#memo-title')).to_have_text('Draft chorus')
    open_rename(editor)
    editor.locator('#rename-input').fill('Cancelled title')
    editor.keyboard.press('Escape')
    expect(editor.locator('#memo-title')).to_have_text('Draft chorus')
    open_rename(editor)
    editor.locator('#rename-input').fill('Piano ideas')
    editor.locator('#memo-meta').click()
    expect(editor.locator('#memo-title')).to_have_text('Piano ideas')
    editor.reload()
    expect(editor.locator('#memo-title')).to_have_text('Piano ideas')
    editor.locator('[data-memo="showcase-memo"]').click()
    expect(editor.locator('#memo-more')).to_be_hidden()


@pytest.mark.parametrize('width', [1024, 1440, 390])
def test_recording_labels_do_not_overlap(editor: Page, width: int) -> None:
    """Return None after checking visible label bounds at fit and zoom after resizing."""
    editor.set_viewport_size({'width': width, 'height': 900})
    editor.evaluate('document.fonts.ready')
    for zoom in (1, 3):
        editor.evaluate('zoom=>{state.recordZoom=zoom;renderRecording()}', zoom)
        boxes = editor.locator('.recording-label').evaluate_all('nodes=>nodes.map(n=>{const b=n.getBoundingClientRect();return {x:b.x,y:b.y,right:b.right,bottom:b.bottom,text:n.textContent}})')
        assert len(boxes) == editor.locator('.recording-band').count()
        for index, a in enumerate(boxes):
            for b in boxes[index+1:]:
                assert a['right'] <= b['x'] or b['right'] <= a['x'] or a['bottom'] <= b['y'] or b['bottom'] <= a['y'], (a, b)
        edge = editor.locator('#timeline').bounding_box()
        assert all(b['right'] <= edge['x']+edge['width']+1 for b in boxes)
    assert all(editor.locator('.recording-band').nth(i).get_attribute('aria-label') for i in range(editor.locator('.recording-band').count()))


def test_saved_command_analysis_and_persistence(editor: Page) -> None:
    """Return None after checking pins, full cards, hollow talking pins, and browser persistence."""
    editor.set_viewport_size({'width': 1440, 'height': 1000})
    editor.locator('.detail-label').click()
    expect(editor.locator('.analysis-region')).to_have_count(4)
    expect(editor.locator('.command-pin')).to_have_count(2)
    expect(editor.locator('.analysis-legend')).to_have_count(3)
    pin = editor.locator('.command-pin').last
    pin.hover()
    expect(editor.locator('#command-card')).to_contain_text('That last one, chorus B')
    editor.mouse.move(5, 5)
    expect(editor.locator('#command-card')).to_be_hidden()
    pin.focus()
    card = editor.locator('#command-card')
    expect(card).to_contain_text('That last one, chorus B')
    for text in ('file', 'chorus', 'previous', '96%', 'Chorus 3'):
        expect(card).to_contain_text(text)
    editor.locator('.command-pin').first.focus()
    expect(card).to_contain_text('Just thinking about the next line')
    expect(editor.locator('.command-pin').first.locator('circle')).to_have_attribute('fill', 'var(--surface)')
    editor.keyboard.press('Escape')
    expect(card).to_be_hidden()
    editor.reload()
    expect(editor.get_by_label('Show analysis', exact=True)).to_be_checked()
    expect(editor.locator('.command-pin')).to_have_count(2)
    folder = Path('outputs/ui_screens')
    folder.mkdir(parents=True, exist_ok=True)
    editor.evaluate('document.fonts.ready')
    editor.screenshot(path=str(folder/'analysis_on.png'))
    editor.evaluate("theme('light')")
    editor.screenshot(path=str(folder/'analysis_on_light.png'))
    editor.route('**/api/memos/second-memo', lambda route: route.fulfill(json={'songmap': {'sections': {}}, 'regions': [], 'peaks': [], 'duration_s': 0, 'meta': {'title': 'Older memo', 'owned': True, 'showcase': False}, 'analysis': {'commands': []}}))
    editor.locator('[data-memo="second-memo"]').click()
    expect(editor.locator('#memo-title')).to_have_text('Older memo')
    expect(editor.locator('.detail-label')).to_be_hidden()


def test_canvas_wave_pixels_and_played_accent(editor: Page) -> None:
    """Return None after checking cached peak pixels, 50 clip canvases, and played accent color."""
    editor.set_viewport_size({'width': 1440, 'height': 1000})
    editor.evaluate("state.canvas.clips=[{take_id:'verse-1',start:0,lane:0},{take_id:'chorus-1',start:20,lane:1},{take_id:'bridge-1',start:45,lane:2}];renderCanvas()")
    editor.wait_for_function("document.querySelectorAll('canvas[data-drawn=true]').length===3")
    assert editor.locator('.clip-waveform').first.evaluate('c=>Array.from(c.getContext("2d").getImageData(0,0,c.width,c.height).data).some((v,i)=>i%4===3&&v>0)')
    expect(editor.locator('.clip-name-chip').first).to_have_text('Verse 1')
    editor.locator('#theme').click()
    editor.wait_for_function("Array.from(document.querySelector('.clip-waveform').getContext('2d').getImageData(0,0,document.querySelector('.clip-waveform').width,document.querySelector('.clip-waveform').height).data).some((v,i,a)=>i%4===0&&v>=24&&v<=28&&a[i+1]>=25&&a[i+1]<=29&&a[i+2]>=27&&a[i+2]<=31&&a[i+3]>0)")
    editor.locator('#theme').click()
    editor.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    folder = Path('outputs/ui_screens')
    folder.mkdir(parents=True, exist_ok=True)
    editor.screenshot(path=str(folder/'canvas_waveforms.png'))
    editor.locator('#play-through').click()
    editor.wait_for_function("player.mode==='playing'")
    editor.locator('#seek').evaluate("n=>{n.value=8;n.dispatchEvent(new Event('input',{bubbles:true}))}")
    editor.wait_for_function("Array.from(document.querySelector('.clip-waveform').getContext('2d').getImageData(0,0,document.querySelector('.clip-waveform').width,document.querySelector('.clip-waveform').height).data).some((v,i,a)=>i%4===0&&v===31&&a[i+1]===183&&a[i+2]===242)")
    editor.locator('#stop').click()
    editor.evaluate("state.canvas.clips=Array.from({length:50},(_,i)=>({take_id:'verse-1',start:i*17,lane:i%3}));renderCanvas()")
    editor.wait_for_function("document.querySelectorAll('canvas[data-drawn=true]').length===50")
