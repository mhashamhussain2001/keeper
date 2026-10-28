"""Pipeline configuration and shared numeric conventions."""

from dataclasses import dataclass, field
from math import isfinite


AUDIO_SAMPLE_RATE = 16000
AST_MODEL_ID = "MIT/ast-finetuned-audioset-10-10-0.4593"
AST_BATCH_SIZE = 8
RMS_EPSILON = 1e-10
DB_AMPLITUDE_FACTOR = 20.0
MILLISECONDS_PER_SECOND = 1000.0
MOCK_LABEL_ROUND_S = 0.5
FRAME_KINDS = ("singing", "speech", "instrument", "silence")
SCORE_GROUPS = {
    "singing": ("Singing", "Male singing", "Female singing", "Child singing", "Humming", "Rapping"),
    "speech": ("Speech", "Male speech, man speaking", "Female speech, woman speaking", "Conversation", "Narration, monologue"),
    "instrument": ("Music", "Musical instrument", "Guitar", "Acoustic guitar", "Piano", "Keyboard (musical)"),
}


def _default_min_region_s() -> dict[str, float]:
    """Return a fresh mapping of minimum region durations in seconds."""
    return {"singing": 2.0, "speech": 0.5, "instrument": 1.0, "silence": 0.5}


@dataclass(frozen=True)
class KeeperConfig:
    """Settings shared by all stages of the Keeper pipeline."""

    sample_rate: int = AUDIO_SAMPLE_RATE
    win_s: float = 2.0
    hop_s: float = 0.5
    silence_db: float = -45.0
    voice_min: float = 0.20
    mode_filter_frames: int = 3
    min_region_s: dict[str, float] = field(default_factory=_default_min_region_s)
    max_gap_s: float = 4.0
    pre_roll_s: float = 0.3
    post_roll_s: float = 0.5
    fade_ms: float = 10.0

    def __post_init__(self) -> None:
        """Return None after rejecting invalid pipeline settings."""
        if self.sample_rate != AUDIO_SAMPLE_RATE:
            raise ValueError("Keeper audio must use 16000 Hz")
        if any(not isfinite(value) or value <= 0 for value in (self.win_s, self.hop_s)):
            raise ValueError("win_s and hop_s must be positive and finite")
        if self.hop_s * self.sample_rate < 1:
            raise ValueError("hop_s must span at least one sample")
        if self.mode_filter_frames < 1 or self.mode_filter_frames % 2 != 1:
            raise ValueError("mode_filter_frames must be positive and odd")
        if not isfinite(self.silence_db) or not 0 <= self.voice_min <= 1:
            raise ValueError("Invalid silence_db or voice_min")
        if set(self.min_region_s) != set(FRAME_KINDS):
            raise ValueError("min_region_s must specify every frame kind")
        durations = (*self.min_region_s.values(), self.max_gap_s,
                     self.pre_roll_s, self.post_roll_s, self.fade_ms)
        if any(not isfinite(value) or value < 0 for value in durations):
            raise ValueError("Region, gap, roll, and fade durations must be nonnegative and finite")
