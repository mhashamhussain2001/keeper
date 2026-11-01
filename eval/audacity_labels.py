"""Read sparse Audacity activity truth alongside cmd: and take: annotations.

Uncovered activity time is silence; annotations retain their prefixes so frame
evaluation can ignore them and take evaluation can select take: intervals.
"""

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Literal, cast, get_args

from keeper.types import Section


TruthRow = tuple[float, float, str]


@dataclass(frozen=True)
class CommandTruth:
    """An oracle intent parsed independently of pipeline command support."""

    intent: Literal["file", "keep", "reject", "none"]
    section: Section | None = None
    target: Literal["previous", "next"] = "previous"
    note: str | None = None


def parse_command_truth(label: str) -> CommandTruth:
    """Return a parsed cmd: intent with key/value arguments and a trailing free-text note."""
    if not label.startswith("cmd:"):
        raise ValueError("Command truth must start with cmd:")
    fields = label[4:].split()
    if not fields or fields[0] not in ("file", "keep", "reject", "none"):
        raise ValueError("Unknown command truth intent")
    intent = fields[0]
    arguments: dict[str, str] = {}
    # Preserve the original spacing in notes, which consume the rest of the line.
    remainder = label[4 + len(intent):].strip()
    while remainder:
        parts = remainder.split(maxsplit=1)
        token = parts[0]
        rest = parts[1] if len(parts) == 2 else ""
        key, separator, value = token.partition("=")
        if not separator or key not in ("section", "target", "note") or key in arguments:
            raise ValueError(f"Invalid command truth argument: {token}")
        if key == "note":
            arguments[key] = remainder[len("note="):]
            break
        if not value:
            raise ValueError(f"Empty command truth argument: {key}")
        arguments[key] = value
        remainder = rest.lstrip()
    section = arguments.get("section")
    target = arguments.get("target", "previous")
    if section is not None and section not in get_args(Section):
        raise ValueError(f"Unknown command truth section: {section}")
    if target not in ("previous", "next"):
        raise ValueError(f"Unknown command truth target: {target}")
    return CommandTruth(cast(Literal["file", "keep", "reject", "none"], intent),
                        cast(Section | None, section), cast(Literal["previous", "next"], target),
                        arguments.get("note"))


def read_audacity_labels(path: str | Path) -> list[TruthRow]:
    """Return canonical activity intervals (music aliases collapsed), preserving cmd:/take: text."""
    labels: list[TruthRow] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        fields = line.split("\t", maxsplit=2)
        if len(fields) != 3:
            raise ValueError(f"Invalid Audacity label at line {line_number}: expected start, end, text")
        start, end = float(fields[0]), float(fields[1])
        if not isfinite(start) or not isfinite(end) or not 0 <= start <= end:
            raise ValueError(f"Invalid Audacity interval at line {line_number}")
        text = fields[2].strip()
        aliases = {"sing": "singing", "inst": "instrument", "speech": "speech", "unclear": "unclear",
                   "singing": "singing", "instrument": "instrument", "music": "music", "silence": "silence"}
        canonical = aliases.get(text.casefold(), text)
        label = "music" if canonical in ("singing", "instrument") else canonical
        labels.append((start, end, label))
    return labels
