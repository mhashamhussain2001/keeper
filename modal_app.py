"""Deploy Keeper's polling web app and bounded T4 processing worker on Modal."""

from pathlib import Path
import os

import modal


ROOT = Path(__file__).resolve().parent
app = modal.App("keeper")
keep_warm = 0  # Set to 1 and redeploy during demo recording; reset to 0 afterwards.
volume = modal.Volume.from_name("keeper-data", create_if_missing=True, version=2)
POLICY_ENV = {name: os.environ.get(name, default) for name, default in {
    "KEEPER_DOCS_ENABLED": "0", "KEEPER_MAX_AUDIO_MINUTES": "30", "KEEPER_MAX_UPLOAD_MB": "150",
    "KEEPER_OWNER_DAILY_UPLOADS": "5", "KEEPER_OWNER_DAILY_MINUTES": "90",
    "KEEPER_DAILY_MINUTES": "240", "KEEPER_RETENTION_DAYS": "14", "KEEPER_UPLOADS_ENABLED": "1",
    "KEEPER_COMMAND_FREE": "1",
}.items()}


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
          "OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4", **POLICY_ENV})
    .add_local_dir(ROOT / "keeper", "/root/keeper", copy=True, ignore=["**/__pycache__/**", "**/*.pyc"])
    .add_local_dir(ROOT / "eval", "/root/eval", copy=True, ignore=["**/__pycache__/**", "**/*.pyc"])
    .add_local_dir(ROOT / "app", "/root/app", copy=True, ignore=["**/__pycache__/**", "**/*.pyc"])
    .add_local_file(ROOT / "requirements.txt", "/root/requirements.txt", copy=True)
    .add_local_file(ROOT / "modal_app.py", "/root/modal_app.py", copy=True)
    .add_local_file(ROOT / "scripts" / "benchmark_audio_loading.py", "/root/scripts/benchmark_audio_loading.py", copy=True)
    .run_function(download_ast, include_source=False)
)


@app.function(image=image, gpu="T4", cpu=2, memory=8192, timeout=1800,
              volumes={"/data": volume}, include_source=False)
def benchmark_audio() -> dict:
    """Return private decoder measurements without changing deployed memos or their score caches."""
    from scripts.benchmark_audio_loading import benchmark

    volume.reload()
    result = benchmark(Path("/data/benchmarks/logic_v2"))
    volume.commit()
    return result


@app.cls(image=image, gpu="T4", cpu=2, memory=8192, timeout=3600,
              volumes={"/data": volume}, secrets=[modal.Secret.from_name("baseten")],
              min_containers=keep_warm, max_containers=1, scaledown_window=30, include_source=False)
class GPUWorker:
    """Process memos after optional once-per-container librosa initialization."""

    @modal.enter()
    def startup(self) -> None:
        """Return None after priming librosa before the container receives its first memo."""
        from keeper.audio_io import warm_librosa
        from keeper.config import KeeperConfig

        self.warmup_s = warm_librosa() if KeeperConfig().warm_librosa_on_start else 0.0
        print(f"librosa container warmup: {self.warmup_s:.6f} s", flush=True)

    @modal.method()
    def process_memo(self, memo_id: str) -> None:
        """Return None after publishing GPU processing results and committing every status transition."""
        import json
        from app.processing import process_memo as process
        from app.storage import Settings, memo_directory, write_status
        import torch

        volume.reload()
        settings = Settings()
        directory = memo_directory(settings, memo_id)
        try:
            if not torch.cuda.is_available():
                raise RuntimeError("The requested GPU worker has no CUDA device")
            process(memo_id, settings, commit=volume.commit, gpu=torch.cuda.get_device_name(0), warmup_s=self.warmup_s)
        except Exception:
            write_status(directory, "failed", "failed", "Processing could not finish", gpu="T4")
            volume.commit()


@app.function(image=image, cpu=1, memory=2048, timeout=300, volumes={"/data": volume},
              min_containers=0, max_containers=1, scaledown_window=60, include_source=False)
@modal.asgi_app()
def web() -> object:
    """Return the existing FastAPI app with shared-volume hooks and asynchronous GPU dispatch."""
    # Keep one web-container writer for the locked quota ledger on this Volume.
    import app.server as server

    def start_processing(memo_id: str) -> None:
        """Return None after spawning a durable Modal GPU invocation without awaiting its result."""
        GPUWorker().process_memo.spawn(memo_id)

    server.start_processing = start_processing
    server.reload_storage = volume.reload
    server.commit_storage = volume.commit
    return server.app


@app.function(image=image, cpu=1, memory=4096, timeout=600, volumes={"/data": volume},
              min_containers=0, max_containers=1, scaledown_window=2, include_source=False)
def manage_memos(action: str, memo_id: str | None = None, enabled: bool = False, dry_run: bool = False) -> dict:
    """Return an authenticated administrative migration or showcase update, without a public endpoint."""
    import json
    from app.metadata import migrate_memos, set_showcase
    from app.storage import Settings, memo_directory
    from app.waveform import write_peaks

    volume.reload()
    settings = Settings()
    if action == "purge":
        from app.retention import sweep_expired

        result = {"expired": sweep_expired(settings, dry_run=dry_run), "dry_run": dry_run}
    elif action == "showcase" and memo_id is not None:
        result = {"id": memo_id, **set_showcase(settings, memo_id, enabled)}
    elif action == "migrate":
        result = {"migrated": migrate_memos(settings), "waveforms": []}
        for directory in settings.outputs_root.iterdir():
            if not directory.is_dir():
                continue
            directory = memo_directory(settings, directory.name)
            manifest = directory / "upload.json"
            if manifest.exists() and not (directory / "peaks.json").exists():
                source = (settings.uploads_root / json.loads(manifest.read_text())["filename"]).resolve()
                if not source.is_relative_to(settings.uploads_root.resolve()):
                    raise ValueError("Upload path escaped storage")
                if source.is_file():
                    write_peaks(source, directory)
                    result["waveforms"].append(directory.name)
    else:
        raise ValueError("Unknown administration action")
    volume.commit()
    return result
