"""Serve owner-scoped memo artifacts and queue validated uploads for background processing."""

import json
import logging
import os
import shutil
import subprocess
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote
from uuid import uuid4
from typing import Literal
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from starlette.responses import Response, PlainTextResponse, JSONResponse
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from app.metadata import friendly_title, owner_token, owns, public_meta, read_meta, save_meta
from app.processing import start_local_processing
from app.storage import Settings, memo_directory, write_status
from app.uploads import AUDIO_EXTENSIONS, save_upload, validate_audio
from app.analysis_view import command_analysis
from app.native_audio import audio_format, open_original
from app.stems import build_stems, ordered_takes, safe_filename
from app.privacy import public_artifact
from app.policy import policy
from app.quotas import ADMISSION_LOCK, QuotaExceeded, queue_ahead, reserve_upload, release_reservation
from app.retention import remove_memo_files, sweep_expired


settings = Settings()
docs_enabled = os.environ.get("KEEPER_DOCS_ENABLED", "1").strip().lower() not in {"0", "false", "off"}
app = FastAPI(title="Keeper", docs_url="/docs" if docs_enabled else None,
              redoc_url="/redoc" if docs_enabled else None, openapi_url="/openapi.json" if docs_enabled else None)
logger = logging.getLogger(__name__)
logging.getLogger("uvicorn.access").disabled = True
Owner = Annotated[str | None, Header(alias="X-Keeper-Owner")]


