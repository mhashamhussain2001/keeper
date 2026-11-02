"""Time the specified test_2.m4a upload through the deployed private memo API."""

import argparse
from datetime import datetime
import json
from pathlib import Path
from time import perf_counter, sleep
from urllib.parse import quote
from uuid import uuid4

from scripts import seed_modal


def measure(url: str, root: Path = seed_modal.ROOT, output_name: str = "logic_v2_upload") -> dict:
    """Return upload-to-completion timing for test_2.m4a, retaining private access in an ignored file."""
    source = root / "data/test_2.m4a"
    destination = root / "outputs" / output_name
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "owner.json").write_text(json.dumps({"owner": seed_modal.OWNER}) + "\n", encoding="utf-8")
    boundary = "keeper-" + uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="test_2.m4a"\r\n'
            'Content-Type: audio/mp4\r\n\r\n').encode() + source.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    started = perf_counter()
    result = seed_modal.api_request(url.rstrip("/") + "/api/memos", body, f"multipart/form-data; boundary={boundary}")
    upload_seconds = perf_counter() - started
    memo_id = result["memo_id"]
    (destination / "memo.json").write_text(json.dumps(result) + "\n", encoding="utf-8")
    status_url = url.rstrip("/") + "/api/memos/" + quote(memo_id, safe="") + "/status"
    previous = None
    while perf_counter() - started < 2100:
        status = seed_modal.api_request(status_url)
        current = status["state"], status["stage"]
        if current != previous:
            print(f"{perf_counter() - started:.3f}s: {current[0]} / {current[1]}", flush=True)
            previous = current
        if status["state"] in ("done", "failed"):
            break
        sleep(3)
    else:
        raise TimeoutError("Upload timing exceeded 35 minutes; inspect the persisted memo ID")
    history = status["history"]
    stages = [{"stage": first["stage"], "seconds": (datetime.fromisoformat(second["updated_at"]) -
               datetime.fromisoformat(first["updated_at"])).total_seconds()} for first, second in zip(history, history[1:])]
    detail = seed_modal.api_request(url.rstrip("/") + "/api/memos/" + quote(memo_id, safe="")) if status["state"] == "done" else {}
    report = {"memo_id": memo_id, "input": "data/test_2.m4a", "state": status["state"],
              "upload_validation_http_s": upload_seconds, "end_to_end_s": perf_counter() - started,
              "stages": stages, "status": status, "detail": detail}
    (destination / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if status["state"] != "done":
        raise RuntimeError(f"Uploaded memo failed: {status.get('error')}")
    return report


def main() -> int:
    """Return zero after printing a successful private test_2.m4a upload timing report."""
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("url")
    args = cli.parse_args()
    report = measure(args.url)
    print(json.dumps({key: value for key, value in report.items() if key != "detail"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
