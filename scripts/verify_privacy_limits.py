"""Verify a deployed app in a fresh browser without recording private strings or processing personal audio."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import re
from typing import Any
from urllib.request import urlopen
import wave

from playwright.sync_api import sync_playwright


def verify(url: str, private_memo_id: str, destination: Path) -> dict[str, Any]:
    """Return safe live-check results for isolation, headers, policy and oversized synthetic audio."""
    destination.mkdir(parents=True, exist_ok=True)
    base = url.rstrip("/")
    report: dict[str, Any] = {}
    html = urlopen(base, timeout=120).read()
    names = ["index.html", *re.findall(r'(?:href|src)="/([^"?]+\.(?:css|js))"', html.decode())]

    def asset(name: str) -> tuple[str, bool]:
        """Return only whether a deployed static asset matches its local source."""
        data = html if name == "index.html" else urlopen(base + "/" + name, timeout=120).read()
        return name, hashlib.sha256(data).digest() == hashlib.sha256((Path("app/static") / name).read_bytes()).digest()

    with ThreadPoolExecutor(max_workers=6) as pool:
        report["assets"] = dict(pool.map(asset, names))
    assert all(report["assets"].values())
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(type(error).__name__))
        page.goto(base, wait_until="networkidle", timeout=120000)
        page.wait_for_function("window.KeeperProcessing !== undefined")
        config_response = page.request.get(base + "/api/config")
        config = config_response.json()
        assert set(config) == {"uploads_enabled", "max_audio_minutes", "max_upload_mb", "retention_days"}
        assert config["max_audio_minutes"] == 30 and config["max_upload_mb"] == 150
        report["config"] = config
        rows = page.evaluate("json('/api/memos')")
        assert all(row["showcase"] and not row["owned"] for row in rows)
        report["fresh_browser_public_only"] = True
        report["showcase_count"] = len(rows)
        report["private_memo_status"] = page.evaluate("""async identity => {
          const response=await fetch('/api/memos/'+encodeURIComponent(identity),
            {headers:{'X-Keeper-Owner':localStorage.getItem('keeper-owner')},cache:'no-store'});
          return response.status;
        }""", private_memo_id)
        assert report["private_memo_status"] == 404
        report["docs_statuses"] = {path: page.request.get(base + path).status
                                    for path in ["/docs", "/redoc", "/openapi.json"]}
        assert set(report["docs_statuses"].values()) == {404}
        for path in ["/api/config", "/api/memos/missing", "/robots.txt", "/"]:
            response = page.request.get(base + path)
            assert response.headers["x-robots-tag"] == "noindex, nofollow"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert "access-control-allow-origin" not in response.headers
            if path.startswith("/api/"):
                assert response.headers["cache-control"] == "private, no-store"
                assert response.headers["x-content-type-options"] == "nosniff"
        assert "Disallow: /" in page.request.get(base + "/robots.txt").text()
        report["headers_and_robots"] = True
        for path in ["/outputs/", "/uploads/", "/data/"]:
            assert page.request.get(base + path).status == 404
        report["static_storage_hidden"] = True
        stream = io.BytesIO()
        with wave.open(stream, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(1)
            audio.writeframes(b"\0" * (31 * 60 * 2))
        # Post the header-duration fixture directly so the browser does not decode
        # thirty-one minutes merely to draw upload peaks for an expected refusal.
        refused = page.evaluate("""async bytes => {
          const form=new FormData();
          form.append('file',new Blob([new Uint8Array(bytes)],{type:'audio/wav'}),crypto.randomUUID()+'.wav');
          const response=await fetch('/api/memos',{method:'POST',body:form,
            headers:{'X-Keeper-Owner':localStorage.getItem('keeper-owner')}});
          const body=await response.json();if(!response.ok)notice(body.detail,true);
          return {status:response.status,detail:body.detail};
        }""", list(stream.getvalue()))
        message = "Up to 30 minutes and 150 MB. Trim the memo and try again."
        assert refused["status"] == 400 and refused["detail"] == message
        page.wait_for_function("document.getElementById('status').textContent.includes('Up to 30 minutes and 150 MB')")
        assert len(page.evaluate("json('/api/memos')")) == len(rows)
        report["oversized_synthetic_upload_status"] = refused["status"]
        report["limits_message_shown"] = True
        report["synthetic_upload_bytes"] = len(stream.getvalue())
        report["no_job_admitted"] = True
        page.screenshot(path=str(destination / "oversized_rejected.png"))
        assert not errors
        report["browser_error_count"] = len(errors)
        browser.close()
    (destination / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    """Return zero for successful live checks while printing only counts, statuses and safe flags."""
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("url")
    cli.add_argument("--private-memo-id", required=True)
    cli.add_argument("--out", type=Path, default=Path("outputs/privacy_limits_live"))
    args = cli.parse_args()
    try:
        report = verify(args.url, args.private_memo_id, args.out)
    except Exception as exc:
        print(f"Live checks failed ({type(exc).__name__}); no private response contents retained.")
        return 1
    print(json.dumps({key: value for key, value in report.items() if key != "assets"}, indent=2))
    print(f"Verified {len(report['assets'])} deployed assets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
