import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Settings:
    data: Path = ROOT / "data"
    database: Path | None = None
    worker_lock_path: Path | None = None
    reviewer_user: str = "reviewer"
    reviewer_password: str = ""
    reviewer_identity: str = "Team member — simulated reviewer"
    recon_backend: str = "fixture"
    gemma_backend: str = "fixture"
    jev_enabled: bool = False
    jev_api_key: str = ""
    gemma_base_url: str = "http://vllm:8000/v1"
    gemma_model: str = "google/gemma-4-31B-it"
    gemma_revision: str = ""
    vggt_checkpoint: str = ""
    vggt_commit: str = ""
    vggt_checkpoint_sha256: str = ""
    max_file_bytes: int = 20 * 1024 * 1024
    max_assessment_bytes: int = 200 * 1024 * 1024
    max_photos: int = 40
    max_pixels: int = 40_000_000
    frame_cap: int = 20
    web_dist: Path = ROOT / "web/dist"

    @property
    def db_path(self):
        return self.database or self.data / "assessment.sqlite3"

    def __post_init__(self):
        if self.recon_backend not in {"fixture", "vggt"}:
            raise ValueError("RECON_BACKEND must be fixture or vggt")
        if self.gemma_backend not in {"fixture", "http"}:
            raise ValueError("GEMMA_BACKEND must be fixture or http")

    def modes(self):
        return {"reconstruction": self.recon_backend, "gemma": self.gemma_backend,
                "jev": "live" if self.jev_enabled else "disabled"}

    @classmethod
    def from_env(cls):
        return cls(
            data=Path(os.getenv("SAFE_HOME_DATA", str(ROOT / "data"))).resolve(),
            database=Path(os.environ["SAFE_HOME_DATABASE"]).resolve() if os.getenv("SAFE_HOME_DATABASE") else None,
            worker_lock_path=Path(os.environ["SAFE_HOME_WORKER_LOCK"]).resolve() if os.getenv("SAFE_HOME_WORKER_LOCK") else None,
            reviewer_user=os.getenv("REVIEWER_USER", "reviewer"),
            reviewer_password=os.getenv("REVIEWER_PASSWORD", ""),
            reviewer_identity=os.getenv("REVIEWER_IDENTITY", "Team member — simulated reviewer"),
            recon_backend=os.getenv("RECON_BACKEND", "fixture"),
            gemma_backend=os.getenv("GEMMA_BACKEND", "fixture"),
            jev_enabled=os.getenv("JEV_ENABLED", "false").lower() == "true",
            jev_api_key=os.getenv("JEV_API_KEY", ""),
            gemma_base_url=os.getenv("GEMMA_BASE_URL", "http://vllm:8000/v1").rstrip("/"),
            gemma_model=os.getenv("GEMMA_MODEL", "google/gemma-4-31B-it"),
            gemma_revision=os.getenv("GEMMA_MODEL_REVISION", ""),
            vggt_checkpoint=os.getenv("VGGT_CHECKPOINT", ""), vggt_commit=os.getenv("VGGT_COMMIT", ""),
            vggt_checkpoint_sha256=os.getenv("VGGT_CHECKPOINT_SHA256", ""),
        )
