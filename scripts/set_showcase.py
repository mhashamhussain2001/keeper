"""Change showcase visibility through authenticated Modal administration, never a public mutation API."""

import argparse
import json


def set_remote_showcase(url: str, memo_id: str, enabled: bool) -> dict:
    """Return updated metadata after verifying the URL belongs to the authenticated Keeper deployment."""
    import modal

    web = modal.Function.from_name("keeper", "web")
    if url.rstrip("/") != web.get_web_url().rstrip("/"):
        raise ValueError("URL does not match the Keeper deployment in the active Modal workspace")
    return modal.Function.from_name("keeper", "manage_memos").remote("showcase", memo_id, enabled)


def main() -> int:
    """Return zero after printing an authenticated showcase visibility update."""
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("url")
    cli.add_argument("memo_id")
    cli.add_argument("state", choices=("on", "off"))
    args = cli.parse_args()
    print(json.dumps(set_remote_showcase(args.url, args.memo_id, args.state == "on"), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
