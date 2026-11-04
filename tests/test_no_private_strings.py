"""Prevent local private substrings from reaching public repository files."""

from pathlib import Path
import subprocess

import pytest

from scripts.check_private_strings import find_private_strings


def test_no_private_strings_in_repository() -> None:
    """Return None after checking public source files without exposing private matches in a failure."""
    root = Path(__file__).resolve().parents[1]
    if not (root / "private" / "denylist.txt").exists():
        pytest.skip("No local private-strings denylist")
    locations = find_private_strings(root)
    if locations:
        pytest.fail("\n".join(f"{path}:{line}" for path, line in locations), pytrace=False)


def test_guard_covers_tracked_untracked_case_and_ignored_files(tmp_path: Path) -> None:
    """Return None after checking private-match locations, case folding and Git ignore boundaries."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text("private/\nignored.txt\n")
    private = tmp_path / "private"
    private.mkdir()
    marker = "sensitive-" + "fixture-marker"
    (private / "denylist.txt").write_text(marker + "\n")
    (tmp_path / "tracked.txt").write_text("safe\n" + marker.upper() + "\n")
    subprocess.run(["git", "add", ".gitignore", "tracked.txt"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "new.txt").write_text(marker)
    (tmp_path / "ignored.txt").write_text(marker)
    assert find_private_strings(tmp_path) == [("new.txt", 1), ("tracked.txt", 2)]
