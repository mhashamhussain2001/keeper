"""Selection of music stretches not claimed by spoken commands."""

from keeper.config import KeeperConfig
from keeper.types import CommandLabel, Region, Take


def find_unlabeled(
    regions: list[Region], command_takes: list[Take], source_file: str, cfg: KeeperConfig,
) -> list[tuple[Take, CommandLabel]]:
    """Return time-ordered unlabeled takes spanning unclaimed music and short silence gaps."""
    takes: list[tuple[Take, CommandLabel]] = []
    index = 0
    while index < len(regions):
        if regions[index].kind != "music":
            index += 1
            continue
        first = last = index
        music_duration = regions[index].end - regions[index].start
        cursor = index + 1
        while cursor < len(regions):
            region = regions[cursor]
            if region.kind == "speech" or (
                region.kind == "silence" and region.end >= region.start + cfg.max_gap_s
            ):
                break
            if region.kind == "music":
                last = cursor
                music_duration += region.end - region.start
            cursor += 1
        index = cursor
        # Rolls can enter neighboring silence, but never cross its far edge or enter speech.
        left = regions[first - 1].start if first and regions[first - 1].kind == "silence" else regions[first].start
        right = (regions[last + 1].end if last + 1 < len(regions)
                 and regions[last + 1].kind == "silence" else regions[last].end)
        start = float(max(left, regions[first].start - cfg.pre_roll_s))
        end = float(min(right, regions[last].end + cfg.post_roll_s))
        if music_duration < cfg.min_unlabeled_s:
            continue
        if any(start < take.end and end > take.start for take in command_takes):
            continue
        variant = f"Take {len(takes) + 1}"
        take = Take("", "unlabeled", variant, start, end, source_file, None, None)
        takes.append((take, CommandLabel(False, "unlabeled", variant, None, 0.0)))
    return takes
