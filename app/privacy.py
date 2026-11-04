"""Remove private recording identifiers from client-facing memo artifacts."""

from typing import Any


def public_artifact(value: Any, original_filename: str = "") -> Any:
    """Return a recursive copy without source filenames, redacting legacy filename strings."""
    if isinstance(value, dict):
        return {key: public_artifact(item, original_filename) for key, item in value.items()
                if key not in {"filename", "original_filename", "source_file"}}
    if isinstance(value, list):
        return [public_artifact(item, original_filename) for item in value]
    if isinstance(value, str) and original_filename:
        return value.replace(original_filename, "recording")
    return value
