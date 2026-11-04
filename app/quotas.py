"""Serialize upload admission and persist rolling cost reservations on shared storage."""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from threading import RLock
from time import time
from typing import Any

from app.metadata import owner_fingerprint, owns, read_meta
from app.policy import Policy
from app.storage import Settings, memo_directory


ADMISSION_LOCK = RLock()
WINDOW_SECONDS = 24 * 60 * 60


class QuotaExceeded(Exception):
    """An upload refusal with a client-safe explanation and retry interval."""

    def __init__(self, message: str, retry_after: int) -> None:
        """Return None after retaining a safe refusal and its positive retry interval."""
        super().__init__(message)
        self.retry_after = max(1, retry_after)


def active_memos(settings: Settings) -> list[tuple[str, dict[str, Any], float]]:
    """Return queued or running memo identities and admission times without exposing them to clients."""
    active = []
    if not settings.outputs_root.is_dir():
        return active
    for item in settings.outputs_root.iterdir():
        try:
            directory = memo_directory(settings, item.name)
            status = json.loads((directory / "status.json").read_text(encoding="utf-8"))
            if status["state"] not in {"queued", "running"}:
                continue
            metadata = read_meta(directory)
            created = datetime.fromisoformat(metadata["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            active.append((item.name, metadata, created.timestamp()))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return active


def queue_ahead(settings: Settings, memo_id: str) -> int:
    """Return only the number of active memos admitted before the requested queued memo."""
    active = active_memos(settings)
    target = next(((created, identity) for identity, _, created in active if identity == memo_id), None)
    return sum((created, identity) < target for identity, _, created in active) if target is not None else 0


def _wait_for_budget(rows: list[dict[str, Any]], duration_s: float, max_count: int | None,
                     max_minutes: float, now: float) -> int:
    """Return seconds until enough rolling reservations expire for the proposed upload."""
    remaining = sorted(rows, key=lambda row: row["accepted_at"])
    for index, row in enumerate(remaining):
        later = remaining[index + 1:]
        if (max_count is None or len(later) < max_count) and sum(item["duration_s"] for item in later) + duration_s <= max_minutes * 60:
            return max(1, math.ceil(row["accepted_at"] + WINDOW_SECONDS - now))
    return WINDOW_SECONDS


def reserve_upload(settings: Settings, token: str, duration_s: float, memo_id: str,
                   limits: Policy, now: float | None = None) -> None:
    """Return None after atomically charging an upload or raise before any GPU work is admitted."""
    now = time() if now is None else now
    with ADMISSION_LOCK:
        for _, metadata, _ in active_memos(settings):
            if owns(metadata, token):
                raise QuotaExceeded("Your previous memo is still processing. Please wait for it to finish.", 30)
        folder = settings.outputs_root / ".quota"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "usage.json"
        rows = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        rows = [row for row in rows if row["accepted_at"] > now - WINDOW_SECONDS]
        identity = owner_fingerprint(token)
        owned = [row for row in rows if row["owner"] == identity]
        if len(owned) >= limits.owner_daily_uploads or sum(row["duration_s"] for row in owned) + duration_s > limits.owner_daily_minutes * 60:
            wait = _wait_for_budget(owned, duration_s, limits.owner_daily_uploads, limits.owner_daily_minutes, now)
            raise QuotaExceeded("This browser has reached its daily upload allowance. Please try again later.", wait)
        if sum(row["duration_s"] for row in rows) + duration_s > limits.daily_minutes * 60:
            wait = _wait_for_budget(rows, duration_s, None, limits.daily_minutes, now)
            raise QuotaExceeded("Demo capacity reached for today. The showcase memo is still open.", wait)
        rows.append({"memo_id": memo_id, "owner": identity, "duration_s": duration_s, "accepted_at": now})
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(rows, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(path)


def release_reservation(settings: Settings, memo_id: str) -> None:
    """Return None after undoing admission when saving the memo failed before it could start."""
    with ADMISSION_LOCK:
        path = settings.outputs_root / ".quota" / "usage.json"
        if path.is_file():
            rows = json.loads(path.read_text(encoding="utf-8"))
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps([row for row in rows if row["memo_id"] != memo_id]) + "\n", encoding="utf-8")
            temporary.replace(path)
