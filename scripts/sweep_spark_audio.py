import argparse, csv, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os, io, base64
import soundfile as sf
from dotenv import load_dotenv
from openai import OpenAI
from keeper.audio_io import load_audio

SR, WIN = 16000, 2.0

ap = argparse.ArgumentParser()
ap.add_argument("audio")
ap.add_argument("--start", type=float, default=0.0)
ap.add_argument("--end", type=float, default=None)
ap.add_argument("--stride", type=int, default=1, help="label every Nth window")
ap.add_argument("--max-calls", type=int, default=20)
ap.add_argument("--out", default="outputs/spark_sweep.csv")
args = ap.parse_args()

y = load_audio(args.audio, SR)
end = min(args.end, len(y) / SR) if args.end is not None else len(y) / SR
starts, t = [], args.start
while t + WIN <= end:
    starts.append(t)
    t += WIN * args.stride
if len(starts) > args.max_calls:
    sys.exit(f"Would make {len(starts)} API calls. Rerun with --max-calls {len(starts)} to confirm, or use --stride/--end.")

load_dotenv()
client = OpenAI(base_url="https://inference.baseten.co/v1", api_key=os.environ["BASETEN_API_KEY"])
Path(args.out).parent.mkdir(parents=True, exist_ok=True)

total = 0
with open(args.out, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["start", "end", "label", "finish", "reasoning_tokens", "total_tokens"])
    for s in starts:
        clip = y[int(s * SR): int((s + WIN) * SR)]
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
        d = r.usage.completion_tokens_details
        rt = d.reasoning_tokens if d else None
        row = [s, s + WIN, r.choices[0].message.content, r.choices[0].finish_reason, rt, r.usage.total_tokens]
        w.writerow(row)
        total += r.usage.total_tokens
        print(f"{s:6.1f}-{s + WIN:6.1f}s  {row[2]!r}  finish={row[3]}  reasoning={rt}")
print(f"{len(starts)} calls, {total} total tokens -> {args.out}")