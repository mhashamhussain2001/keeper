"""Upload real audio through the deployed browser UI and retain its actual two-line event log."""

import argparse
import json
import io
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import unquote
from zipfile import ZipFile

from playwright.sync_api import sync_playwright


def verify(url: str, source: Path, destination: Path, upload_name: str = "123 Example Street 4.m4a") -> dict[str, Any]:
    """Return live upload evidence after checking the final editor and downloading a real generated clip."""
    destination.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors: list[str] = []
        privacy_errors: list[str] = []
        json_responses = 0

        def check_json(response: Any) -> None:
            """Return None after checking every browser API JSON response for private upload identifiers."""
            nonlocal json_responses
            if "/api/" in response.url and "application/json" in response.headers.get("content-type", ""):
                value = response.text()
                json_responses += 1
                if "Example Street" in value or '"original_filename"' in value or '"filename"' in value:
                    privacy_errors.append(response.url)

        page.on("response", check_json)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url, wait_until="networkidle", timeout=120000)
        page.wait_for_function("window.KeeperProcessing !== undefined")
        owner = page.evaluate("localStorage.getItem('keeper-owner')")
        (destination / "owner.json").write_text(json.dumps({"owner": owner}) + "\n", encoding="utf-8")
        started = perf_counter()
        page.locator("#file").set_input_files({"name": upload_name, "mimeType": "audio/mp4", "buffer": source.read_bytes()})
        last_count = -1
        mid_log = mid_reveal = False
        while perf_counter() - started < 1800:
            page.wait_for_timeout(150)
            assert "Example Street" not in page.content()
            info = page.evaluate("""() => ({phase:KeeperProcessing.active?.phase,
                count:document.querySelectorAll('.listening-entry').length,
                flying:[...document.querySelectorAll('.chop-clone')].some(n=>n.getAnimations().some(a=>a.currentTime>a.effect.getTiming().delay+100&&a.currentTime<a.effect.getTiming().delay+460)),
                error:document.querySelector('#status.error')?.textContent})""")
            if info["error"]:
                raise RuntimeError(info["error"])
            if info["count"] != last_count:
                print(f"{perf_counter()-started:.1f}s: {info.get('phase', 'editor')} / {info['count']} log events", flush=True)
                last_count = info["count"]
            if not mid_log and info["count"] >= 3 and info.get("phase") == "listening" and page.locator(".listening-progress[aria-valuenow='0']").count() == 0:
                page.screenshot(path=str(destination / "live_mid_log.png"))
                mid_log = True
            if not mid_reveal and info["flying"]:
                page.screenshot(path=str(destination / "live_mid_reveal.png"))
                mid_reveal = True
            if info["count"] and not info.get("phase"):
                break
        else:
            raise TimeoutError("Live browser upload did not reach the editor within 30 minutes")
        memo_id = page.evaluate("state.memo")
        response = page.request.get(url.rstrip("/") + f"/api/memos/{memo_id}/status", headers={"X-Keeper-Owner": owner})
        status = response.json()
        assert status["state"] == "done", status
        lines = page.locator(".listening-entry").evaluate_all("nodes=>nodes.map(n=>[...n.querySelectorAll('.listening-voice,.listening-process,.listening-caption')].filter(c=>!c.hidden).map(c=>c.textContent).join('\\n'))")
        expected_rows = sum(event["type"] != "ast_progress" for event in status["events"])
        assert len(lines) == expected_rows
        assert page.locator(".listening-entry[data-event=ast_start]").count() == 1
        assert page.locator(".listening-entry[data-event=ast_progress]").count() == 0
        assert len(status["events"]) <= 400
        assert status["events"][-1]["type"] == "done"
        final = page.evaluate("""() => ({rows:document.querySelectorAll('.take-row').length,
           count:Number(document.getElementById('take-count').textContent),
           expected:state.takes.filter(t=>t.section!=='rejected').length,
           clip:state.takes.find(t=>t.clip_url)?.clip_url, mode:player.mode})""")
        assert final["count"] == final["expected"] and not errors, errors
        clip = page.request.get(url.rstrip("/") + final["clip"], headers={"X-Keeper-Owner": owner})
        assert clip.ok and clip.body()[:4] == b"RIFF"
        assert "Example Street" not in unquote(str(clip.headers))
        recording = page.request.get(url.rstrip("/") + f"/api/memos/{memo_id}/recording",
                                     headers={"X-Keeper-Owner": owner, "Range": "bytes=0-63"})
        assert recording.ok and "Example Street" not in unquote(str(recording.headers))
        assert final["mode"] == "stopped"
        page.screenshot(path=str(destination / "live_final_editor.png"))
        log = "\n\n".join(lines) + "\n"
        (destination / "actual_log.txt").write_text(log, encoding="utf-8")
        downloads = []
        for path in ["/api/memos", f"/api/memos/{memo_id}", f"/api/memos/{memo_id}/status", f"/api/memos/{memo_id}/canvas"]:
            reply = page.request.get(url.rstrip("/") + path, headers={"X-Keeper-Owner": owner})
            assert reply.ok and "Example Street" not in reply.text() and '"original_filename"' not in reply.text()
        stems = page.request.get(url.rstrip("/") + f"/api/memos/{memo_id}/export/stems", headers={"X-Keeper-Owner": owner}, timeout=120000)
        assert stems.ok and "Example Street" not in unquote(str(stems.headers))
        with ZipFile(io.BytesIO(stems.body())) as archive:
            assert "Example Street" not in str(archive.namelist())
            assert b"Example Street" not in archive.read("songmap.txt")
            zip_names = archive.namelist()
        with page.expect_download(timeout=120000) as download_info:
            page.evaluate("exportAudio(state.takes.find(take=>take.clip_url))")
        download = download_info.value
        assert "Example Street" not in download.suggested_filename and download.failure() is None
        download.save_as(str(destination / download.suggested_filename))
        downloads.append(download.suggested_filename)
        page.evaluate("void KeeperProcessing.replay()")
        page.wait_for_function("KeeperProcessing.active?.phase==='listening'")
        while page.evaluate("KeeperProcessing.active!==null"):
            assert "Example Street" not in page.content()
            page.wait_for_timeout(150)
        replay_lines = page.locator(".listening-entry").evaluate_all("nodes=>nodes.map(n=>[...n.querySelectorAll('.listening-voice,.listening-process,.listening-caption')].filter(c=>!c.hidden).map(c=>c.textContent).join('\\n'))")
        assert replay_lines == lines
        page.screenshot(path=str(destination / "live_final_replay.png"))
        assert not privacy_errors and not errors, {"privacy": privacy_errors, "browser": errors}
        report = {"url": url, "memo_id": memo_id, "input": str(source), "elapsed_s": perf_counter() - started,
                  "status": status, "editor": final, "clip_bytes": len(clip.body()), "errors": errors,
                  "mid_log_screenshot": mid_log, "mid_reveal_screenshot": mid_reveal,
                  "privacy_json_responses": json_responses, "privacy_errors": privacy_errors,
                  "zip_entries": zip_names, "stems_bytes": len(stems.body()), "downloads": downloads, "replay_log_matches": True}
        (destination / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(log, flush=True)
        print(json.dumps({key: value for key, value in report.items() if key != "status"}, indent=2), flush=True)
        browser.close()
        return report


def main() -> int:
    """Return zero after verifying a deployed real audio upload and saving its browser evidence."""
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("url")
    cli.add_argument("--source", type=Path, default=Path("data/test_2.m4a"))
    cli.add_argument("--out", type=Path, default=Path("outputs/processing_upload"))
    cli.add_argument("--upload-name", default="123 Example Street 4.m4a")
    args = cli.parse_args()
    verify(args.url, args.source, args.out, args.upload_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
