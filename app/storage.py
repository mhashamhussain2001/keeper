"""Environment-configured memo storage and atomic processing status artifacts."""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    """Shared local or mounted filesystem locations."""

    outputs_root: Path = field(default_factory=lambda: Path(os.environ.get("KEEPER_OUTPUTS_DIR", PROJECT_ROOT / "outputs")))
    uploads_root: Path = field(default_factory=lambda: Path(os.environ.get("KEEPER_UPLOADS_DIR", PROJECT_ROOT / "data" / "uploads")))
    static_root: Path = PROJECT_ROOT / "app" / "static"


def memo_directory(settings: Settings, memo_id: str) -> Path:
    """Return a validated memo directory strictly inside the configured outputs root."""
    if not memo_id or memo_id in (".", "..") or any(char in memo_id for char in "/\\:"):
        raise ValueError("Invalid memo ID")
    directory = (settings.outputs_root / memo_id).resolve()
    if not directory.is_relative_to(settings.outputs_root.resolve()) or directory == settings.outputs_root.resolve():
        raise ValueError("Invalid memo directory")
    return directory


def write_status(directory: Path, state: str, stage: str, error: str | None = None,
                 **metadata: Any) -> dict[str, Any]:
    """Return an atomically persisted processing status with a timestamped transition history."""
    if state not in ("queued", "running", "done", "failed"):
        raise ValueError("Invalid processing state")
    path = directory / "status.json"
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    timestamp = datetime.now(timezone.utc).isoformat()
    history = previous.get("history", [])
    if not history or (history[-1]["state"], history[-1]["stage"]) != (state, stage):
        history.append({"state": state, "stage": stage, "updated_at": timestamp})
    status = {**previous, **metadata, "state": state, "stage": stage, "error": error,
              "updated_at": timestamp, "history": history}
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / "status.tmp"
    temporary.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return status
