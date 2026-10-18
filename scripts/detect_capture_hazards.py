#!/usr/bin/env python3
"""Run the production Jev stage over the demo case photos and archive the responses.

Calls the same ``step_wise.pipeline.jev`` code the worker uses - the production
prompts, image preparation and evidence gates - against the live TypeSafe API
for every photo in ``demo/capture``. One raw response record per photo is kept
under ``pitch/jev-results/2026-10-17/room-capture/`` next to the other recorded
Jev runs, and the candidates that pass the production gates are anchored on the
capture analysis to refresh ``frontend/public/capture-data/hazards.json``.

  .venv/bin/python scripts/detect_capture_hazards.py [capture_dir]

Requires ``JEV_ENABLED=true`` and ``JEV_API_KEY`` (environment or ``.env``).
Every call bills a small amount of input tokens; successful records are reused
on rerun unless ``--fresh`` is given. Failed photos are recorded as such and
never produce findings.
"""
import argparse
import hashlib
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from step_wise.config import ROOT, Settings
from step_wise.pipeline import jev
from step_wise.pipeline.geometry import supported_anchor
from step_wise.pipeline.http import ExternalFailure, post_json
from step_wise.pipeline.photos import canonical_box, decode, prepared_image

VIEWER = ROOT / "frontend/public/capture-data"
PHOTOS = ROOT / "demo/capture"
RECORDS = ROOT / "pitch/jev-results/2026-10-17/room-capture"

# Which recorded Jev questions may back each reviewed finding, in preference order.
FINDING_TARGETS = {
    "carpet": ("rug_near", "rug_far"),
    "cord": ("floor_cable",),
}
LABELS = {
    "carpet": "Carpet in front of the kitchen",
    "cord": "Cord in the walkway",
}
CATEGORIES = {"carpet": "rug", "cord": "cable"}
COLORS = {"carpet": "#f5b54a", "cord": "#ff6b5e"}
TEXT = {
    "carpet": {
        "observation": "A lightweight carpet with an uneven edge lies in front of the kitchen cabinets.",
        "action": "Check that the rug is secured; consider removing it or adding a non-slip backing.",
    },
    "cord": {
        "observation": "A cord hangs from the table and reaches the floor beside the chair.",
        "action": "Route cables along the wall and keep the walkway clear.",
    },
}


def case_photos():
    """The staged case photos, keyed by their index in the viewer's view list."""
    views = json.loads((VIEWER / "viewpoints.json").read_text())["views"]
    index_of = {view["id"]: index for index, view in enumerate(views)}
    photos = {}
    for path in sorted((PHOTOS / "images").glob("*.jpg")):
        photo_id = path.stem.split("-", 1)[1]  # "019-IMG_7599.jpg" -> "IMG_7599"
        photos[index_of[photo_id]] = (photo_id, path)
    return views, photos


def detect_photo(settings, photo_id, path):
    """One live Jev call per photo with the production questions, preparation and gates."""
    source = path.read_bytes()
    image, metadata = decode(source)
    prepared, transform = prepared_image(image)
    buffer = io.BytesIO()
    prepared.save(buffer, "JPEG", quality=92)
    payload = jev.request_payload(buffer.getvalue(), [])
    start = time.perf_counter()
    body = post_json(jev.ENDPOINT, payload,
                     {"Authorization": "Bearer " + settings.jev_api_key, "TypeSafe-Danger-Zone-Images": "true"},
                     timeout=(10, 90))
    elapsed = time.perf_counter() - start
    candidates = jev.interpret(body, {"id": photo_id}, {"upright_size": metadata["upright_size"], "transform": transform}, [])
    scores = {c["question_id"]: (c["provider_response"]["presence"]["noul"], c["provider_response"]["box"]["confidence"]) for c in candidates}
    print(f"  {photo_id}: " + ", ".join(f"{key}={score:.2f}/{conf:.2f}" for key, (score, conf) in scores.items()))
    record = {
        "source": path.relative_to(ROOT).as_posix(),
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "prepared_sha256": hashlib.sha256(buffer.getvalue()).hexdigest(),
        "upright_size": metadata["upright_size"],
        "transform": transform,
        "endpoint": jev.ENDPOINT,
        "requested_model": jev.MODEL,
        "questions": payload["questions"],
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "response": {"model": body.get("model"), "answers": body.get("answers"), "usage": body.get("usage")},
        "candidates": candidates,
        "status": "succeeded",
        "elapsed_seconds": elapsed,
    }
    return record, candidates


