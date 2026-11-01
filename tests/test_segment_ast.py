"""Optional offline CUDA/CPU equivalence check using the installed AST model cache."""

import numpy as np
import pytest
import torch

from keeper.segment_ast import ASTScorer


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA device is unavailable")
def test_cuda_scores_match_cpu() -> None:
    """Return None after checking full-precision CUDA batches of 32 match CPU within 1e-4."""
    y = (0.1 * np.sin(2 * np.pi * 220 * np.arange(68000) / 16000)).astype(np.float32)
    cpu = ASTScorer(device="cpu")
    expected = cpu.score_windows(y, 16000, 2.0, 0.5)
    gpu = ASTScorer(device="cuda")
    assert gpu.batch_size == 32
    actual = gpu.score_windows(y, 16000, 2.0, 0.5)
    np.testing.assert_allclose(actual.to_numpy(), expected.to_numpy(), atol=1e-4, rtol=0)
