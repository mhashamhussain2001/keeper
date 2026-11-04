"""Verify upload policy, queue visibility and long-memo export safeguards in the browser."""

from playwright.sync_api import Page, expect


def test_visible_policy_and_mobile_bounds(editor: Page) -> None:
    """Return None after checking configured upload/retention copy and readable phone-width layout."""
    expect(editor.locator("#upload-limits")).to_have_text("Up to 30 minutes and 150 MB.")
    expect(editor.locator("#browser-privacy")).to_have_text("Private to this browser. Deleted after 14 days.")
    editor.set_viewport_size({"width": 390, "height": 844})
    expect(editor.locator("#upload-limits")).to_be_visible()
    expect(editor.locator("#browser-privacy")).to_be_visible()
    editor.wait_for_function("document.documentElement.scrollWidth<=innerWidth")


def test_kill_switch_disables_every_upload_path(editor: Page, editor_url: str) -> None:
    """Return None after checking a paused configuration disables the control and blocks file-input admission."""
    editor.route("**/api/config", lambda route: route.fulfill(json={"uploads_enabled": False,
        "max_audio_minutes": 30, "max_upload_mb": 150, "retention_days": 14}))
    uploads = []
    editor.on("request", lambda request: uploads.append(True) if request.method == "POST" and request.url.endswith("/api/memos") else None)
    editor.goto(editor_url)
    expect(editor.locator(".take-row").first).to_be_visible()
    expect(editor.locator("#upload")).to_be_disabled()
    expect(editor.locator("#upload-limits")).to_contain_text("Uploads are paused")
    editor.locator("#file").set_input_files({"name": "generated.wav", "mimeType": "audio/wav", "buffer": b"not-uploaded"})
    expect(editor.locator("#status")).to_contain_text("Uploads are paused")
    assert not uploads


def test_queue_counts_update_without_pipeline_events(editor: Page) -> None:
    """Return None after checking truthful queue counts update without creating progress events."""
    editor.evaluate("window.queueSession=new KeeperProcessing.Listening(state.memo,[],true)")
    for ahead in [2, 1, 0]:
        editor.evaluate("ahead=>queueSession.feed({state:'queued',queue_ahead:ahead,events:[]})", ahead)
        expect(editor.locator("#listening-queue")).to_have_text(
            f"Your memo is queued. {ahead} memo{'s' if ahead != 1 else ''} ahead." if ahead else "Your memo is queued. You are next.")
        expect(editor.locator(".listening-entry")).to_have_count(0)
    editor.evaluate("queueSession.feed({state:'running',events:[]})")
    expect(editor.locator("#listening-queue")).to_be_hidden()
    editor.evaluate("queueSession.cancel()")


def test_long_memo_disables_arrangement_but_keeps_stems(editor: Page) -> None:
    """Return None after checking the twenty-minute arrangement guard, tooltip and programmatic bypass protection."""
    editor.evaluate("state.data.duration_s=1201;state.canvas.clips=[{take_id:state.takes[0].id,start:0,lane:0}];renderCanvas()")
    requested = []
    editor.on("request", lambda request: requested.append(True) if request.url.endswith("/recording") else None)
    editor.locator("#export").click()
    item = editor.get_by_role("menuitem", name="Arrangement (WAV)", exact=True)
    expect(item).to_have_attribute("aria-disabled", "true")
    expect(item).to_have_attribute("title", "Use All takes (ZIP) for long memos")
    expect(editor.get_by_role("menuitem", name="All takes (ZIP)", exact=True)).not_to_have_attribute("aria-disabled", "true")
    editor.keyboard.press("Escape")
    editor.evaluate("exportAudio()")
    expect(editor.locator("#move-toast")).to_contain_text("Use All takes (ZIP) for long memos")
    assert not requested


def test_cross_origin_api_has_no_cors_permission(editor: Page, editor_url: str) -> None:
    """Return None after denying cross-origin requests while permitting the app's own origin."""
    blocked = editor.request.get(editor_url + "/api/config", headers={"Origin": "https://external.invalid"})
    assert blocked.status == 400 and "access-control-allow-origin" not in blocked.headers
    allowed = editor.request.get(editor_url + "/api/config", headers={"Origin": editor_url})
    assert allowed.status == 200 and "access-control-allow-origin" not in allowed.headers
