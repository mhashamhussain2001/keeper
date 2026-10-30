"""Backward take selection from spoken command boundaries."""

from keeper.config import KeeperConfig
from keeper.types import CommandLabel, Region, Take


def cut_takes(
    regions: list[Region],
    command_labels: dict[float, CommandLabel],
    source_file: str,
    cfg: KeeperConfig,
) -> tuple[list[tuple[Take, CommandLabel]], list[str]]:
    """Return provisional takes paired with commands and warnings for commands without music."""
    takes: list[tuple[Take, CommandLabel]] = []
    warnings: list[str] = []
    previous_end = 0.0
    for index, command_region in enumerate(regions):
        label = command_labels.get(command_region.start)
        if command_region.kind != "speech" or label is None or not label.is_command:
            continue
        music: list[Region] = []
        boundary = previous_end
        for region in reversed(regions[:index]):
            if region.end <= previous_end:
                boundary = previous_end
                break
            if region.kind == "speech" or (
                region.kind == "silence" and region.end >= region.start + cfg.max_gap_s
            ):
                boundary = max(previous_end, region.end)
                break
            if region.kind == "music":
                music.append(region)
            if region.start <= previous_end:
                boundary = previous_end
                break
        if not music:
            warnings.append(f"command at {command_region.start}s had no preceding music")
            continue
        start = float(max(boundary, music[-1].start - cfg.pre_roll_s))
        end = float(min(command_region.start, music[0].end + cfg.post_roll_s))
        take = Take("", label.section or "unsorted", label.variant or "", start, end, source_file, label.note, None)
        takes.append((take, label))
        previous_end = end
    return takes, warnings
