"""Optional local AST scoring behind a scorer protocol."""

from math import ceil
from typing import Protocol

import numpy as np
import pandas as pd

from keeper.config import AST_BATCH_SIZE, AST_MODEL_ID, SCORE_GROUPS, KeeperConfig


class Scorer(Protocol):
    """Interface implemented by interchangeable window classifiers."""

    def score_windows(self, y: np.ndarray, sr: int, win_s: float, hop_s: float) -> pd.DataFrame:
        """Return window timestamps, group probabilities, and raw label probabilities."""
        ...


class ASTScorer:
    """AudioSet AST inference with explicit label validation and sigmoid scores."""

    def __init__(self) -> None:
        """Return None after loading AST and validating every required AudioSet label."""
        from transformers import ASTFeatureExtractor, ASTForAudioClassification

        # Loading is deliberately deferred until this optional scorer is constructed.
        # Only cached artifacts are permitted: this scaffold never downloads a model.
        self.extractor = ASTFeatureExtractor.from_pretrained(AST_MODEL_ID, local_files_only=True)
        self.model = ASTForAudioClassification.from_pretrained(AST_MODEL_ID, local_files_only=True)
        label_indices = {label: int(index) for index, label in self.model.config.id2label.items()}
        missing = sorted({label for labels in SCORE_GROUPS.values() for label in labels} - label_indices.keys())
        if missing:
            raise ValueError("Missing AST labels: " + ", ".join(missing))
        self.label_indices = label_indices
        self.model.eval()

    def score_windows(self, y: np.ndarray, sr: int, win_s: float, hop_s: float) -> pd.DataFrame:
        """Return scores for hop-spaced windows with a zero-padded final window."""
        import torch

        if y.ndim != 1 or y.dtype != np.float32:
            raise ValueError("Audio must be a mono float32 array")
        if sr != KeeperConfig().sample_rate:
            raise ValueError("Keeper audio must use 16000 Hz")
        if any(not np.isfinite(value) or value * sr < 1 for value in (win_s, hop_s)):
            raise ValueError("Window and hop must each span at least one sample")
        raw_labels = list(dict.fromkeys(label for labels in SCORE_GROUPS.values() for label in labels))
        columns = ["start", "end", *SCORE_GROUPS, "music", *raw_labels]
        rows: list[dict[str, float]] = []
        window_count = ceil(len(y) / (hop_s * sr))
        window_samples = round(win_s * sr)
        for batch_start in range(0, window_count, AST_BATCH_SIZE):
            indices = range(batch_start, min(batch_start + AST_BATCH_SIZE, window_count))
            windows = []
            for index in indices:
                start = round(index * hop_s * sr)
                window = y[start:start + window_samples]
                windows.append(np.pad(window, (0, window_samples - len(window))))
            inputs = self.extractor(windows, sampling_rate=sr, return_tensors="pt")
            with torch.no_grad():
                probabilities = torch.sigmoid(self.model(**inputs).logits).cpu().numpy()
            for index, probabilities_row in zip(indices, probabilities):
                row = {"start": float(index * hop_s), "end": float(index * hop_s + win_s)}
                row.update({label: float(probabilities_row[self.label_indices[label]]) for label in raw_labels})
                row.update({group: max(row[label] for label in labels) for group, labels in SCORE_GROUPS.items()})
                row["music"] = max(row["singing"], row["instrument"])
                rows.append(row)
        return pd.DataFrame(rows, columns=columns)
