"""Attach a photo-completed or illustrative GLB to one current analysis.

This does not write hazard anchors, measurements, review decisions or reports.
"""
import argparse
import json
import math
import shutil
import struct
from pathlib import Path

import numpy as np

from step_wise import db
from step_wise.config import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assessment", required=True)
    parser.add_argument("--scene", required=True)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--provenance", choices=("illustrative", "photo_completed"), default="illustrative")
    parser.add_argument("--pins", type=Path, help="Required for illustrations: JSON current hazard ID -> illustrative [x,y,z]")
    parser.add_argument("--display-transform", type=Path, help="Photo completion only: JSON 4×4 rigid raw-VGGT-to-viewer transform")
    parser.add_argument("--expected-input-revision", required=True, type=int)
    parser.add_argument("--expected-analysis-job", required=True)
    parser.add_argument("--replace", action="store_true", help="Attach a new asset, retaining the previous asset")
    args = parser.parse_args()
    completed = args.provenance == "photo_completed"
    settings = Settings.from_env()
    if not 20 <= args.model.stat().st_size <= settings.max_file_bytes:
        raise ValueError("Presentation exceeds the local asset size bound")
    raw = args.model.read_bytes()
    magic, version, length, chunk_length, chunk_type = struct.unpack("<4sIIII", raw[:20])
    if magic != b"glTF" or version != 2 or length != len(raw) or chunk_type != 0x4E4F534A:
        raise ValueError("Expected a self-contained GLB 2 asset")
    model = json.loads(raw[20:20 + chunk_length])
    for item in model.get("images", []) + model.get("buffers", []):
        if item.get("uri") and not item["uri"].startswith("data:"):
            raise ValueError("Presentation must not fetch external assets")
    pins = json.loads(args.pins.read_text()) if args.pins else {}
    if not isinstance(pins, dict) or (not completed and not pins) or len(pins) > 60 or any(
        not isinstance(v, list) or len(v) != 3 or any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in v)
        for v in pins.values()
    ):
        raise ValueError("Expected finite illustrative pin positions")
    if completed and pins:
        raise ValueError("Photo completion uses the original analytical positions, not illustrative pins")
    display_transform = None
    if args.display_transform:
        if not completed:
            raise ValueError("Display transform is only supported for photo completion")
        matrix = np.asarray(json.loads(args.display_transform.read_text()), dtype=float)
        if (matrix.shape != (4, 4) or not np.isfinite(matrix).all()
                or not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-6)
                or not np.allclose(matrix[:3, :3] @ matrix[:3, :3].T, np.eye(3), atol=1e-4)
                or not np.isclose(np.linalg.det(matrix[:3, :3]), 1, atol=1e-4)):
            raise ValueError("Display transform must be a finite rigid 4×4 transform")
        display_transform = matrix.tolist()
    with db.connect(settings, write=True) as conn:
        row = db.assessment(conn, args.assessment)
        if row["input_revision"] != args.expected_input_revision or row["analysis_job_id"] != args.expected_analysis_job:
            raise db.Conflict("Presentation belongs to a different input or analysis")
        if conn.execute("SELECT 1 FROM jobs WHERE assessment_id=? AND status IN ('queued','running')", (args.assessment,)).fetchone():
            raise db.Conflict("Wait for the assessment's active job")
        scene = next((s for s in db.active_scenes(conn, args.assessment) if s["id"] == args.scene), None)
        if scene is None:
            raise db.Missing("Room not found")
        hazards = {h["id"]: json.loads(h["payload"]) for h in conn.execute(
            "SELECT * FROM hazards WHERE job_id=?", (args.expected_analysis_job,))}
        photos = {p["id"] for p in conn.execute("SELECT id FROM photos WHERE scene_id=? AND active=1", (args.scene,))}
        if any(hid not in hazards or not any(e["photo_id"] in photos for e in hazards[hid]["evidence"]) for hid in pins):
            raise ValueError("Pins must link current photographic findings in this room")
        payload = json.loads(scene["payload"])
        if payload.get("presentation") and not args.replace:
            raise db.Conflict("Room already has a presentation; keep the original attachment")
        target = settings.data / "assessments" / args.assessment / "assets" / (db.uid() + f"-{args.provenance}.glb")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.model, target)
        asset_id = db.store_asset(settings, conn, args.assessment, target.relative_to(settings.data).as_posix(),
                                  "model/gltf-binary", args.provenance, "derived")
        payload["presentation"] = {"asset_id": asset_id, "input_revision": row["input_revision"],
                                   "source_job_id": row["analysis_job_id"], "hazard_positions": pins,
                                   "provenance": args.provenance,
                                   "coordinate_space": "raw_scene_relative" if completed else "illustrative"}
        if display_transform is not None:
            payload["presentation"]["display_transform"] = display_transform
        conn.execute("UPDATE scenes SET payload=? WHERE id=?", (db.dumps(payload), args.scene))
    print(f"Attached {args.provenance} presentation; analytical coordinates and review decisions are unchanged.")


if __name__ == "__main__":
    main()
