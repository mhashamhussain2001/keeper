"""Serve owner-scoped memo artifacts and queue validated uploads for background processing."""

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote
from uuid import uuid4
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from starlette.responses import Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.metadata import friendly_title, owner_token, owns, public_meta, read_meta, save_meta
from app.processing import start_local_processing
from app.storage import Settings, memo_directory, write_status
from app.uploads import AUDIO_EXTENSIONS, save_upload, validate_audio


settings = Settings()
app = FastAPI(title="Keeper")
logger = logging.getLogger(__name__)
Owner = Annotated[str | None, Header(alias="X-Keeper-Owner")]


@app.middleware("http")
async def private_api_responses(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Return API responses that cannot be cached or reused across browser-owner headers."""
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "X-Keeper-Owner"
    return response


class RenameMemo(BaseModel):
    """Owner-editable memo metadata; ownership and showcase are never client editable."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=120, pattern=r"^[^\x00-\x1f\x7f]+$")


def reload_storage() -> None:
    """Return None locally; Modal replaces this hook with its Volume reload operation."""


def commit_storage() -> None:
    """Return None locally; Modal replaces this hook with its Volume commit operation."""


def start_processing(memo_id: str) -> None:
    """Return None after starting local processing; Modal replaces this with a GPU spawn."""
    start_local_processing(memo_id, settings)


def _within(root: Path, path: Path) -> Path:
    """Return a resolved child path or raise 404 for paths outside its root."""
    resolved = path.resolve()
    if resolved == root.resolve() or not resolved.is_relative_to(root.resolve()):
        raise HTTPException(status_code=404, detail="Not found")
    return resolved


def _read_json(path: Path) -> Any:
    """Return a parsed JSON artifact or raise a clear server error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Cannot read memo artifact {path.name}") from exc


def _access(memo_id: str, owner: str | None, owner_only: bool = False) -> tuple[Path, dict[str, Any]]:
    """Return authorized storage and metadata, concealing all other memo IDs with 404."""
    try:
        directory = memo_directory(settings, memo_id)
        metadata = read_meta(directory)
    except (ValueError, OSError):
        raise HTTPException(status_code=404, detail="Memo not found") from None
    if not directory.is_dir() or not (owns(metadata, owner) or (not owner_only and metadata.get("showcase") is True)):
        raise HTTPException(status_code=404, detail="Memo not found")
    return directory, metadata


def _completed(directory: Path) -> bool:
    """Return whether a memo has completed artifacts, including legacy memos without status."""
    status = _within(directory, directory / "status.json")
    return (directory / "songmap.json").is_file() and (not status.exists() or _read_json(status)["state"] == "done")


@app.get("/api/memos")
def list_memos(x_keeper_owner: Owner = None) -> list[dict[str, Any]]:
    """Return caller-owned completed memos newest first, followed by public showcase memos."""
    reload_storage()
    owner = owner_token(x_keeper_owner)
    memos = []
    if not settings.outputs_root.is_dir():
        return memos
    for directory in settings.outputs_root.iterdir():
        try:
            directory, metadata = _access(directory.name, owner)
            if _completed(directory):
                memos.append({"id": directory.name, **public_meta(metadata, owner)})
        except HTTPException:
            continue
    return sorted(memos, key=lambda row: (row["owned"], row["created_at"] or "", row["id"]), reverse=True)


@app.get("/api/memos/{memo_id}")
def memo_detail(memo_id: str, x_keeper_owner: Owner = None) -> dict[str, Any]:
    """Return authorized display data, waveform, and guarded clip URLs without owner tokens."""
    reload_storage()
    owner = owner_token(x_keeper_owner)
    directory, metadata = _access(memo_id, owner)
    if not _completed(directory):
        raise HTTPException(status_code=404, detail="Memo not found")
    songmap = _read_json(_within(directory, directory / "songmap.json"))
    region_data = _read_json(_within(directory, directory / "regions.json"))
    regions = region_data["regions"] if isinstance(region_data, dict) else region_data
    for takes in songmap["sections"].values():
        for take in takes:
            take["clip_url"] = None
            if not take.get("clip_path"):
                continue
            candidate = Path(take["clip_path"].replace("\\", "/"))
            clip = _within(directory, candidate if candidate.is_absolute() else directory / candidate)
            relative = clip.relative_to(directory).as_posix()
            take["clip_path"] = relative
            take["clip_url"] = f"/api/memos/{quote(memo_id, safe='')}/clips/{quote(relative, safe='/')}"
    peaks_path = _within(directory, directory / "peaks.json")
    peaks = _read_json(peaks_path)["peaks"] if peaks_path.exists() else [0.0] * 800
    duration = max((float(region["end"]) for region in regions), default=0.0)
    return {"songmap": songmap, "regions": regions, "duration_s": duration, "peaks": peaks, "meta": public_meta(metadata, owner)}


@app.get("/api/memos/{memo_id}/status")
def memo_status(memo_id: str, x_keeper_owner: Owner = None) -> dict[str, Any]:
    """Return authorized status and timestamped stages, including queued or failed memos."""
    reload_storage()
    directory, _ = _access(memo_id, owner_token(x_keeper_owner))
    path = _within(directory, directory / "status.json")
    if path.is_file():
        return _read_json(path)
    if (directory / "songmap.json").is_file():
        return {"state": "done", "stage": "done", "error": None, "updated_at": None, "history": []}
    raise HTTPException(status_code=404, detail="Memo not found")


@app.get("/api/memos/{memo_id}/clips/{path:path}")
def serve_clip(memo_id: str, path: str, x_keeper_owner: Owner = None) -> FileResponse:
    """Return an authorized WAV clip or 404 for hidden memos and invalid paths."""
    reload_storage()
    directory, _ = _access(memo_id, owner_token(x_keeper_owner))
    if not _completed(directory):
        raise HTTPException(status_code=404, detail="Memo not found")
    clip = _within(directory, directory / path.replace("\\", "/"))
    if clip.suffix.lower() != ".wav" or not clip.is_file():
        raise HTTPException(status_code=404, detail="Clip not found")
    return FileResponse(clip, media_type="audio/wav")


@app.patch("/api/memos/{memo_id}")
def rename_memo(memo_id: str, change: RenameMemo, x_keeper_owner: Owner = None) -> dict[str, Any]:
    """Return public metadata after an owner changes only the friendly title."""
    reload_storage()
    owner = owner_token(x_keeper_owner)
    directory, metadata = _access(memo_id, owner, owner_only=True)
    metadata["title"] = change.title
    save_meta(directory, metadata)
    commit_storage()
    return public_meta(metadata, owner)


@app.delete("/api/memos/{memo_id}")
def delete_memo(memo_id: str, x_keeper_owner: Owner = None) -> dict[str, bool]:
    """Return deleted=true after removing an owner's inactive memo and corresponding upload."""
    reload_storage()
    directory, _ = _access(memo_id, owner_token(x_keeper_owner), owner_only=True)
    status = directory / "status.json"
    if status.exists() and _read_json(status)["state"] in ("queued", "running"):
        raise HTTPException(status_code=409, detail="Wait for processing to finish before deleting this memo")
    upload = _within(directory, directory / "upload.json")
    if upload.exists():
        filename = _read_json(upload).get("filename", "")
        source = _within(settings.uploads_root, settings.uploads_root / filename)
        if source.name.startswith(memo_id + "."):
            source.unlink(missing_ok=True)
    directory = _within(settings.outputs_root, directory)
    shutil.rmtree(directory)
    commit_storage()
    return {"deleted": True}


@app.post("/api/memos", status_code=201)
def upload_memo(file: Annotated[UploadFile, File()], x_keeper_owner: Owner = None,
                title: Annotated[str | None, Form()] = None) -> dict[str, str]:
    """Return a random queued ID after validating audio and recording browser ownership."""
    reload_storage()
    owner = owner_token(x_keeper_owner)
    if owner is None:
        file.file.close()
        raise HTTPException(status_code=400, detail="A browser owner token is required to upload")
    filename = Path((file.filename or "memo.wav").replace("\\", "/")).name
    suffix = Path(filename).suffix.lower()
    if suffix not in AUDIO_EXTENSIONS:
        file.file.close()
        raise HTTPException(status_code=400, detail="Upload a WAV, MP3, M4A, FLAC, OGG, or AIFF audio file")
    if title is not None:
        try:
            title = RenameMemo(title=title).title
        except ValueError as exc:
            file.file.close()
            raise HTTPException(status_code=400, detail="Title must contain 1-120 characters without control characters") from exc
    memo_id = uuid4().hex
    destination = memo_directory(settings, memo_id)
    source = settings.uploads_root / f"{memo_id}{suffix}"
    try:
        settings.uploads_root.mkdir(parents=True, exist_ok=True)
        save_upload(file.file, source)
        duration = validate_audio(source)
    except ValueError as exc:
        try:
            source.unlink(missing_ok=True)
        except OSError:
            logger.warning("Cannot remove rejected upload while its decoder holds it open")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        file.file.close()
    destination.mkdir(parents=True, exist_ok=False)
    now = datetime.now(timezone.utc)
    save_meta(destination, {"title": title or friendly_title(now), "original_filename": filename,
                           "created_at": now.isoformat(), "owner": owner, "showcase": False})
    (destination / "upload.json").write_text(json.dumps({"filename": source.name, "original_filename": filename, "duration_s": duration}), encoding="utf-8")
    write_status(destination, "queued", "queued")
    commit_storage()
    try:
        start_processing(memo_id)
    except Exception as exc:
        write_status(destination, "failed", "failed", f"Cannot start processing: {exc}")
        commit_storage()
        raise HTTPException(status_code=500, detail="Cannot start memo processing") from exc
    return {"memo_id": memo_id}


app.mount("/", StaticFiles(directory=settings.static_root, html=True), name="static")
