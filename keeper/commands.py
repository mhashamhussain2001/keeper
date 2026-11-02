"""Combine repeated same-section commands for take selection only."""

from dataclasses import replace

from keeper.types import CommandLabel, Region


def chain_commands(regions: list[Region], commands: dict[float, CommandLabel]) -> tuple[list[Region], dict[float, CommandLabel]]:
    """Return a cutting-only timeline with consecutive same-section commands joined across silence."""
    result: list[Region] = []
    labels = dict(commands)
    index = 0
    while index < len(regions):
        first = regions[index]
        label = commands.get(first.start)
        if first.kind != "speech" or label is None or not label.is_command:
            result.append(first)
            index += 1
            continue
        end_index = index
        combined = label
        while True:
            following = end_index + 1
            while following < len(regions) and regions[following].kind == "silence":
                following += 1
            if following >= len(regions) or regions[following].kind != "speech":
                break
            other = commands.get(regions[following].start)
            if other is None or not other.is_command or other.section != label.section:
                break
            combined = replace(other, target="next" if "next" in (combined.target, other.target) else "previous",
                               heard="; ".join(text for text in (combined.heard, other.heard) if text) or None)
            labels.pop(regions[following].start, None)
            end_index = following
        result.append(replace(first, end=regions[end_index].end))
        labels[first.start] = combined
        index = end_index + 1
    return result, labels