@app.middleware("http")
async def private_api_responses(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Return API responses that cannot be cached or reused across browser-owner headers."""
    origin = request.headers.get("origin")
    if request.url.path.startswith("/api/") and origin and origin != f"{request.url.scheme}://{request.url.netloc}":
        response = JSONResponse({"detail": "Use this app from its own website."}, status_code=400)
    else:
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.error("Request failed (%s)", type(exc).__name__)
            response = JSONResponse({"detail": "This request could not be completed."}, status_code=500)
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "X-Keeper-Owner"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots() -> str:
    """Return a crawler directive excluding the entire application."""
    return "User-agent: *\nDisallow: /\n"


@app.get("/api/config")
def public_config() -> dict[str, bool | float | int]:
    """Return only public upload availability, size/duration limits and retention days."""
    limits = policy()
    return {"uploads_enabled": limits.uploads_enabled, "max_audio_minutes": limits.max_audio_minutes,
            "max_upload_mb": limits.max_upload_mb, "retention_days": limits.retention_days}


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


def _timeline(directory: Path) -> tuple[list[dict[str, Any]], float]:
    """Return saved regions and duration, retaining older memos that have no region artifact."""
    path = _within(directory, directory / "regions.json")
    value = _read_json(path) if path.exists() else []
    regions = value["regions"] if isinstance(value, dict) else value
    if regions:
        return regions, max(float(region["end"]) for region in regions)
    upload_path = _within(directory, directory / "upload.json")
    upload = _read_json(upload_path) if upload_path.exists() else {}
    songmap = _read_json(_within(directory, directory / "songmap.json"))
    duration = upload.get("duration_s") or max((take["end"] for takes in songmap["sections"].values() for take in takes), default=0.0)
    return regions, float(duration)


@app.get("/api/memos")
def list_memos(x_keeper_owner: Owner = None) -> list[dict[str, Any]]:
    """Return caller-owned completed memos newest first, followed by public showcase memos."""
    with ADMISSION_LOCK:
        reload_storage()
        sweep_expired(settings)
        if settings.outputs_root.is_dir():
            commit_storage()
    owner = owner_token(x_keeper_owner)
    memos = []
    if not settings.outputs_root.is_dir():
        return memos
    for directory in settings.outputs_root.iterdir():
        try:
            directory, metadata = _access(directory.name, owner)
            if _completed(directory):
                _, duration = _timeline(directory)
                memos.append({"id": directory.name, **public_meta(metadata, owner), "duration_s": duration})
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
    names_path = _within(directory, directory / "take_names.json")
    names = _read_json(names_path) if names_path.exists() else {}
    regions, duration = _timeline(directory)
    for takes in songmap["sections"].values():
        for take in takes:
            if take["id"] in names:
                take["custom_name"] = names[take["id"]]
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
    return public_artifact({"songmap": songmap, "regions": regions, "duration_s": duration, "peaks": peaks, "meta": public_meta(metadata, owner),
                            "analysis": command_analysis(directory, songmap, regions)}, metadata.get("original_filename", ""))


@app.get("/api/memos/{memo_id}/status")
def memo_status(memo_id: str, x_keeper_owner: Owner = None) -> dict[str, Any]:
    """Return authorized status and timestamped stages, including queued or failed memos."""
    reload_storage()
    directory, metadata = _access(memo_id, owner_token(x_keeper_owner))
    path = _within(directory, directory / "status.json")
    if path.is_file():
        status = public_artifact(_read_json(path), metadata.get("original_filename", ""))
        if status.get("state") == "queued":
            status["queue_ahead"] = queue_ahead(settings, memo_id)
        return status
    if (directory / "songmap.json").is_file():
        return {"state": "done", "stage": "done", "error": None, "updated_at": None, "history": [], "events": []}
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
    if metadata.get("showcase"):
        raise HTTPException(403, "Showcase memos are read-only")
    metadata["title"] = change.title
    save_meta(directory, metadata)
    commit_storage()
    return public_meta(metadata, owner)


@app.delete("/api/memos/{memo_id}")
def delete_memo(memo_id: str, x_keeper_owner: Owner = None) -> dict[str, bool]:
    """Return deleted=true after removing an owner's inactive memo and corresponding upload."""
    reload_storage()
    owner = owner_token(x_keeper_owner)
    directory, metadata = _access(memo_id, owner)
    if metadata.get("showcase"):
        raise HTTPException(status_code=403, detail="Showcase memos cannot be deleted")
    if not owns(metadata, owner):
        raise HTTPException(status_code=404, detail="Memo not found")
    status = directory / "status.json"
    if status.exists() and _read_json(status)["state"] in ("queued", "running"):
        raise HTTPException(status_code=409, detail="Wait for processing to finish before deleting this memo")
    with ADMISSION_LOCK:
        remove_memo_files(settings, directory)
        commit_storage()
    return {"deleted": True}


@app.post("/api/memos", status_code=201)
def upload_memo(file: Annotated[UploadFile, File()], x_keeper_owner: Owner = None,
                title: Annotated[str | None, Form()] = None) -> dict[str, str]:
    """Return a random queued ID after validating audio and recording browser ownership."""
    limits = policy()
    if not limits.uploads_enabled:
        file.file.close()
        raise HTTPException(status_code=503, detail="Uploads are paused. You can still listen to the showcase memo.")
    owner = owner_token(x_keeper_owner)
    if owner is None:
        file.file.close()
        raise HTTPException(status_code=400, detail="A browser owner token is required to upload")
    filename = Path((file.filename or "memo.wav").replace("\\", "/")).name
    suffix = Path(filename).suffix.lower()
    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    # audio/* includes iOS audio/x-m4a, audio/m4a, audio/mp4, audio/aac, and audio/x-caf.
    if not (content_type.startswith("audio/") or content_type == "application/octet-stream" or suffix in AUDIO_EXTENSIONS):
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
    accepted = False
    reserved = False
    source_saved = False
    directory_created = False
    try:
        settings.uploads_root.mkdir(parents=True, exist_ok=True)
        save_upload(file.file, source, limits)
        source_saved = True
        duration = validate_audio(source)
        with ADMISSION_LOCK:
            reload_storage()
            sweep_expired(settings, limits)
            reserve_upload(settings, owner, duration, memo_id, limits)
            reserved = True
            destination.mkdir(parents=True, exist_ok=False)
            directory_created = True
            now = datetime.now(timezone.utc)
            save_meta(destination, {"title": title or friendly_title(now), "original_filename": filename,
                                   "created_at": now.isoformat(), "owner": owner, "showcase": False})
            (destination / "upload.json").write_text(json.dumps({"filename": source.name, "original_filename": filename,
                                                                 "duration_s": duration}), encoding="utf-8")
            write_status(destination, "queued", "queued")
            commit_storage()
            accepted = True
    except QuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": str(exc.retry_after)}) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        file.file.close()
        if not accepted:
            if source_saved:
                source.unlink(missing_ok=True)
            if reserved:
                with ADMISSION_LOCK:
                    release_reservation(settings, memo_id)
                    if directory_created:
                        remove_memo_files(settings, destination)
                    commit_storage()
    try:
        start_processing(memo_id)
    except Exception as exc:
        write_status(destination, "failed", "failed", "Cannot start processing")
        commit_storage()
        raise HTTPException(status_code=500, detail="Cannot start memo processing") from exc
    return {"memo_id": memo_id}


class MoveTake(BaseModel):
    """An owner-requested section change with stable take identity."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    section: str | None = Field(default=None, min_length=1, max_length=80)
    name: str | None = Field(default=None, min_length=1, max_length=120, pattern=r"^[^\x00-\x1f\x7f]+$")


class CanvasSection(BaseModel):
    """A named arrangement interval in seconds."""

    name: str = Field(min_length=1, max_length=80)
    start: FiniteFloat = Field(ge=0)
    end: FiniteFloat = Field(gt=0)
    type: Literal["intro", "verse", "pre-chorus", "chorus", "bridge", "hook", "outro", "custom"] | None = None

    @model_validator(mode="after")
    def ordered(self) -> "CanvasSection":
        """Return a section whose end follows its start."""
        if self.end <= self.start:
            raise ValueError("Section end must follow its start")
        return self


class CanvasClip(BaseModel):
    """A stable take reference placed at a canvas time and vertical lane."""

    take_id: str
    start: FiniteFloat = Field(ge=0, le=86400)
    lane: int = Field(ge=0, le=99)


class CanvasDocument(BaseModel):
    """A bounded arrangement independent of source memo length."""

    sections: list[CanvasSection] = Field(default_factory=list, max_length=500)
    clips: list[CanvasClip] = Field(default_factory=list, max_length=2000)


def _save_artifact(directory: Path, filename: str, value: Any) -> None:
    """Return None after atomically replacing an edited memo artifact."""
    temporary = directory / (filename + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(directory / filename)
    commit_storage()


@app.patch("/api/memos/{memo_id}/takes/{take_id}")
def move_take(memo_id: str, take_id: str, change: MoveTake, x_keeper_owner: Owner = None) -> dict:
    """Return the song map after moving a stable take and renumbering section variants."""
    reload_storage()
    directory, metadata = _access(memo_id, owner_token(x_keeper_owner), owner_only=True)
    if metadata.get("showcase"):
        raise HTTPException(403, "Showcase memos are read-only")
    songmap = _read_json(directory / "songmap.json")
    if change.section is not None and change.section not in {"verse", "chorus", "bridge", "intro", "outro", "hook", "pre-chorus", "unsorted", "unlabeled", *songmap["sections"]}:
        raise HTTPException(422, "Unknown section")
    take = next((take for takes in songmap["sections"].values() for take in takes if take["id"] == take_id), None)
    if take is None:
        raise HTTPException(404, "Take not found")
    if change.section is not None and change.section != take["section"]:
        for section in songmap["sections"]:
            songmap["sections"][section] = [row for row in songmap["sections"][section] if row["id"] != take_id]
        take["section"] = change.section
        songmap["sections"].setdefault(change.section, []).append(take)
    if change.name is not None:
        names_path = directory / "take_names.json"
        names = _read_json(names_path) if names_path.exists() else {}
        names[take_id] = change.name.strip()
        _save_artifact(directory, "take_names.json", names)
    for takes in songmap["sections"].values():
        for index, row in enumerate(takes, 1):
            row["variant"] = f"Take {index}"
    save_meta(directory, metadata)
    _save_artifact(directory, "songmap.json", songmap)
    return public_artifact(songmap, metadata.get("original_filename", ""))


@app.get("/api/memos/{memo_id}/canvas")
def get_canvas(memo_id: str, x_keeper_owner: Owner = None) -> dict:
    """Return an owner's saved arrangement or an empty canvas."""
    reload_storage()
    directory, _ = _access(memo_id, owner_token(x_keeper_owner), owner_only=True)
    path = _within(directory, directory / "canvas.json")
    return _read_json(path) if path.exists() else {"sections": [], "clips": []}


@app.put("/api/memos/{memo_id}/canvas")
def put_canvas(memo_id: str, canvas: CanvasDocument, x_keeper_owner: Owner = None) -> dict:
    """Return a persisted owner arrangement after checking every referenced take exists."""
    reload_storage()
    directory, metadata = _access(memo_id, owner_token(x_keeper_owner), owner_only=True)
    if metadata.get("showcase"):
        raise HTTPException(403, "Showcase memos are read-only")
    songmap = _read_json(directory / "songmap.json")
    ids = {take["id"] for takes in songmap["sections"].values() for take in takes}
    if any(clip.take_id not in ids for clip in canvas.clips):
        raise HTTPException(422, "Unknown canvas take")
    value = canvas.model_dump(exclude_none=True)
    save_meta(directory, metadata)
    _save_artifact(directory, "canvas.json", value)
    return value


@app.get("/api/memos/{memo_id}/recording")
def recording(memo_id: str, x_keeper_owner: Owner = None) -> FileResponse:
    """Return the authorized original audio for shared recording playback."""
    reload_storage()
    directory, _ = _access(memo_id, owner_token(x_keeper_owner))
    manifest = directory / "upload.json"
    if not manifest.exists():
        raise HTTPException(404, "Original recording unavailable")
    source = _within(settings.uploads_root, settings.uploads_root / _read_json(manifest)["filename"])
    if not source.is_file():
        raise HTTPException(404, "Original recording unavailable")
    try:
        rate, channels = audio_format(source)
    except (OSError, ValueError, IndexError, KeyError, subprocess.SubprocessError):
        # Playback can still try a browser decoder; export explicitly reports a preview fallback.
        return FileResponse(source)
    return FileResponse(source, headers={"X-Audio-Sample-Rate": str(rate), "X-Audio-Channels": str(channels)})


@app.get("/api/memos/{memo_id}/export/stems")
def export_stems(memo_id: str, x_keeper_owner: Owner = None, include_rejected: bool = False) -> StreamingResponse:
    """Return an owner's bounded original-quality stems ZIP, allowing read-only showcase exports."""
    reload_storage()
    directory, metadata = _access(memo_id, owner_token(x_keeper_owner), owner_only=True)
    if not _completed(directory):
        raise HTTPException(404, "Memo is not ready to export")
    manifest = _within(directory, directory / "upload.json")
    if not manifest.exists():
        raise HTTPException(404, "Original recording unavailable")
    source = _within(settings.uploads_root, settings.uploads_root / _read_json(manifest)["filename"])
    if not source.is_file():
        raise HTTPException(404, "Original recording unavailable")
    songmap = _read_json(_within(directory, directory / "songmap.json"))
    names_path = _within(directory, directory / "take_names.json")
    canvas_path = _within(directory, directory / "canvas.json")
    takes = ordered_takes(songmap, _read_json(names_path) if names_path.exists() else {},
                          _read_json(canvas_path) if canvas_path.exists() else {}, include_rejected)
    if not takes:
        raise HTTPException(422, "No takes to export")
    try:
        with open_original(source) as audio:
            archive = build_stems(audio, takes)
    except OverflowError as exc:
        raise HTTPException(413, str(exc)) from exc
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise HTTPException(422, f"Cannot export original audio: {exc}") from exc

    def chunks() -> Iterator[bytes]:
        """Yield ZIP chunks and release the temporary archive on completion or disconnect."""
        try:
            while chunk := archive.read(64 * 1024):
                yield chunk
        finally:
            archive.close()

    filename = quote(safe_filename(metadata.get("title", "Memo")) + " - stems.zip", safe="")
    return StreamingResponse(chunks(), media_type="application/zip", background=BackgroundTask(archive.close),
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})


app.mount("/", StaticFiles(directory=settings.static_root, html=True), name="static")
