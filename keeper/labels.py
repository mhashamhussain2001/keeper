"""Offline command fixtures and cached, audited Muse Spark audio classification."""

import json
import os
import base64
import hashlib
import io
import re
from time import perf_counter
from typing import Any
from pathlib import Path
from typing import Protocol

import numpy as np
import soundfile as sf
from dotenv import load_dotenv

from keeper.config import MOCK_LABEL_ROUND_S
from keeper.types import CommandLabel, Region


class LabelParser(Protocol):
    """Interface for parsing speech-region audio into structured commands."""

    def parse(self, audio: np.ndarray, sr: int, region: Region) -> CommandLabel:
        """Return the structured command, if any, spoken in this speech-region audio."""
        ...


class MockLabelParser:
    """Resolve deterministic command fixtures by rounded speech start time."""

    def __init__(self, path: str | Path) -> None:
        """Return None after loading the JSON command mapping."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.labels = {float(key): CommandLabel(**value) for key, value in data.items()}

    def parse(self, audio: np.ndarray, sr: int, region: Region) -> CommandLabel:
        """Return the fixture at the nearest half-second or a non-command label."""
        if region.kind != "speech":
            raise ValueError("Only speech regions may be parsed")
        key = round(region.start / MOCK_LABEL_ROUND_S) * MOCK_LABEL_ROUND_S
        return self.labels.get(key, CommandLabel(False, "unsorted", None, None, 0.0))


SPARK_MODEL = "meta-models/muse-spark-open-1026"
SPARK_PROMPT = '''You are listening to a short clip from a songwriter's voice memo. The songwriter may be giving an instruction about the music they just performed or are about to perform, or just talking to themselves, or performing lyrics (rap, spoken word). Classify the clip and reply with ONLY a JSON object, no other text:
{"heard": "<what was said, verbatim as best you can>", "intent": "file" | "keep" | "reject" | "none" | "performance" | "noise", "section": "intro" | "verse" | "pre-chorus" | "chorus" | "bridge" | "hook" | "outro" | null, "target": "previous" | "next", "note": "<extra detail such as 'the higher one', 'chorus B', 'that last one', or null>", "confidence": <0 to 1>}
Definitions: file = names a song section for a take (e.g. "that's the verse", "chorus, the higher one", "bridge idea"). keep = save it without naming a section ("keep that one", "save that"). reject = discard ("no", "nah", "scrap that"). none = talking, thinking out loud, lyric ideas spoken casually. performance = the clip itself is a musical performance (rapping, spoken word, half-sung lyrics). noise = the clip contains no spoken words (breath, chair, cough, room sound, instrument only). target = "next" only if they announce a section BEFORE performing it. Never invent letters or numbers for variants; put any the speaker says into note.'''
SPARK_PROMPT_VERSION = hashlib.sha256((SPARK_MODEL + SPARK_PROMPT).encode()).hexdigest()[:16]


def validate_spark(content: str) -> dict[str, Any]:
    """Return the first decoded JSON object after validating all required Spark fields."""
    if not content.strip():
        raise ValueError("empty content")
    if re.search(r"(?:cannot|can't|unable|not able).{0,100}(?:hear|process|listen|access).{0,60}(?:audio|clip|sound)", content, re.I | re.S):
        raise ValueError("audio refusal")
    start = content.find("{")
    if start < 0:
        raise ValueError("invalid JSON: no object")
    data, _ = json.JSONDecoder().raw_decode(content[start:])
    if not isinstance(data, dict) or set(data) != {"heard", "intent", "section", "target", "note", "confidence"}:
        raise ValueError("invalid fields")
    if data["intent"] not in ("file", "keep", "reject", "none", "performance", "noise"):
        raise ValueError("invalid intent")
    if data["section"] not in (None, "intro", "verse", "pre-chorus", "chorus", "bridge", "hook", "outro"):
        raise ValueError("invalid section")
    if data["intent"] == "file" and data["section"] is None:
        raise ValueError("file intent requires section")
    if data["target"] not in ("previous", "next"):
        raise ValueError("invalid target")
    if not isinstance(data["heard"], str) or (data["note"] is not None and not isinstance(data["note"], str)):
        raise ValueError("invalid text fields")
    if isinstance(data["confidence"], bool) or not isinstance(data["confidence"], (int, float)) or not np.isfinite(data["confidence"]) or not 0 <= data["confidence"] <= 1:
        raise ValueError("invalid confidence")
    return data


def spark_command(data: dict[str, Any]) -> CommandLabel:
    """Return the pipeline mapping of a validated Spark intent, retaining heard and target."""
    intent = data["intent"]
    section = data["section"] if intent == "file" else "rejected" if intent == "reject" else "unsorted"
    return CommandLabel(intent in ("file", "keep", "reject"), section, None, data["note"],
                        float(data["confidence"]), data["heard"], data["target"], intent)


class MuseSparkLabelParser:
    """Cached and audited Baseten audio classification with a per-memo HTTP-call budget."""

    audio_padding_s = 0.3

    def __init__(self, cache_path: str | Path = "outputs/spark_cache.json", max_spark_calls: int = 40,
                 client: Any = None) -> None:
        """Return None after loading cached results without creating a client or making network calls."""
        self.cache_path = Path(cache_path)
        self.entries = json.loads(self.cache_path.read_text(encoding="utf-8")) if self.cache_path.exists() else {}
        self.client = client
        self.max_spark_calls = max_spark_calls
        self.calls_made = 0
        self.previous_calls = sum(len(row["attempts"]) for row in self.entries.values())
        self.records: list[dict[str, Any]] = []
        self.use_extra_body = False
        self.temperature = True

    def _audio_key(self, audio: np.ndarray, sr: int, region: Region) -> tuple[bytes, str]:
        """Return PCM-16 WAV bytes and their region/audio/prompt-specific cache key."""
        if sr != 16000 or audio.ndim != 1 or audio.dtype != np.float32 or not np.isfinite(audio).all():
            raise ValueError("Spark audio must be finite mono float32 at 16000 Hz")
        buf = io.BytesIO()
        sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
        wav = buf.getvalue()
        key = json.dumps([region.start, region.end, hashlib.sha1(wav).hexdigest(), SPARK_PROMPT_VERSION])
        return wav, key

    def preflight(self, clips: list[tuple[np.ndarray, int, Region]]) -> None:
        """Return None or raise before processing if uncached regions exceed the remaining call budget."""
        missing = {self._audio_key(audio, sr, region)[1] for audio, sr, region in clips} - self.entries.keys()
        if self.previous_calls + self.calls_made + len(missing) > self.max_spark_calls:
            raise RuntimeError(f"Spark cost guard: {len(missing)} uncached regions + {self.previous_calls + self.calls_made} prior calls exceed max_spark_calls={self.max_spark_calls}")

    def parse(self, audio: np.ndarray, sr: int, region: Region) -> CommandLabel:
        """Return a cached or retried Spark command and persist its complete attempt audit."""
        wav, key = self._audio_key(audio, sr, region)
        if key in self.entries:
            self.records.append({**self.entries[key], "cached": True})
            return spark_command(self.entries[key]["parsed"])
        if self.previous_calls + self.calls_made >= self.max_spark_calls:
            raise RuntimeError(f"Spark cost guard: {self.previous_calls + self.calls_made + 1} uncached calls exceed max_spark_calls={self.max_spark_calls}")
        if self.client is None:
            load_dotenv()
            from openai import OpenAI
            self.client = OpenAI(base_url="https://inference.baseten.co/v1", api_key=os.environ["BASETEN_API_KEY"], max_retries=0, timeout=120)
        attempts = []
        parsed = {"heard": "", "intent": "none", "section": None, "target": "previous", "note": None, "confidence": 0.0}
        for attempt in range(2):
            options: dict[str, Any] = {"model": SPARK_MODEL, "max_tokens": 2000, "messages": [{"role": "user", "content": [
                {"type": "text", "text": SPARK_PROMPT}, {"type": "audio_url", "audio_url": {"url": "data:audio/wav;base64," + base64.b64encode(wav).decode()}}]}]}
            options.update({"extra_body": {"reasoning_effort": "low"}} if self.use_extra_body else {"reasoning_effort": "low"})
            if self.temperature:
                options["temperature"] = 0
            while True:
                if self.previous_calls + self.calls_made >= self.max_spark_calls:
                    raise RuntimeError(f"Spark cost guard: {self.previous_calls + self.calls_made + 1} uncached calls exceed max_spark_calls={self.max_spark_calls}")
                started = perf_counter()
                item: dict[str, Any] = {"region_start": region.start, "region_end": region.end, "raw_content": "", "parsed": None,
                                        "finish_reason": None, "reasoning_tokens": 0, "total_tokens": 0, "retries": attempt, "failure_reason": None}
                try:
                    self.calls_made += 1
                    response = self.client.chat.completions.create(**options)
                    choice = response.choices[0]
                    item.update(raw_content=choice.message.content or "", finish_reason=choice.finish_reason,
                                reasoning_tokens=getattr(getattr(response.usage, "completion_tokens_details", None), "reasoning_tokens", 0) or 0,
                                total_tokens=getattr(response.usage, "total_tokens", 0) or 0)
                    if choice.finish_reason == "length":
                        raise ValueError("finish_reason=length")
                    opening = item["raw_content"].find("{")
                    if opening >= 0:
                        item["parsed"], _ = json.JSONDecoder().raw_decode(item["raw_content"][opening:])
                    parsed = validate_spark(item["raw_content"])
                    item["parsed"] = parsed
                except Exception as exc:
                    item["failure_reason"] = f"{type(exc).__name__}: {exc}"
                    if getattr(exc, "status_code", None) in (401, 403):
                        raise
                    text = str(exc).lower()
                    if "reasoning_effort" in text and (isinstance(exc, TypeError) or getattr(exc, "status_code", None) == 400) and not self.use_extra_body:
                        self.use_extra_body = True
                        options.pop("reasoning_effort", None)
                        options["extra_body"] = {"reasoning_effort": "low"}
                        item["latency_s"] = perf_counter() - started
                        attempts.append(item)
                        continue
                    if "temperature" in text and self.temperature and (isinstance(exc, TypeError) or getattr(exc, "status_code", None) == 400):
                        self.temperature = False
                        options.pop("temperature", None)
                        item["latency_s"] = perf_counter() - started
                        attempts.append(item)
                        continue
                item["latency_s"] = perf_counter() - started
                attempts.append(item)
                break
            if item["failure_reason"] is None:
                break
        record = {"start": region.start, "end": region.end, "key": key, "parsed": parsed,
                  "attempts": attempts, "retries": max(0, len(attempts) - 1), "failure_reason": attempts[-1]["failure_reason"]}
        self.entries[key] = record
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.entries, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.cache_path)
        self.records.append({**record, "cached": False})
        return spark_command(parsed)
