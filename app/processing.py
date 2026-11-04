"""Background memo processing shared by local threads and Modal GPU workers."""

import json
import logging
import os
from collections.abc import Callable
from threading import Thread

from dotenv import load_dotenv

from app.storage import Settings, memo_directory
from app.event_status import EventStatus
from app.waveform import write_peaks
from app.spark_budget import WebLabelParser
from keeper.config import KeeperConfig
from keeper.labels import MockLabelParser, MuseSparkLabelParser
from keeper.pipeline import run_pipeline
from keeper.events import EventReporter


logger = logging.getLogger(__name__)


def process_memo(memo_id: str, settings: Settings, commit: Callable[[], None] | None = None,
                 gpu: str | None = None, warmup_s: float | None = None) -> None:
    """Return None after processing a saved upload and persisting done or failed status."""
    directory = memo_directory(settings, memo_id)
    parser_name = "mock"
    status = EventStatus(directory, commit, parser=parser_name, gpu=gpu, gpu_librosa_warmup_s=warmup_s)

    try:
        load_dotenv()
        parser_name = "spark" if os.environ.get("BASETEN_API_KEY") else "mock"
        status.metadata["parser"] = parser_name
        status.progress("loading audio")
        manifest = json.loads((directory / "upload.json").read_text(encoding="utf-8"))
        source = (settings.uploads_root / manifest["filename"]).resolve()
        if not source.is_relative_to(settings.uploads_root.resolve()):
            raise ValueError("Upload path escaped uploads root")
        if not (directory / "peaks.json").exists():
            write_peaks(source, directory)
        status.metadata["peaks"] = json.loads((directory / "peaks.json").read_text(encoding="utf-8"))["peaks"]

        if parser_name == "spark":
            parser = WebLabelParser(directory / "spark_cache.json", float(manifest["duration_s"]),
                                    prompt_v2=KeeperConfig().spark_prompt_v2)
        else:
            mock = directory / "mock_labels.json"
            mock.write_text("{}\n", encoding="utf-8")
            parser = MockLabelParser(mock)
        cache = directory / "window_scores.csv"
        run_pipeline(source, parser, directory, scores_csv=cache if cache.exists() else None,
                     progress=status.progress, on_event=status.event)
        status.finish("done")
    except Exception as exc:
        logger.error("Processing failed for memo %s (%s)", memo_id, type(exc).__name__)
        (directory / "songmap.json").unlink(missing_ok=True)
        if not status.events or status.events[-1]["type"] != "failed":
            EventReporter(status.event).emit("failed", message=str(exc))
        status.finish("failed", str(exc))


def start_local_processing(memo_id: str, settings: Settings) -> Thread:
    """Return a started daemon thread that processes one memo without blocking the upload."""
    thread = Thread(target=process_memo, args=(memo_id, settings), name=f"keeper-{memo_id}", daemon=True)
    thread.start()
    return thread
