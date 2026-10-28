"""Section grouping, unique naming, and serialization tests."""

from pathlib import Path

from keeper.songmap import build_songmap
from keeper.types import CommandLabel, SongMap, Take


def _take() -> Take:
    """Return a provisional take for song map naming tests."""
    return Take("", "", "", 0.0, 5.0, "memo.wav", None, None)


def test_unsorted_command() -> None:
    """Return None after checking keep-style commands retain notes in the unsorted section."""
    label = CommandLabel(True, "unsorted", None, "keep that one", 1.0)
    songmap = build_songmap([(_take(), label)], "memo.wav")
    take = songmap.sections["unsorted"][0]
    assert (take.section, take.variant, take.note, take.id) == (
        "unsorted", "Take 1", "keep that one", "unsorted-take-1",
    )


def test_auto_numbering_is_section_local_and_counts_named_takes() -> None:
    """Return None after checking automatic variants count all previous takes in their section."""
    labels = [CommandLabel(True, "chorus", "B", None, 1.0),
              CommandLabel(True, "verse", None, None, 1.0),
              CommandLabel(True, "chorus", None, None, 1.0),
              CommandLabel(True, "verse", None, None, 1.0)]
    songmap = build_songmap([(_take(), label) for label in labels], "memo.wav")
    assert [take.variant for take in songmap.sections["chorus"]] == ["B", "Take 2"]
    assert [take.variant for take in songmap.sections["verse"]] == ["Take 1", "Take 2"]


def test_duplicate_variants_are_retained() -> None:
    """Return None after checking repeated options receive numbered suffixes and unique IDs."""
    label = CommandLabel(True, "chorus", "B", None, 1.0)
    songmap = build_songmap([(_take(), label) for _ in range(3)], "memo.wav")
    assert [take.variant for take in songmap.sections["chorus"]] == ["B", "B (2)", "B (3)"]
    assert [take.id for take in songmap.sections["chorus"]] == ["chorus-b", "chorus-b-2", "chorus-b-3"]


def test_slug_collisions_still_have_unique_ids() -> None:
    """Return None after checking punctuation differences cannot collide in exported filenames."""
    labels = [CommandLabel(True, "chorus", variant, None, 1.0) for variant in ("A!", "A?", "A (2)")]
    songmap = build_songmap([(_take(), label) for label in labels], "memo.wav")
    ids = [take.id for take in songmap.sections["chorus"]]
    assert len(ids) == len(set(ids))


def test_json_round_trip(tmp_path: Path) -> None:
    """Return None after checking all nested take fields survive a JSON round trip."""
    label = CommandLabel(True, "pre-chorus", "B", "gentler ending", 0.9)
    songmap = build_songmap([(_take(), label)], "memo.wav")
    path = tmp_path / "songmap.json"
    songmap.to_json(path)
    restored = SongMap.from_json(path)
    assert restored == songmap
    assert restored.to_dict() == songmap.to_dict()
    assert label.to_dict()["note"] == "gentler ending"
