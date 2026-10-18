"""Stage a reconstructed capture for the room view of the demo case.

The reviewed capture (photos IMG_7580-IMG_7618, compressed polished splats and
the recorded Jev findings) is already committed: viewer assets under
frontend/public/capture-data, case photos under demo/capture. A fresh clone
needs nothing here. Run with the project's Python environment only to stage a
different local capture:
  .venv/bin/python scripts/prepare_local_room_review.py /absolute/capture/path
Then `python -m step_wise.cli import-capture` builds the reviewable case.

Findings are never written here: scripts/detect_capture_hazards.py records them
from live Jev runs over the case photos, and a committed hazards.json takes
precedence like every other repository asset.
"""

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Only what local-room-viewer.html loads; the standalone capture viewer's mesh loader stays out.
VENDOR = ["three.module.js", "OrbitControls.js", "splats.js", "LICENSE-three.txt", "LICENSE-splats.txt"]


def prepare(source: Path, target: Path, photos: Path) -> None:
    source = source.resolve(strict=True)
    data = json.loads((source / "viewpoints.json").read_text())
    expected = [f"IMG_{i}" for i in range(7580, 7619)]
    if [view["id"] for view in data["views"]] != expected:
        raise ValueError("Expected exactly IMG_7580 through IMG_7618, in order.")
    # Do not expose checkpoints, analysis arrays, photos or other captures through public/.
    staged = [(target, name) for name in ["viewpoints.json", *(f"vendor/{v}" for v in VENDOR)]]
    staged += [(photos, name) for name in ["training.json", *(view["image"] for view in data["views"])]]
    for directory, name in staged:
        original = (source / name).resolve(strict=True)
        if not original.is_relative_to(source):
            raise ValueError(f"Asset escapes the capture directory: {name}")
        link = directory / name
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() and link.resolve() == original:
            continue
        if link.exists() and not link.is_symlink():
            # A committed repository asset takes precedence over symlinking.
            continue
        if link.exists() or link.is_symlink():
            raise FileExistsError(f"Refusing to replace an existing asset: {link}")
        link.symlink_to(original)
    splats = source / ("room-polished.ply" if (source / "room-polished.ply").is_file() else "room.ply")
    if not (target / "room.ksplat").exists():
        subprocess.run(["node", str(ROOT / "scripts/compress_splats.mjs"), str(splats), str(target / "room.ksplat")], check=True)
    print(f"Viewer assets ready in {target}; findings stay as recorded by scripts/detect_capture_hazards.py")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    args = parser.parse_args()
    prepare(args.capture, ROOT / "frontend/public/capture-data", ROOT / "demo/capture")
