"""Bound expiration work and remove only validated non-showcase memo storage."""

from bisect import bisect_right
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from app.metadata import read_meta
from app.policy import Policy, policy
from app.storage import Settings, memo_directory


def remove_memo_files(settings: Settings, directory: Path) -> None:
    """Return None after deleting a validated memo directory and its own uploaded source."""
    checked = memo_directory(settings, directory.name)
    if checked != directory.resolve() or checked == settings.outputs_root.resolve():
        raise ValueError("Invalid memo deletion target")
    manifest = checked / "upload.json"
    if manifest.is_file() and manifest.resolve().is_relative_to(checked):
        upload = json.loads(manifest.read_text(encoding="utf-8"))
        source = (settings.uploads_root / upload.get("filename", "")).resolve()
        if source != settings.uploads_root.resolve() and source.is_relative_to(settings.uploads_root.resolve()):
            shared = False
            for item in settings.outputs_root.iterdir():
                if item.name == checked.name:
                    continue
                try:
                    other = memo_directory(settings, item.name)
                    other_manifest = other / "upload.json"
                    if other_manifest.is_file() and other_manifest.resolve().is_relative_to(other):
                        reference = json.loads(other_manifest.read_text(encoding="utf-8")).get("filename", "")
                        if (settings.uploads_root / reference).resolve() == source:
                            shared = True
                            break
                except (OSError, ValueError, TypeError):
                    continue
            if source.is_file() and not shared:
                source.unlink(missing_ok=True)
    shutil.rmtree(checked)


def sweep_expired(settings: Settings, limits: Policy | None = None, now: datetime | None = None,
                  limit: int = 20, dry_run: bool = False) -> list[str]:
    """Return expired memo IDs from at most twenty inspected folders, preserving every showcase."""
    limits = limits or policy()
    now = now or datetime.now(timezone.utc)
    if not settings.outputs_root.is_dir():
        return []
    identities = []
    for item in settings.outputs_root.iterdir():
        try:
            directory = memo_directory(settings, item.name)
            if directory.is_dir():
                identities.append(item.name)
        except ValueError:
            continue
    identities.sort()
    cursor_path = settings.outputs_root / ".retention-cursor"
    cursor = cursor_path.read_text(encoding="utf-8") if cursor_path.is_file() else ""
    start = bisect_right(identities, cursor)
    candidates = (identities[start:] + identities[:start])[:max(0, min(20, limit))]
    expired = []
    for identity in candidates:
        try:
            directory = memo_directory(settings, identity)
            metadata = read_meta(directory)
            if metadata.get("showcase"):
                continue
            created = datetime.fromisoformat(metadata["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if (now - created).total_seconds() < limits.retention_days * 86400:
                continue
            expired.append(identity)
            if not dry_run:
                remove_memo_files(settings, directory)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if candidates and not dry_run:
        temporary = cursor_path.with_suffix(".tmp")
        temporary.write_text(candidates[-1], encoding="utf-8")
        temporary.replace(cursor_path)
    return expired
