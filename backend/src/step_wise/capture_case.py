"""Turn the reconstructed room in frontend/public/capture-data into a reviewable case.

The room geometry comes from the offline VGGT + Gaussian-splat capture; the case
photos are the views it uses, kept in demo/capture. The findings are the
recorded outputs of live TypeSafe Jev runs over those photos
(scripts/detect_capture_hazards.py; raw responses in
pitch/jev-results/2026-10-17/room-capture/). Each finding keeps the model's
presence and box-confidence scores and points at its raw record. The rug
cleared the automatic localization gate; the cable was flagged on presence and
is kept as a reviewer-confirmed approximate pin, exactly as the gates dictate.
"""
import io
import json

from PIL import Image

from step_wise import db
from step_wise.config import ROOT
from step_wise.models import AssessmentInput, Profile
from step_wise.pipeline.photos import ingest

VIEWER = ROOT / "frontend/public/capture-data"
PHOTOS = ROOT / "demo/capture"
# Long edge of the stored evidence photos: enough for the photo view, half the bytes of the 2048 px capture.
PHOTO_EDGE = 1600
ROOM = "Living room and kitchen"
# Evidence photos first, then a spread of the room. The 3D viewer carries all 39 views.
VIEWS = (23, 19, 33, 8, 0, 12, 28, 36)
PROFILE = {
    "description": "Going home after a hip fracture. Lives alone; her daughter lives in another city. Often gets up at night to go to the bathroom.",
    "mobility_aid": "walker", "aid_width_m": 0.62, "vision": "Cataracts, reduced vision in low light",
    "support_needs": "Caregiver 3 hours in the morning", "relevant_history": "Hip fracture (September 2026)",
    "daily_routes": ["bedroom → bathroom (night)", "bedroom → kitchen"],
}
# Review-layer copy for each recorded finding: the model proposes the detection,
# the reviewer confirms the priority and patient relevance.
FINDINGS = {
    "carpet": {"category": "rug", "priority": "urgent", "guidance_ids": ["floor-rug"],
               "profile_relevance": "Maria uses a walker: the wheels catch on the edges. It lies right where she stops to cook, on her bedroom → kitchen route.",
               "profile_fields": ["mobility_aid", "daily_routes"]},
    "cord": {"category": "cable", "priority": "important", "guidance_ids": ["floor-cable"],
             "profile_relevance": "With reduced vision in low light, Maria may not see the cable while she walks around the table with her walker.",
             "profile_fields": ["vision", "mobility_aid"]},
}


def display_copy(path):
    image = Image.open(path)
    image.thumbnail((PHOTO_EDGE, PHOTO_EDGE), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=95)
    return buffer.getvalue()


def import_capture(settings, viewer=VIEWER, photos=PHOTOS, label="Maria R. · 84"):
    db.init(settings)
    manifest = json.loads((viewer / "viewpoints.json").read_text())
    views = manifest["views"]
    marked = json.loads((viewer / "hazards.json").read_text())
    selected = list(dict.fromkeys([*(m["view_index"] for m in marked), *VIEWS]))
    inputs = AssessmentInput(label=label, profile=Profile.model_validate(PROFILE), profile_confirmed=True, staged_data_confirmed=True, processing_consent=True)
    aid = db.create_assessment(settings, inputs.model_dump(mode="json"))
    photo_ids = {}
    for index in selected:
        role = "overview" if index == selected[0] else "detail"
        photo_ids[index] = ingest(settings, aid, display_copy(photos / views[index]["image"]), ROOM, role, None)
    with db.connect(settings, write=True) as conn:
        row = db.assessment(conn, aid)
        scene_id = conn.execute("SELECT id FROM scenes WHERE assessment_id=?", (aid,)).fetchone()[0]
        sizes = {r["id"]: json.loads(r["payload"]) for r in conn.execute("SELECT id,payload FROM photos WHERE assessment_id=?", (aid,))}
        job_id = db.uid()
        hazards, pins = [], {}
        for mark in marked:
            text = FINDINGS[mark["id"]]
            photo_id = photo_ids[mark["view_index"]]
            width, height = sizes[photo_id]["width"], sizes[photo_id]["height"]
            l, t, r, b = mark["box"]
            evidence = [{"photo_id": photo_id, "box": [l * width, t * height, r * width, b * height]}]
            support = [photo_ids[i] for i, view in enumerate(views) if view["id"] in mark.get("source_photo_ids", [mark["photo_id"]]) and i in photo_ids] or [photo_id]
            anchor = {"scene_id": scene_id, "position_world": mark["position_world"], "method": mark["position_method"],
                      "source_photo_ids": support, "uncertainty": mark["uncertainty"]}
            uncertainty = (f"{mark['uncertainty']} Recorded response: presence {mark['presence']:.2f}, "
                           f"box confidence {mark['box_confidence']:.2f} - {mark['detection_record']}.")
            hazard = {**text, "observation": mark["observation"], "suggested_action": mark["action"],
                      "evidence": evidence, "measurement_ids": [], "anchor": anchor, "uncertainty": uncertainty}
            hazard_id = db.uid()
            hazards.append((hazard_id, hazard))
            pins[hazard_id] = {"pin": mark["id"], "view": mark["view_index"]}
        first = photo_ids[selected[0]]
        output = {"hazards": [h for _, h in hazards], "producer": "TypeSafe Jev 1.14 · recorded live runs over the case photos",
                  "provenance": "live",
                  "modes": {"reconstruction": "vggt_gaussian_splats_offline", "jev": "live", "gemma": "not_run", "avatar": "fixture"},
                  "coverage": [{"photo_id": first, "category": c, "coverage": "out_of_frame", "note": "Room not photographed"} for c in ("bathroom", "stairs", "lighting")],
                  "follow_up_requests": ["The bathroom and bedroom were not photographed: photos of the night route bedroom → bathroom are needed."],
                  "evidence_markers": {}, "warnings": []}
        conn.execute("INSERT INTO jobs(id,assessment_id,kind,status,input_revision,review_revision,stages,output,created_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (job_id, aid, "analyze", "succeeded", row["input_revision"], row["review_revision"], "{}", db.dumps(output), db.now(), db.now()))
        for hazard_id, hazard in hazards:
            conn.execute("INSERT INTO hazards VALUES(?,?,?,?)", (hazard_id, aid, job_id, db.dumps(hazard)))
        scene = {"status": "ready", "provenance": "live", "reconstruction_kind": "multiview", "scale_state": "unknown",
                 "bounds": manifest.get("bounds"),
                 "capture": {"viewer_url": "/local-room-viewer.html", "views": len(views), "pins": pins}}
        conn.execute("UPDATE scenes SET payload=? WHERE id=?", (db.dumps(scene), scene_id))
        conn.execute("UPDATE assessments SET analysis_job_id=?,status='review_ready',updated_at=? WHERE id=?", (job_id, db.now(), aid))
    return {"assessment_id": aid, "photos": len(photo_ids), "hazards": len(hazards)}
