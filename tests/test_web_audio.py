"""Run the dependency-free JavaScript transport regression tests as part of the full suite."""

import subprocess
from pathlib import Path


def test_audio_transport_gestures_pause_seek_fallback_and_interruptions() -> None:
    """Return None after verifying browser transport behavior with mocked audio and fetch, without network."""
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests" / "audio_transport.cjs"), str(root / "tests" / "web_ui.cjs"),
                                 str(root / "tests" / "ios_silent.cjs"), str(root / "tests" / "wav_encoder.cjs")], cwd=root,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
