"""Pipeline configuration and shared numeric conventions."""

from dataclasses import dataclass, field
from math import isfinite
from typing import Literal


AUDIO_SAMPLE_RATE = 16000
AST_MODEL_ID = "MIT/ast-finetuned-audioset-10-10-0.4593"
AST_BATCH_SIZE = 8
RMS_EPSILON = 1e-10
DB_AMPLITUDE_FACTOR = 20.0
MILLISECONDS_PER_SECOND = 1000.0
MOCK_LABEL_ROUND_S = 0.5
FRAME_KINDS = ("music", "speech", "silence")
SCORE_GROUPS = {
    "singing": ("Singing", "Male singing", "Female singing", "Child singing", "Humming", "Rapping"),
    "speech": ("Speech", "Male speech, man speaking", "Female speech, woman speaking", "Conversation", "Narration, monologue"),
    "instrument": ("Music", "Musical instrument", "Guitar", "Acoustic guitar", "Piano", "Keyboard (musical)"),
}


def _default_min_region_s() -> dict[str, float]:
    """Return a fresh mapping of minimum region durations in seconds."""
    return {"music": 1.0, "speech": 0.5, "silence": 0.5}


@dataclass(frozen=True)
class KeeperConfig:
    """Settings shared by all stages of the Keeper pipeline."""

    sample_rate: int = AUDIO_SAMPLE_RATE
    win_s: float = 2.0
    hop_s: float = 0.5
    silence_db: float = -45.0
    speech_min: float = 0.40
    silence_mode: Literal["relative", "absolute"] = "relative"
    noise_floor_pct: float = 10.0
    silence_margin_db: float = 12.0
    min_dynamic_range_db: float = 20.0
    gate_clamp_db: tuple[float, float] = (-60.0, -30.0)
    island_max_s: float = 8.0
    island_vote_min: float = 0.6
    min_unlabeled_s: float = 3.0
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
        if not isfinite(self.silence_db) or not 0 <= self.speech_min <= 1:
            raise ValueError("Invalid silence_db or speech_min")
        if self.silence_mode not in ("relative", "absolute"):
            raise ValueError("silence_mode must be relative or absolute")
        if not 0 <= self.noise_floor_pct <= 100 or not 0.5 < self.island_vote_min <= 1:
            raise ValueError("Invalid noise_floor_pct or island_vote_min")
        if len(self.gate_clamp_db) != 2 or not all(isfinite(value) for value in self.gate_clamp_db):
            raise ValueError("gate_clamp_db must contain two finite bounds")
        if self.gate_clamp_db[0] > self.gate_clamp_db[1]:
            raise ValueError("gate_clamp_db bounds must be ordered")
        if set(self.min_region_s) != set(FRAME_KINDS):
            raise ValueError("min_region_s must specify every frame kind")
        durations = (*self.min_region_s.values(), self.max_gap_s,
                     self.pre_roll_s, self.post_roll_s, self.fade_ms, self.silence_margin_db,
                     self.min_dynamic_range_db, self.island_max_s, self.min_unlabeled_s)
        if any(not isfinite(value) or value < 0 for value in durations):
            raise ValueError("Region, gap, roll, and fade durations must be nonnegative and finite")
