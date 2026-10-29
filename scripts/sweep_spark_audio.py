import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os, io, base64
import soundfile as sf
from dotenv import load_dotenv
from openai import OpenAI
from keeper.audio_io import load_audio

load_dotenv()
client = OpenAI(base_url="https://inference.baseten.co/v1",
                api_key=os.environ["BASETEN_API_KEY"])

SR, WIN = 16000, 2.0
y = load_audio("data/test.wav", SR)
n_windows = int(len(y) // (WIN * SR))          # whole 2 s windows only

for i in range(n_windows):                      # the loop picks every start for you
    start = i * WIN
    clip = y[int(start * SR): int((start + WIN) * SR)]
    buf = io.BytesIO()
    sf.write(buf, clip, SR, format="WAV", subtype="PCM_16")
    url = "data:audio/wav;base64," + base64.b64encode(buf.getvalue()).decode()

    r = client.chat.completions.create(
        model="meta-models/muse-spark-open-1026",
        max_tokens=2000,
        reasoning_effort="low",
        messages=[{"role": "user", "content": [
            {"type": "text", "text": "Classify this clip as exactly one of: singing, speech, instrument, silence. Reply with the single word only."},
            {"type": "audio_url", "audio_url": {"url": url}},
        ]}],
    )
    print(f"{start:5.1f}-{start + WIN:5.1f}s  {r.choices[0].message.content!r}  "
          f"finish={r.choices[0].finish_reason}  reasoning={r.usage.completion_tokens_details.reasoning_tokens}")