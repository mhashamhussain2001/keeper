"""Background memo processing shared by local threads and Modal GPU workers."""

import json
import logging
import os
from collections.abc import Callable
from threading import Thread

from dotenv import load_dotenv

from app.storage import Settings, memo_directory, write_status
from keeper.config import KeeperConfig
from keeper.labels import MockLabelParser, MuseSparkLabelParser
from keeper.pipeline import run_pipeline


logger = logging.getLogger(__name__)


def process_memo(memo_id: str, settings: Settings, commit: Callable[[], None] | None = None,
                 gpu: str | None = None) -> None:
    """Return None after processing a saved upload and persisting done or failed status."""
    directory = memo_directory(settings, memo_id)
    parser_name = "mock"

    def progress(stage: str) -> None:
        """Return None after publishing a running stage and synchronizing mounted storage."""
        write_status(directory, "running", stage, parser=parser_name, gpu=gpu)
        if commit is not None:
            commit()

    try:
        load_dotenv()
        parser_name = "spark" if os.environ.get("BASETEN_API_KEY") else "mock"
        progress("loading audio")
        manifest = json.loads((directory / "upload.json").read_text(encoding="utf-8"))
        source = (settings.uploads_root / manifest["filename"]).resolve()
        if not source.is_relative_to(settings.uploads_root.resolve()):
            raise ValueError("Upload path escaped uploads root")
        if parser_name == "spark":
            parser = MuseSparkLabelParser(directory / "spark_cache.json", KeeperConfig().max_spark_calls)
        else:
            mock = directory / "mock_labels.json"
            mock.write_text("{}\n", encoding="utf-8")
            parser = MockLabelParser(mock)
        cache = directory / "window_scores.csv"
        run_pipeline(source, parser, directory, scores_csv=cache if cache.exists() else None, progress=progress)
        write_status(directory, "done", "done", parser=parser_name, gpu=gpu)
        if commit is not None:
            commit()
    except Exception as exc:
        logger.exception("Processing failed for memo %s", memo_id)
        (directory / "songmap.json").unlink(missing_ok=True)
        write_status(directory, "failed", "failed", str(exc), parser=parser_name, gpu=gpu)
        if commit is not None:
            commit()


def start_local_processing(memo_id: str, settings: Settings) -> Thread:
    """Return a started daemon thread that processes one memo without blocking the upload."""
    thread = Thread(target=process_memo, args=(memo_id, settings), name=f"keeper-{memo_id}", daemon=True)
    thread.start()
    return thread
