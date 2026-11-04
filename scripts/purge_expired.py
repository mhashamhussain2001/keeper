"""Run one bounded memo-retention sweep without ever printing private metadata."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.policy import policy
from app.quotas import ADMISSION_LOCK
from app.retention import sweep_expired
from app.storage import Settings


def main() -> int:
    """Return zero after printing only expired memo IDs from a bounded optional dry run."""
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--dry-run", action="store_true")
    cli.add_argument("--modal", action="store_true", help="Run the bounded sweep against the deployed volume")
    args = cli.parse_args()
    if args.modal:
        import modal

        expired = modal.Function.from_name("keeper", "manage_memos").remote("purge", dry_run=args.dry_run)["expired"]
    else:
        with ADMISSION_LOCK:
            expired = sweep_expired(Settings(), policy(), dry_run=args.dry_run)
    for identity in expired:
        print(identity)
    print(f"{'Would remove' if args.dry_run else 'Removed'} {len(expired)} expired memo(s); at most 20 folders inspected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
