"""Check tracked and untracked non-ignored files without displaying private matching text."""

from pathlib import Path
import subprocess


def find_private_strings(root: Path, denylist: Path | None = None) -> list[tuple[str, int]]:
    """Return only file paths and line numbers containing local case-insensitive private substrings."""
    denylist = denylist or root / "private" / "denylist.txt"
    if not denylist.is_file():
        return []
    needles = [line.strip().casefold() for line in denylist.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    result = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                            cwd=root, capture_output=True, check=True)
    matches = []
    for name in sorted(set(result.stdout.decode("utf-8").split("\0")) - {""}):
        path = root / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        content = data.decode("utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig", errors="replace")
        for number, line in enumerate(content.splitlines(), 1):
            if any(needle in line.casefold() for needle in needles):
                matches.append((name, number))
    return matches


def main() -> int:
    """Return a failing exit code for private matches while printing only their file locations."""
    root = Path(__file__).resolve().parents[1]
    if not (root / "private" / "denylist.txt").exists():
        print("Private-strings guard skipped: no local denylist.")
        return 0
    matches = find_private_strings(root)
    for path, line in matches:
        print(f"{path}:{line}")
    if not matches:
        print("Private-strings guard passed.")
    return int(bool(matches))


if __name__ == "__main__":
    raise SystemExit(main())
