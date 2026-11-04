"""Exercise bounded expiration, shared sources, dry runs and showcase preservation."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess

from app.metadata import save_meta
from app.policy import Policy
from app.retention import sweep_expired
from app.storage import Settings


def seed(settings: Settings, identity: str, created: datetime, showcase: bool = False) -> Path:
    """Return a synthetic memo directory with an uploaded source and cached export artifact."""
    directory = settings.outputs_root / identity
    directory.mkdir(parents=True)
    settings.uploads_root.mkdir(parents=True, exist_ok=True)
    source = settings.uploads_root / (identity + ".wav")
    source.write_bytes(b"synthetic audio")
    save_meta(directory, {"title": "Retention fixture", "owner": None, "showcase": showcase,
                          "created_at": created.isoformat()})
    (directory / "upload.json").write_text(json.dumps({"filename": source.name}))
    (directory / "cached-stems.zip").write_bytes(b"synthetic archive")
    return directory


def test_bounded_sweep_expires_sources_and_preserves_showcases(tmp_path: Path) -> None:
    """Return None after checking two bounded batches remove expired files and preserve recent/showcase memos."""
    settings = Settings(outputs_root=tmp_path / "memos", uploads_root=tmp_path / "uploads")
    now = datetime.now(timezone.utc)
    for index in range(25):
        seed(settings, f"expired-{index:02d}", now - timedelta(days=15))
    recent = seed(settings, "recent", now)
    showcase = seed(settings, "showcase", now - timedelta(days=100), True)
    first = sweep_expired(settings, Policy(), now)
    assert len(first) == 20
    assert all(not (settings.uploads_root / (identity + ".wav")).exists() for identity in first)
    second = sweep_expired(settings, Policy(), now)
    assert len(second) == 5 and recent.is_dir() and showcase.is_dir()
    assert (showcase / "cached-stems.zip").is_file()
    assert (settings.uploads_root / "showcase.wav").is_file()


def test_cursor_advances_across_recent_memos_and_dry_run_changes_nothing(tmp_path: Path) -> None:
    """Return None after checking dry runs preserve every byte and recent folders cannot starve expiration."""
    settings = Settings(outputs_root=tmp_path / "memos", uploads_root=tmp_path / "uploads")
    now = datetime.now(timezone.utc)
    for index in range(22):
        seed(settings, f"a-recent-{index:02d}", now)
    expired = seed(settings, "z-expired", now - timedelta(days=30))
    assert sweep_expired(settings, now=now) == []
    before = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert sweep_expired(settings, now=now, dry_run=True) == [expired.name]
    after = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert before == after
    assert sweep_expired(settings, now=now) == [expired.name] and not expired.exists()


def test_legacy_shared_source_and_traversal_are_safe(tmp_path: Path) -> None:
    """Return None after retaining showcase-shared audio and rejecting source paths outside uploads."""
    settings = Settings(outputs_root=tmp_path / "memos", uploads_root=tmp_path / "uploads")
    now = datetime.now(timezone.utc)
    expired = seed(settings, "expired", now - timedelta(days=30))
    showcase = seed(settings, "showcase", now - timedelta(days=30), True)
    (expired / "upload.json").write_text((showcase / "upload.json").read_text())
    bad = seed(settings, "traversal", now - timedelta(days=30))
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"preserve outside")
    (bad / "upload.json").write_text(json.dumps({"filename": "../outside.wav"}))
    assert len(sweep_expired(settings, now=now)) == 2
    assert outside.read_bytes() == b"preserve outside" and showcase.is_dir()
    assert (settings.uploads_root / "showcase.wav").is_file()


def test_manual_purge_dry_run(tmp_path: Path) -> None:
    """Return None after checking the manual script reports only IDs and leaves dry-run files intact."""
    settings = Settings(outputs_root=tmp_path / "memos", uploads_root=tmp_path / "uploads")
    now = datetime.now(timezone.utc)
    expired = seed(settings, "expired", now - timedelta(days=30))
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([str(root / ".venv/Scripts/python.exe"), "scripts/purge_expired.py", "--dry-run"],
                            cwd=root, env={**os.environ, "KEEPER_OUTPUTS_DIR": str(settings.outputs_root),
                                           "KEEPER_UPLOADS_DIR": str(settings.uploads_root)}, capture_output=True, text=True)
    assert result.returncode == 0 and "Would remove 1" in result.stdout and expired.is_dir()
