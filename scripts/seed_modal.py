"""Upload only data/memo2.wav to a deployed Keeper API and report timestamped stage timings."""

import argparse
import json
from datetime import datetime
from pathlib import Path
from time import perf_counter, sleep
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
OWNER = str(uuid4())


def api_request(url: str, data: bytes | None = None, content_type: str | None = None) -> Any:
    """Return decoded API JSON or raise a clear HTTP failure without credentials."""
    headers = {"X-Keeper-Owner": OWNER}
    if content_type is not None:
        headers["Content-Type"] = content_type
    try:
        with urlopen(Request(url, data=data, headers=headers), timeout=300) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode(errors='replace')}") from exc


def seed(url: str) -> dict[str, Any]:
    """Return measured memo2 upload, queue, stage, and total timings after verifying it is listed done."""
    parsed = urlsplit(url)
    if parsed.scheme not in ("https", "http") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Supply a plain deployed HTTP(S) base URL without credentials or query parameters")
    url = url.rstrip("/")
    source = ROOT / "data" / "memo2.wav"
    boundary = f"keeper-{uuid4().hex}"
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="memo2.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n").encode() + source.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    started = perf_counter()
    result = api_request(url + "/api/memos", body, f"multipart/form-data; boundary={boundary}")
    upload_seconds = perf_counter() - started
    memo_id = result["memo_id"]
    print(f"Uploaded memo2 as {memo_id} in {upload_seconds:.3f} s", flush=True)
    status_url = url + f"/api/memos/{quote(memo_id, safe='')}/status"
    previous = None
    while perf_counter() - started < 2100:
        status = api_request(status_url)
        if (status["state"], status["stage"]) != previous:
            previous = status["state"], status["stage"]
            print(f"{perf_counter() - started:.1f} s: {status['state']} / {status['stage']}", flush=True)
        if status["state"] in ("done", "failed"):
            break
        sleep(3)
    else:
        raise TimeoutError(f"Timed out waiting for {memo_id}; resume inspection at {status_url}")
    history = status["history"]
    timings = [{"stage": row["stage"], "seconds": (datetime.fromisoformat(next_row["updated_at"]) -
                datetime.fromisoformat(row["updated_at"])).total_seconds()}
               for row, next_row in zip(history, history[1:])]
    listed = api_request(url + "/api/memos")
    listed_ids = [item["id"] if isinstance(item, dict) else item for item in listed]
    if status["state"] == "done" and memo_id not in listed_ids:
        raise RuntimeError(f"Completed memo {memo_id} is missing from /api/memos")
    report = {"url": url, "memo_id": memo_id, "wall_clock_s": perf_counter() - started,
              "upload_and_validation_s": upload_seconds, "stages": timings, "gpu": status.get("gpu"),
              "parser": status.get("parser"), "state": status["state"], "listed": memo_id in listed_ids, "status": status}
    output = ROOT / "outputs" / "modal_seed_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if status["state"] == "failed":
        raise RuntimeError(f"Memo {memo_id} failed: {status['error']}")
    return report


def main() -> int:
    """Return zero after seeding memo2 and printing the complete timing report."""
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("url")
    args = cli.parse_args()
    print(json.dumps(seed(args.url), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
