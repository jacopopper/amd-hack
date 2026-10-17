"""Stage a reconstructed capture for the room view of the demo case.

The reviewed capture (photos IMG_7580-IMG_7618, compressed polished splats and
mocked hazard findings) is already committed: viewer assets under
frontend/public/capture-data, case photos under demo/capture. A fresh clone
needs nothing here. Run with the project's Python environment (numpy is
required) only to stage a different local capture or refresh the mocked findings:
  .venv/bin/python scripts/prepare_local_room_review.py /absolute/capture/path
Then `python -m step_wise.cli import-capture` builds the reviewable case.
The two findings are explicitly mocked, not outputs from JEV.
"""

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np

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
    if (source / "surface-polish.json").is_file():
        staged.append((photos, "surface-polish.json"))
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

    # Hand-selected photo regions. Depth only gives an indicative navigation pin;
    # neither the mock classification nor the position is clinically validated.
    findings = [
        {
            "id": "cord", "label": "Cord in the walkway", "category": "cable",
            "view_index": 19, "color": "#ff6b5e",
            "box": [0.025, 0.32, 0.43, 0.99], "pixel": [0.19, 0.78],
            "observation": "A cord hangs from the table and reaches the floor beside the chair.",
            "action": "Gather and secure the cord along the furniture, away from the walkway.",
        },
        {
            "id": "carpet", "label": "Carpet in front of the kitchen", "category": "rug",
            "view_index": 23, "color": "#f5b54a",
            "box": [0.40, 0.565, 0.84, 0.66], "pixel": [0.63, 0.615],
            "observation": "A lightweight carpet with an uneven edge lies in front of the kitchen cabinets.",
            "action": "Remove it from the walkway or secure it with non-slip backing; check the edges.",
        },
    ]
    scene = json.loads((source / "scene.json").read_text())
    with np.load(source / "assets/capture-analysis.npz") as arrays:
        for finding in findings:
            index = finding["view_index"]
            x, y = finding.pop("pixel")
            points = arrays["world_points"][index]
            left, top, right, bottom = scene["images"][index]["transform"]["content_box"]
            row, col = round(top + y * (bottom - top - 1)), round(left + x * (right - left - 1))
            position = np.median(points[row - 2:row + 3, col - 2:col + 3].reshape(-1, 3), axis=0)
            if not np.isfinite(position).all():
                raise ValueError(f"Non-finite mock pin: {finding['id']}")
            finding.update(
                position_world=position.tolist(),
                photo_id=data["views"][index]["id"],
                provenance="mock_user_requested",
                position_method="manual_photo_region_depth_estimate",
                uncertainty="Manually marked demo finding. Approximate, unvalidated 3D position. Not a JEV result.",
            )
    (target / "hazards.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2) + "\n")
    print(f"39 photos and 2 explicitly mocked findings ready in {target}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    args = parser.parse_args()
    prepare(args.capture, ROOT / "frontend/public/capture-data", ROOT / "demo/capture")
