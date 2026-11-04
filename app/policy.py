"""Read the deployment's upload, cost and retention policy from environment variables."""

from dataclasses import dataclass
import math
import os


@dataclass(frozen=True)
class Policy:
    """Configured upload admission and retention limits."""

    max_audio_minutes: float = 30.0
    max_upload_mb: float = 150.0
    owner_daily_uploads: int = 5
    owner_daily_minutes: float = 90.0
    daily_minutes: float = 240.0
    retention_days: int = 14
    uploads_enabled: bool = True

    @property
    def limits_message(self) -> str:
        """Return the shared friendly limits message used for rejected uploads."""
        return f"Up to {self.max_audio_minutes:g} minutes and {self.max_upload_mb:g} MB. Trim the memo and try again."

    @property
    def max_upload_bytes(self) -> int:
        """Return the maximum copied upload size in bytes."""
        return int(self.max_upload_mb * 1024 * 1024)


def policy() -> Policy:
    """Return validated policy values, honoring environment changes without retaining sensitive state."""
    values = {}
    for key, default in Policy().__dict__.items():
        raw = os.environ.get("KEEPER_" + key.upper())
        if raw is None:
            values[key] = default
        elif key == "uploads_enabled":
            values[key] = raw.strip().lower() not in {"0", "false", "off"}
        else:
            number = float(raw)
            if not math.isfinite(number) or number < 0 or (key.startswith("max_") and number <= 0):
                raise ValueError("Invalid upload policy configuration")
            if isinstance(default, int) and not number.is_integer():
                raise ValueError("Invalid integer upload policy configuration")
            values[key] = int(number) if isinstance(default, int) else number
    return Policy(**values)
