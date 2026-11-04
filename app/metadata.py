"""Memo ownership, friendly metadata, and non-destructive legacy migration."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from app.storage import Settings, memo_directory


def owner_token(value: str | None) -> str | None:
    """Return a canonical UUID owner token or None for absent or invalid headers."""
    try:
        return str(UUID(value)) if value else None
    except (ValueError, AttributeError):
        return None


def friendly_title(timestamp: datetime) -> str:
    """Return a default memo title in the supplied datetime's timezone."""
    return f"Memo, {timestamp.strftime('%b')} {timestamp.day}, {timestamp.hour % 12 or 12}:{timestamp.minute:02d} {'AM' if timestamp.hour < 12 else 'PM'}"


def save_meta(directory: Path, metadata: dict[str, Any]) -> None:
    """Return None after atomically saving metadata without exposing its owner token."""
    temporary = directory / "meta.tmp"
    temporary.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    temporary.replace(directory / "meta.json")


def read_meta(directory: Path) -> dict[str, Any]:
    """Return stored metadata, treating an unmigrated folder as private and ownerless."""
    path = directory / "meta.json"
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError("Metadata path escaped memo")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"title": "Memo", "original_filename": "", "created_at": "", "owner": None, "showcase": False}


def owns(metadata: dict[str, Any], owner: str | None) -> bool:
    """Return whether a nonempty caller token owns this memo."""
    return owner is not None and metadata.get("owner") == owner


def public_meta(metadata: dict[str, Any], owner: str | None) -> dict[str, Any]:
    """Return display metadata and ownership status without disclosing the owner token."""
    return {key: metadata.get(key) for key in ("title", "created_at", "showcase")} | {"owned": owns(metadata, owner)}


def migrate_memos(settings: Settings) -> list[str]:
    """Return migrated memo IDs after adding private ownerless metadata without changing existing files."""
    migrated = []
    if not settings.outputs_root.exists():
        return migrated
    for item in settings.outputs_root.iterdir():
        if not item.is_dir():
            continue
        try:
            directory = memo_directory(settings, item.name)
        except ValueError:
            continue
        if (directory / "meta.json").exists() or not any((directory / name).is_file() for name in ("songmap.json", "upload.json", "status.json")):
            continue
        status_path, upload_path = directory / "status.json", directory / "upload.json"
        status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
        upload = json.loads(upload_path.read_text(encoding="utf-8")) if upload_path.exists() else {}
        created = status.get("history", [{}])[0].get("updated_at") if status.get("history") else None
        timestamp = datetime.fromisoformat(created) if created else datetime.fromtimestamp(directory.stat().st_mtime, timezone.utc)
        save_meta(directory, {"title": friendly_title(timestamp), "original_filename": upload.get("original_filename", upload.get("filename", "")),
                              "created_at": timestamp.isoformat(), "owner": None, "showcase": False})
        migrated.append(item.name)
    return migrated


def set_showcase(settings: Settings, memo_id: str, enabled: bool) -> dict[str, Any]:
    """Return public metadata after an authenticated administrator changes showcase visibility."""
    directory = memo_directory(settings, memo_id)
    if not (directory / "meta.json").is_file():
        raise FileNotFoundError("Migrate memo metadata before changing showcase visibility")
    metadata = read_meta(directory)
    metadata["showcase"] = enabled
    save_meta(directory, metadata)
    return public_meta(metadata, None)
