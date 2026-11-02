"""Directional take selection and optional last-attempt selection from command boundaries."""

from keeper.config import KeeperConfig
from keeper.commands import chain_commands
from keeper.types import CommandLabel, Region, Take


def cut_takes(
    regions: list[Region],
    command_labels: dict[float, CommandLabel],
    source_file: str,
    cfg: KeeperConfig,
) -> tuple[list[tuple[Take, CommandLabel]], list[str]]:
    """Return provisional takes paired with commands and warnings for commands without music."""
    if cfg.merge_adjacent_commands:
        regions, command_labels = chain_commands(regions, command_labels)
    takes: list[tuple[Take, CommandLabel]] = []
    warnings: list[str] = []
    previous_end = 0.0
    forward_claims: list[tuple[float, float]] = []
    for index, command_region in enumerate(regions):
        label = command_labels.get(command_region.start)
        if command_region.kind != "speech" or label is None or not label.is_command:
            continue
        forward = cfg.support_target_next and label.target == "next"
        music: list[Region] = []
        boundary = previous_end
        if forward:
            boundary = regions[-1].end
        candidates = regions[index + 1:] if forward else list(reversed(regions[:index]))
        for region in candidates:
            if forward:
                if region.kind == "speech" or (region.kind == "silence" and region.end - region.start >= cfg.max_gap_s):
                    boundary = region.start
                    break
                if region.kind == "music":
                    music.append(region)
                continue
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
            direction = "following" if forward else "preceding"
            warnings.append(f"command at {command_region.start}s had no {direction} music")
            continue
        music.sort(key=lambda region: region.start)
        if not forward and any(min(region.end, end) > max(region.start, start)
                               for region in music for start, end in forward_claims):
            warnings.append(f"command at {command_region.start}s overlaps music already claimed by a forward command")
            continue
        lower = command_region.end if forward else boundary
        upper = boundary if forward else command_region.start
        if cfg.support_last_attempt and label.scope == "last_attempt":
            pauses = [region for region in regions if region.kind == "silence"
                      and region.start >= music[0].end and region.end <= music[-1].start
                      and region.end - region.start >= cfg.attempt_gap_s]
            if pauses:
                lower = max(lower, pauses[-1].end)
                music = [region for region in music if region.start >= lower]
            else:
                warnings.append("last attempt requested but no pause found")
        start = float(max(lower, music[0].start - cfg.pre_roll_s))
        end = float(min(upper, music[-1].end + cfg.post_roll_s))
        take = Take("", label.section or "unsorted", label.variant or "", start, end, source_file, label.note, None)
        takes.append((take, label))
        if forward:
            forward_claims.append((start, end))
        else:
            previous_end = end
    return takes, warnings