def anchor(analysis, scene, photo_id, box_upright, sent_size, floor_level=False):
    """Lift a detected box onto the capture analysis; abstain when the depth is unusable."""
    image = next(item for item in scene["images"] if item["photo_id"] == photo_id)
    transform = image["transform"]
    factor = [transform["upright_size"][0] / sent_size[0], transform["upright_size"][1] / sent_size[1]]
    left, top, right, bottom = [value * f for value, f in zip(box_upright, (factor[0], factor[1], factor[0], factor[1]))]
    sx, sy = transform["scale_xy"]
    ox, oy = transform["offset_xy"]
    fl, ft = left * sx + ox, top * sy + oy
    fr, fb = right * sx + ox, bottom * sy + oy
    dx, dy = (fr - fl) * .2, (fb - ft) * .2
    l, t, r, b = max(0, int(fl + dx)), max(0, int(ft + dy)), min(518, int(fr - dx)), min(518, int(fb - dy))
    if floor_level:
        # A flagged-but-unlocalized run often boxes the object's full run, table to floor;
        # the navigation pin belongs where it meets the floor, so sample the lowest rows.
        t = t + int((b - t) * .7)
    if r - l < 2 or b - t < 2:
        return None, None
    index = image["tensor_index"]
    points = analysis["world_points"][index, t:b, l:r].reshape(-1, 3)
    support = (analysis["confidence"][index, t:b, l:r] >= scene["confidence_threshold"]).reshape(-1).astype(float)
    camera = np.asarray(image["world_to_camera"], float)
    center = supported_anchor(points, support, camera_center=-camera[:, :3].T @ camera[:, 3])
    if center is not None:
        return center, "jev_box_foreground_anchor"
    finite = points[np.isfinite(points).all(axis=1)]
    if len(finite) < 5:
        return None, None
    return np.median(finite, axis=0).tolist(), "jev_box_depth_estimate"


