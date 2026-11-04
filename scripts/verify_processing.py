"""Upload real audio through the deployed browser UI and retain its actual two-line event log."""

import argparse
import json
from pathlib import Path
from time import perf_counter
from typing import Any

from playwright.sync_api import sync_playwright


def verify(url: str, source: Path, destination: Path) -> dict[str, Any]:
    """Return live upload evidence after checking the final editor and downloading a real generated clip."""
    destination.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url, wait_until="networkidle", timeout=120000)
        page.wait_for_function("window.KeeperProcessing !== undefined")
        owner = page.evaluate("localStorage.getItem('keeper-owner')")
        (destination / "owner.json").write_text(json.dumps({"owner": owner}) + "\n", encoding="utf-8")
        started = perf_counter()
        page.locator("#file").set_input_files(str(source.resolve()))
        last_count = -1
        mid_log = mid_reveal = False
        while perf_counter() - started < 1800:
            page.wait_for_timeout(150)
            info = page.evaluate("""() => ({phase:KeeperProcessing.active?.phase,
                count:document.querySelectorAll('.listening-entry').length,
                flying:[...document.querySelectorAll('.chop-clone')].some(n=>n.getAnimations().some(a=>a.currentTime>a.effect.getTiming().delay+100&&a.currentTime<a.effect.getTiming().delay+460)),
                error:document.querySelector('#status.error')?.textContent})""")
            if info["error"]:
                raise RuntimeError(info["error"])
            if info["count"] != last_count:
                print(f"{perf_counter()-started:.1f}s: {info.get('phase', 'editor')} / {info['count']} log events", flush=True)
                last_count = info["count"]
            if not mid_log and info["count"] >= 5 and info.get("phase") == "listening":
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
        lines = page.locator(".listening-entry").evaluate_all("nodes=>nodes.map(n=>[...n.children].map(c=>c.textContent).join('\\n'))")
        assert len(lines) == len(status["events"])
        assert len(status["events"]) <= 400
        assert status["events"][-1]["type"] == "done"
        final = page.evaluate("""() => ({rows:document.querySelectorAll('.take-row').length,
           count:Number(document.getElementById('take-count').textContent),
           expected:state.takes.filter(t=>t.section!=='rejected').length,
           clip:state.takes.find(t=>t.clip_url)?.clip_url, mode:player.mode})""")
        assert final["count"] == final["expected"] and not errors, errors
        clip = page.request.get(url.rstrip("/") + final["clip"], headers={"X-Keeper-Owner": owner})
        assert clip.ok and clip.body()[:4] == b"RIFF"
        assert final["mode"] == "stopped"
        page.screenshot(path=str(destination / "live_final_editor.png"))
        log = "\n\n".join(lines) + "\n"
        (destination / "actual_log.txt").write_text(log, encoding="utf-8")
        report = {"url": url, "memo_id": memo_id, "input": str(source), "elapsed_s": perf_counter() - started,
                  "status": status, "editor": final, "clip_bytes": len(clip.body()), "errors": errors,
                  "mid_log_screenshot": mid_log, "mid_reveal_screenshot": mid_reveal}
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
    args = cli.parse_args()
    verify(args.url, args.source, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
