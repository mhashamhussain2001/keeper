"""Serve local memo artifacts and run uploads through the existing mock pipeline."""

import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from keeper.labels import MockLabelParser
from keeper.pipeline import run_pipeline


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    """Filesystem locations for the local web app."""

    outputs_root: Path = PROJECT_ROOT / "outputs"
    uploads_root: Path = PROJECT_ROOT / "data" / "uploads"
    static_root: Path = PROJECT_ROOT / "app" / "static"


settings = Settings()
app = FastAPI(title="Keeper")
logger = logging.getLogger(__name__)


def _within(root: Path, path: Path) -> Path:
    """Return a resolved child path or raise 404 for paths outside its root."""
    resolved = path.resolve()
    if resolved == root.resolve() or not resolved.is_relative_to(root.resolve()):
        raise HTTPException(status_code=404, detail="Not found")
    return resolved


def _memo_dir(memo_id: str) -> Path:
    """Return a memo directory containing a song map or raise 404."""
    if not memo_id or memo_id in (".", "..") or any(char in memo_id for char in "/\\:"):
        raise HTTPException(status_code=404, detail="Memo not found")
    directory = _within(settings.outputs_root, settings.outputs_root / memo_id)
    if not _within(directory, directory / "songmap.json").is_file():
        raise HTTPException(status_code=404, detail="Memo not found")
    return directory


def _read_json(path: Path) -> Any:
    """Return a parsed JSON artifact or raise a clear server error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Cannot read memo artifact {path.name}") from exc


@app.get("/api/memos")
def list_memos() -> list[str]:
    """Return sorted memo folder IDs with a songmap.json artifact."""
    if not settings.outputs_root.is_dir():
        return []
    memos = []
    for directory in settings.outputs_root.iterdir():
        try:
            _memo_dir(directory.name)
        except HTTPException:
            continue
        memos.append(directory.name)
    return sorted(memos)


@app.get("/api/memos/{memo_id}")
def memo_detail(memo_id: str) -> dict[str, Any]:
    """Return song map, regions, memo duration, and guarded relative clip URLs."""
    directory = _memo_dir(memo_id)
    songmap = _read_json(directory / "songmap.json")
    metadata = _read_json(_within(directory, directory / "regions.json"))
    regions = metadata["regions"] if isinstance(metadata, dict) else metadata
    for takes in songmap["sections"].values():
        for take in takes:
            take["clip_url"] = None
            clip_path = take.get("clip_path")
            if not clip_path:
                continue
            # Current exports use absolute paths; older artifacts may be relative.
            candidate = Path(clip_path.replace("\\", "/"))
            clip = _within(directory, candidate if candidate.is_absolute() else directory / candidate)
            relative = clip.relative_to(directory).as_posix()
            take["clip_path"] = relative
            take["clip_url"] = f"/api/memos/{quote(memo_id, safe='')}/clips/{quote(relative, safe='/')}"
    duration = max((float(region["end"]) for region in regions), default=0.0)
    return {"songmap": songmap, "regions": regions, "duration_s": duration}


@app.get("/api/memos/{memo_id}/clips/{path:path}")
def serve_clip(memo_id: str, path: str) -> FileResponse:
    """Return a WAV inside this memo folder or raise 404 for invalid paths."""
    directory = _memo_dir(memo_id)
    clip = _within(directory, directory / path.replace("\\", "/"))
    if clip.suffix.lower() != ".wav" or not clip.is_file():
        raise HTTPException(status_code=404, detail="Clip not found")
    return FileResponse(clip, media_type="audio/wav")


@app.post("/api/memos", status_code=201)
def upload_memo(file: Annotated[UploadFile, File()]) -> dict[str, str]:
    """Return the new memo ID after saving and synchronously processing an upload."""
    filename = Path((file.filename or "memo.wav").replace("\\", "/")).name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aif", ".aiff"}:
        raise HTTPException(status_code=400, detail="Upload a WAV, MP3, M4A, FLAC, OGG, or AIFF audio file")
    stem = re.sub(r"[^a-z0-9]+", "-", Path(filename).stem.lower()).strip("-")[:64] or "memo"
    memo_id = f"{stem}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    destination = settings.outputs_root / memo_id
    source = settings.uploads_root / f"{memo_id}{suffix}"
    created = False
    try:
        settings.uploads_root.mkdir(parents=True, exist_ok=True)
        destination.mkdir(parents=True, exist_ok=False)
        created = True
        with source.open("xb") as target:
            shutil.copyfileobj(file.file, target)
        mock_labels = destination / "mock_labels.json"
        mock_labels.write_text("{}\n", encoding="utf-8")
        run_pipeline(source, MockLabelParser(mock_labels), destination)
    except Exception as exc:
        # A partial result must not appear as a processed memo in the picker.
        if created:
            (destination / "songmap.json").unlink(missing_ok=True)
        logger.exception("Pipeline failed for memo %s", memo_id)
        raise HTTPException(status_code=500, detail=f"Pipeline failed: {exc}") from exc
    finally:
        file.file.close()
    return {"memo_id": memo_id}


app.mount("/", StaticFiles(directory=settings.static_root, html=True), name="static")