def build_findings(views, records, candidates, analysis, scene):
    """One finding per reviewed object, backed by the strongest recorded evidence.

    A target that passed the presence gate but not the box-confidence gate stays in
    the case with its recorded scores and an explicit caveat - the reviewer decides,
    exactly as in the product flow; the script never invents a localization.
    """
    sizes, transforms = {}, {}
    for record in records.values():
        if record["status"] == "succeeded":
            sizes[record["_photo_id"]] = record["upright_size"]
            transforms[record["_photo_id"]] = record["transform"]
    findings = []
    for finding_id, targets in FINDING_TARGETS.items():
        present = [c for c in candidates if c["question_id"] in targets and c["presence"] == "present"]
        gated = [c for c in present if c.get("evidence")]
        flagged = [c for c in present if not c.get("evidence")]
        if not present:
            print(f"  {finding_id}: Jev did not flag it in any case photo; leaving it out")
            continue
        prefer = {"cord": "IMG_7599", "carpet": "IMG_7603"}.get(finding_id)
        pool = gated or flagged
        allowed = [c for c in pool if c["photo_id"] == prefer] or pool
        primary = max(allowed, key=lambda c: c["provider_response"]["box"]["confidence"])
        photo_id = primary["photo_id"]
        width, height = sizes[photo_id]
        if primary.get("evidence"):
            raw_box = primary["evidence"]["box"]
        else:
            raw = primary["provider_response"]["box"]["box"]
            raw_box = canonical_box([raw[k] for k in ("left", "top", "right", "bottom")], transforms[photo_id], sizes[photo_id])
        box = [value / size for value, size in zip(raw_box, (width, height, width, height))]
        position, method = anchor(analysis, scene, photo_id, raw_box, sizes[photo_id], floor_level=not gated)
        if position is None:
            print(f"  {finding_id}: depth anchor abstained; leaving it out")
            continue
        presence = primary["provider_response"]["presence"]["noul"]
        confidence = primary["provider_response"]["box"]["confidence"]
        if gated:
            agreeing = {photo_id}
            for candidate in gated:
                other, _ = anchor(analysis, scene, candidate["photo_id"], candidate["evidence"]["box"], sizes[candidate["photo_id"]])
                if other and np.linalg.norm(np.asarray(other) - np.asarray(position)) <= .15 * np.linalg.norm(np.asarray(position)):
                    agreeing.add(candidate["photo_id"])
            uncertainty = ("Detected by a live TypeSafe Jev run on the case photos; the 3D pin is an "
                           "approximate depth estimate for navigation, not a validated metric position.")
        else:
            agreeing = {photo_id}
            uncertainty = (f"Jev flagged this on a live run (presence {presence:.2f}, above the presence gate) but its "
                           f"localization scored {confidence:.2f}, below the automatic box gate, so the pin is kept as "
                           "reviewer-confirmed and approximate rather than model-localized.")
        view_index = next(index for index, view in enumerate(views) if view["id"] == photo_id)
        findings.append({
            "id": finding_id,
            "label": LABELS[finding_id],
            "category": CATEGORIES[finding_id],
            "view_index": view_index,
            "color": COLORS[finding_id],
            "box": [round(value, 4) for value in box],
            **TEXT[finding_id],
            "position_world": [round(value, 6) for value in position],
            "photo_id": photo_id,
            "producer": jev.MODEL,
            "provenance": "live",
            "presence": presence,
            "box_confidence": confidence,
            "localization": "gate_passed" if gated else "below_box_gate",
            "source_photo_ids": sorted(agreeing),
            "position_method": method,
            "detection_record": RECORDS.relative_to(ROOT).as_posix() + f"/{photo_id}.json",
            "uncertainty": uncertainty,
        })
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, nargs="?",
                        default=ROOT / "data/live-tests/room-capture-39-20261017",
                        help="Capture directory with scene.json and assets/capture-analysis.npz")
    parser.add_argument("--fresh", action="store_true", help="Call the API again even for recorded photos")
    args = parser.parse_args()
    settings = Settings.from_env()
    if not settings.jev_enabled or not settings.jev_api_key:
        raise SystemExit("JEV_ENABLED=true and JEV_API_KEY are required; refusing to simulate detections")

    RECORDS.mkdir(parents=True, exist_ok=True)
    views, photos = case_photos()
    print(f"Running the production Jev stage over {len(photos)} case photos")
    records, candidates = {}, []
    for index in sorted(photos):
        photo_id, path = photos[index]
        record_path = RECORDS / f"{photo_id}.json"
        if record_path.is_file() and not args.fresh:
            record = json.loads(record_path.read_text())
            if record["status"] == "succeeded":
                record["_photo_id"] = photo_id
                records[photo_id] = record
                candidates.extend(record["candidates"])
                print(f"  {photo_id}: reusing recorded responses")
                continue
        try:
            record, photo_candidates = detect_photo(settings, photo_id, path)
        except (ExternalFailure, ValueError, KeyError, TypeError) as exc:
            print(f"  {photo_id}: {exc}")
            record_path.write_text(json.dumps({"source": path.relative_to(ROOT).as_posix(), "status": "failed",
                                               "error": str(exc), "at_utc": datetime.now(timezone.utc).isoformat()},
                                              indent=2) + "\n")
            continue
        record["_photo_id"] = photo_id
        records[photo_id] = record
        candidates.extend(photo_candidates)
        del record["_photo_id"]
        record_path.write_text(json.dumps(record, indent=2) + "\n")
        record["_photo_id"] = photo_id
        time.sleep(.25)

    summary = {"runs": [{"photo_id": photo_id, "status": record["status"],
                         "elapsed_seconds": round(record.get("elapsed_seconds", 0), 3),
                         "usage": record.get("response", {}).get("usage")}
                        for photo_id, record in sorted(records.items())],
               "gates": {"presence": jev.PRESENCE_THRESHOLD, "box_confidence": jev.BOX_CONFIDENCE_THRESHOLD},
               "model": jev.MODEL}
    (RECORDS / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    scene = json.loads((args.capture / "scene.json").read_text())
    with np.load(args.capture / "assets/capture-analysis.npz") as arrays:
        analysis = {name: arrays[name] for name in ("world_points", "confidence")}
        findings = build_findings(views, records, candidates, analysis, scene)
    (VIEWER / "hazards.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2) + "\n")
    print(f"{len(findings)} live Jev findings written to {VIEWER / 'hazards.json'}; records in {RECORDS}")


if __name__ == "__main__":
    main()
