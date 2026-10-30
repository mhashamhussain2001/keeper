import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # add repo root

import os, io, base64
import soundfile as sf
from dotenv import load_dotenv
from openai import OpenAI
from keeper.audio_io import load_audio

load_dotenv()
client = OpenAI(base_url="https://inference.baseten.co/v1",
                api_key=os.environ["BASETEN_API_KEY"])

start = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
y = load_audio("data/test.wav", 16000)[int(start*16000):int((start+2)*16000)]
buf = io.BytesIO()
sf.write(buf, y, 16000, format="WAV", subtype="PCM_16")    # WAV bytes in memory
url = "data:audio/wav;base64," + base64.b64encode(buf.getvalue()).decode()

r = client.chat.completions.create(
    model="meta-models/muse-spark-open-1026",
    max_tokens=2000,
    messages=[{"role": "user", "content": [
        {"type": "text", "text": "Classify this clip as exactly one of: singing, speech, instrument, silence. Reply with the single word only."},
        {"type": "audio_url", "audio_url": {"url": url}},
    ]}],
    reasoning_effort="low",
)

print(f"start={start}s -> {r.choices[0].message.content!r} | finish={r.choices[0].finish_reason} | reasoning_tokens={r.usage.completion_tokens_details.reasoning_tokens}")