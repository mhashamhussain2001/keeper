"""Deploy Keeper's polling web app and bounded T4 processing worker on Modal."""

from pathlib import Path

import modal


ROOT = Path(__file__).resolve().parent
app = modal.App("keeper")
volume = modal.Volume.from_name("keeper-data", create_if_missing=True, version=2)


def download_ast() -> None:
    """Return None after baking the AST model and numpy feature extractor into the image cache."""
    from transformers import ASTFeatureExtractor, ASTForAudioClassification
    from keeper.config import AST_MODEL_ID

    ASTFeatureExtractor.from_pretrained(AST_MODEL_ID)
    ASTForAudioClassification.from_pretrained(AST_MODEL_ID)


image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libsndfile1")
    .pip_install_from_requirements(str(ROOT / "requirements.txt"))
    .env({"HF_HOME": "/opt/huggingface", "HF_HUB_DISABLE_TELEMETRY": "1",
          "KEEPER_OUTPUTS_DIR": "/data/outputs", "KEEPER_UPLOADS_DIR": "/data/uploads",
          "OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4"})
    .add_local_dir(ROOT / "keeper", "/root/keeper", copy=True, ignore=["**/__pycache__/**", "**/*.pyc"])
    .add_local_dir(ROOT / "eval", "/root/eval", copy=True, ignore=["**/__pycache__/**", "**/*.pyc"])
    .add_local_dir(ROOT / "app", "/root/app", copy=True, ignore=["**/__pycache__/**", "**/*.pyc"])
    .add_local_file(ROOT / "requirements.txt", "/root/requirements.txt", copy=True)
    .add_local_file(ROOT / "modal_app.py", "/root/modal_app.py", copy=True)
    .run_function(download_ast, include_source=False)
)


@app.function(image=image, gpu="T4", cpu=2, memory=8192, timeout=1800,
              volumes={"/data": volume}, secrets=[modal.Secret.from_name("baseten")],
              min_containers=0, max_containers=1, scaledown_window=30, include_source=False)
def process_memo(memo_id: str) -> None:
    """Return None after publishing GPU processing results and committing every status transition."""
    from app.processing import process_memo as process
    from app.storage import Settings, memo_directory, write_status
    import torch

    volume.reload()
    settings = Settings()
    try:
        if not torch.cuda.is_available():
            raise RuntimeError("The requested GPU worker has no CUDA device")
        process(memo_id, settings, commit=volume.commit, gpu=torch.cuda.get_device_name(0))
    except Exception as exc:
        write_status(memo_directory(settings, memo_id), "failed", "failed", str(exc), gpu="T4")
        volume.commit()
        raise


@app.function(image=image, cpu=1, memory=2048, timeout=300, volumes={"/data": volume},
              min_containers=0, max_containers=1, scaledown_window=60, include_source=False)
@modal.asgi_app()
def web() -> object:
    """Return the existing FastAPI app with shared-volume hooks and asynchronous GPU dispatch."""
    import app.server as server

    def start_processing(memo_id: str) -> None:
        """Return None after spawning a durable Modal GPU invocation without awaiting its result."""
        process_memo.spawn(memo_id)

    server.start_processing = start_processing
    server.reload_storage = volume.reload
    server.commit_storage = volume.commit
    return server.app
