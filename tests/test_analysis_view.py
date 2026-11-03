"""Saved command projection excludes cache internals and alternate speech regions."""

import json
from pathlib import Path

from app.analysis_view import command_analysis


def test_analysis_saved_regions_and_filed_transcripts(tmp_path: Path) -> None:
    """Return None after checking safe display fields, chained commands, and unmatched cache regions."""
    parsed = {'heard': 'bridge idea', 'intent': 'file', 'section': 'bridge', 'target': 'next', 'confidence': .9}
    cache = {'current': {'start': 3, 'end': 4, 'parsed': parsed, 'key': 'private cache key', 'attempts': ['private raw record']},
             'alternate': {'start': 2, 'end': 4, 'parsed': parsed},
             'talking': {'start': 8, 'end': 9, 'parsed': {**parsed, 'heard': 'thinking', 'intent': 'none'}}}
    (tmp_path/'spark_cache.json').write_text(json.dumps(cache))
    songmap = {'sections': {'bridge': [{'id': 'bridge-1', 'heard': 'Okay, bridge idea; Idea for a bridge'}]}}
    regions = [{'start': 3, 'end': 4, 'kind': 'speech'}, {'start': 8, 'end': 9, 'kind': 'speech'}]
    result = command_analysis(tmp_path, songmap, regions)
    assert len(result['commands']) == 2
    assert result['commands'][0] == {'start': 3, 'end': 4, **parsed, 'take_ids': ['bridge-1']}
    assert result['commands'][1]['take_ids'] == []
    assert 'private' not in json.dumps(result)
    # A deleted/re-filed take is never invented by nearest-neighbor matching.
    assert command_analysis(tmp_path, {'sections': {}}, regions)['commands'][0]['take_ids'] == []


def test_analysis_missing_or_invalid_cache(tmp_path: Path) -> None:
    """Return None after checking older memos and incomplete cache files yield no commands."""
    regions = [{'start': 0, 'end': 1, 'kind': 'speech'}]
    assert command_analysis(tmp_path, {}, regions) == {'commands': []}
    for content in ('{', '[]', '{}'):
        (tmp_path/'spark_cache.json').write_text(content)
        assert command_analysis(tmp_path, {'sections': {}}, regions) == {'commands': []}
