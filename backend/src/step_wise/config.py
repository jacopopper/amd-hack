import os
import json
import subprocess
from hashlib import sha256
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _project_env(name, default=None):
    return os.getenv(f"STEP_WISE_{name}", os.getenv(f"SAFE_HOME_{name}", default))


def _load_project_env():
    path = ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if separator and key.strip() and key.strip() not in os.environ:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            os.environ[key.strip()] = value


def _local_vggt_defaults():
    checkpoint = ROOT / "model-cache/VGGT-1B/model.pt"
    provenance = checkpoint.with_name("provenance.json")
    source = ROOT / "model-cache/vggt"
    if not checkpoint.is_file() or not provenance.is_file() or not (source / ".git").exists():
        return "", "", ""
    try:
        metadata = json.loads(provenance.read_text())
        commit = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=3, check=True).stdout.strip()
        return str(checkpoint), commit, metadata["sha256"]
    except (OSError, subprocess.SubprocessError, KeyError, ValueError):
        return "", "", ""


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
    avatar_backend: str = "fixture"
    jev_enabled: bool = False
    jev_api_key: str = ""
    gemma_base_url: str = "http://vllm:8000/v1"
    gemma_model: str = "google/gemma-4-31B-it"
    gemma_provider: str = ""
    hf_token: str = ""
    gemma_revision: str = ""
    gemma_timeout_seconds: int = 120
    blender_bin: str = ""
    blender_home: str = ""
    blender_user_config: str = ""
    blender_xdg_config_home: str = ""
    blender_xdg_data_home: str = ""
    mpfb_asset_pack_sha256: str = ""
    avatar_timeout_seconds: int = 300
    vggt_python: str = ""
    vggt_timeout_seconds: int = 900
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
        if self.avatar_backend not in {"fixture", "mpfb"}:
            raise ValueError("AVATAR_BACKEND must be fixture or mpfb")
        for name in ("max_file_bytes", "max_assessment_bytes", "max_photos", "max_pixels", "frame_cap",
                     "gemma_timeout_seconds", "avatar_timeout_seconds", "vggt_timeout_seconds"):
            if isinstance(getattr(self, name), bool) or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.frame_cap > self.max_photos:
            raise ValueError("The room frame cap cannot exceed the assessment photo limit")

    def modes(self):
        return {"reconstruction": self.recon_backend, "gemma": self.gemma_backend, "avatar": self.avatar_backend,
                "jev": "live" if self.jev_enabled else "disabled"}

    def gemma_identity(self):
        # Bind queued requests/cache reuse without exposing endpoint credentials.
        return {"provider": self.gemma_provider or None, "model": self.gemma_model,
                "requested_hub_revision": self.gemma_revision or None,
                "endpoint_sha256": sha256(self.gemma_base_url.rstrip("/").encode()).hexdigest()}

    @classmethod
    def from_env(cls):
        _load_project_env()
        local_checkpoint, local_commit, local_sha256 = _local_vggt_defaults()
        vggt_python = Path(os.getenv("VGGT_PYTHON", str(ROOT / "model-cache/vggt-env/bin/python3"))).expanduser()
        if not vggt_python.is_absolute():
            vggt_python = (ROOT / vggt_python).resolve()
        vggt_checkpoint = os.getenv("VGGT_CHECKPOINT", local_checkpoint).strip()
        if vggt_checkpoint:
            checkpoint_path = Path(vggt_checkpoint).expanduser()
            vggt_checkpoint = str(checkpoint_path if checkpoint_path.is_absolute() else (ROOT / checkpoint_path).resolve())
        return cls(
            data=Path(_project_env("DATA", str(ROOT / "data"))).resolve(),
            database=Path(_project_env("DATABASE")).resolve() if _project_env("DATABASE") else None,
            worker_lock_path=Path(_project_env("WORKER_LOCK")).resolve() if _project_env("WORKER_LOCK") else None,
            reviewer_user=os.getenv("REVIEWER_USER", "reviewer"),
            reviewer_password=os.getenv("REVIEWER_PASSWORD", ""),
            reviewer_identity=os.getenv("REVIEWER_IDENTITY", "Team member — simulated reviewer"),
            recon_backend=os.getenv("RECON_BACKEND", "vggt" if local_checkpoint else "fixture"),
            gemma_backend=os.getenv("GEMMA_BACKEND", "http" if os.getenv("HF_TOKEN") else "fixture"),
            avatar_backend=os.getenv("AVATAR_BACKEND", "mpfb" if os.getenv("BLENDER_BIN") else "fixture"),
            jev_enabled=os.getenv("JEV_ENABLED", "false").lower() == "true",
            jev_api_key=os.getenv("JEV_API_KEY", ""),
            gemma_base_url=os.getenv("GEMMA_BASE_URL", "https://router.huggingface.co/v1").rstrip("/"),
            gemma_model=os.getenv("GEMMA_MODEL", "google/gemma-4-31B-it"),
            gemma_provider=os.getenv("GEMMA_PROVIDER", ""),
            hf_token=os.getenv("HF_TOKEN", ""),
            gemma_revision=os.getenv("GEMMA_MODEL_REVISION", ""),
            gemma_timeout_seconds=int(os.getenv("GEMMA_TIMEOUT_SECONDS", "120")),
            blender_bin=os.getenv("BLENDER_BIN", ""),
            blender_home=os.getenv("BLENDER_HOME", ""),
            blender_user_config=os.getenv("BLENDER_USER_CONFIG", ""),
            blender_xdg_config_home=os.getenv("XDG_CONFIG_HOME", ""),
            blender_xdg_data_home=os.getenv("XDG_DATA_HOME", ""),
            mpfb_asset_pack_sha256=os.getenv("MPFB_ASSET_PACK_SHA256", ""),
            avatar_timeout_seconds=int(os.getenv("AVATAR_TIMEOUT_SECONDS", "300")),
            vggt_python=str(vggt_python),
            vggt_timeout_seconds=int(os.getenv("VGGT_TIMEOUT_SECONDS", "900")),
            vggt_checkpoint=vggt_checkpoint, vggt_commit=os.getenv("VGGT_COMMIT", local_commit),
            vggt_checkpoint_sha256=os.getenv("VGGT_CHECKPOINT_SHA256", local_sha256),
            frame_cap=int(os.getenv("VGGT_FRAME_CAP", "20")),
            max_photos=int(_project_env("MAX_PHOTOS", "40")),
            max_file_bytes=int(_project_env("MAX_FILE_BYTES", str(20 * 1024 * 1024))),
            max_assessment_bytes=int(_project_env("MAX_ASSESSMENT_BYTES", str(200 * 1024 * 1024))),
            max_pixels=int(_project_env("MAX_PIXELS", "40000000")),
        )
