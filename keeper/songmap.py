"""Section grouping, variant naming, and stable take identifiers."""

import re
import unicodedata
from dataclasses import replace

from keeper.types import CommandLabel, SongMap, Take


def _slugify(value: str) -> str:
    """Return a lowercase ASCII slug safe for use as a clip filename."""
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-") or "take"


def build_songmap(takes_with_labels: list[tuple[Take, CommandLabel]], source_file: str) -> SongMap:
    """Return a song map with section-local variants and unique slugified take identifiers."""
    sections: dict[str, list[Take]] = {}
    used_ids: set[str] = set()
    for take, label in takes_with_labels:
        section = label.section or "unsorted"
        section_takes = sections.setdefault(section, [])
        base_variant = label.variant if label.variant is not None else f"Take {len(section_takes) + 1}"
        variant = base_variant
        used_variants = {existing.variant for existing in section_takes}
        occurrence = 1
        while variant in used_variants:
            occurrence += 1
            variant = f"{base_variant} ({occurrence})"
        base_id = _slugify(f"{section}-{variant}")
        take_id = base_id
        occurrence = 1
        while take_id in used_ids:
            occurrence += 1
            take_id = f"{base_id}-{occurrence}"
        used_ids.add(take_id)
        section_takes.append(replace(take, id=take_id, section=section, variant=variant,
                                     source_file=source_file, note=label.note))
    return SongMap(source_file, sections)
