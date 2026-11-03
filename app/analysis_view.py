"""Read saved parser decisions into a safe, display-only analysis view."""

import json
from pathlib import Path
from typing import Any


def command_analysis(directory: Path, songmap: dict[str, Any], regions: list[dict[str, Any]]) -> dict[str, Any]:
    """Return public command metadata and filed take IDs from existing parser cache records."""
    path = directory / "spark_cache.json"
    if not path.is_file() or not regions:
        return {"commands": []}
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"commands": []}
    if not isinstance(entries, dict):
        return {"commands": []}
    rows: dict[tuple[float, float], dict[str, Any]] = {}
    takes = [take for section in songmap["sections"].values() for take in section]
    for entry in entries.values():
        if not isinstance(entry, dict) or not isinstance(entry.get("parsed"), dict):
            continue
        start, end = entry.get("start"), entry.get("end")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        # An ablation cache may contain alternate speech regions. Use the saved memo timeline.
        if not any(region["kind"] == "speech" and abs(region["start"]-start)<.001 and abs(region["end"]-end)<.001 for region in regions):
            continue
        parsed = entry["parsed"]
        intent = parsed.get("intent", "none")
        heard = parsed.get("heard", "")
        explicit = [take for take in takes if heard and heard in (take.get("heard") or "")]
        rows[(start,end)] = {"start": start, "end": end, "heard": parsed.get("heard", ""), "intent": intent,
                            "section": parsed.get("section"), "target": parsed.get("target", "previous"),
                            "confidence": parsed.get("confidence", 0), "take_ids": [take["id"] for take in explicit] if intent in ("file","keep","reject") else []}
    return {"commands": sorted(rows.values(), key=lambda row: row["start"])}
