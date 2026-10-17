import base64
import hashlib
import json
import re
import logging
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from step_wise import db, snapshot
from step_wise.config import Settings
from step_wise.models import AssessmentBundle, AssessmentEdit, AvatarArtifact, AssessmentInput, CalibrationInput, Hazard, ProfileConfirmation, ReviewInput, Revisions, SceneLinkInput
from step_wise.pipeline.photos import ingest
from step_wise.pipeline import clinical
from step_wise.pipeline.registration import bind_metric_link, compose_rooms
from step_wise.pipeline.http import ExternalFailure
from step_wise.pipeline.reports import release
from step_wise.validation import validate_hazard


def room_presentation(scene, assessment):
    value = scene.get("presentation")
    if (not value or value.get("source_job_id") != assessment["analysis_job_id"]
            or value.get("input_revision") != assessment["input_revision"]):
        return None
    provenance = value.get("provenance", "illustrative")
    if provenance not in {"illustrative", "photo_completed"}:
        return None
    completed = provenance == "photo_completed"
    if completed and value.get("coordinate_space") != "raw_scene_relative":
        return None
    return {"model_url": f"/api/assets/{value['asset_id']}", "provenance": provenance,
            "coordinate_space": "raw_scene_relative" if completed else "illustrative",
            "display_transform": value.get("display_transform") if completed else None,
            "label": ("Ricostruzione fotografica · superfici mancanti completate a mano" if completed
                      else "Modello illustrativo · proporzioni e posizioni non misurate"),
            "hazard_positions": {} if completed else value["hazard_positions"]}


def current_findings(conn, job_id, output):
    result = []
    for hazard in conn.execute("SELECT * FROM hazards WHERE job_id=? ORDER BY rowid", (job_id,)):
        review = conn.execute("SELECT * FROM reviews WHERE hazard_id=? ORDER BY review_revision DESC LIMIT 1", (hazard["id"],)).fetchone()
        payload = json.loads(hazard["payload"])
        marker = output.get("evidence_markers", {}).get(hazard["id"])
        if review and (review["decision"] == "reject" or json.loads(review["effective"]).get("evidence") != payload["evidence"]):
            marker = None
        result.append({"id": hazard["id"], **payload, "evidence_marker": marker,
                       "producer": output.get("producer"), "provenance": output.get("provenance"),
                       "review": {**dict(review), "effective": json.loads(review["effective"])} if review else None})
    return result


def document_metadata(document):
    return {"id": document["id"], "format": document["format"], "page_count": document["page_count"],
            "created_at": document["created_at"], "asset_url": f"/api/assets/{document['asset_id']}"}


class UploadLimit:
    def __init__(self, app, limit):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or not scope["path"].endswith(("/photos", "/clinical-documents")):
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"error": "Invalid content length"}, status_code=422)(scope, receive, send)
        if declared > self.limit:
            return await JSONResponse({"error": "Upload body exceeds the configured limit"}, status_code=413)(scope, receive, send)
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > self.limit:
                raise HTTPException(413, "Upload body exceeds the configured limit")
            return message

        return await self.app(scope, bounded_receive, send)


# The only mutations a public demo visitor can make on their private copy.
DEMO_MUTATIONS = re.compile(r"^/api/(demo/session|assessments/[^/]+/(reviews/[^/]+|reports)|reports/[^/]+/(release|revoke))$")


INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)


def content_security_policy(script_hashes=()):
    # The capture viewer sorts Gaussian splats in a blob: worker that compiles WebAssembly.
    scripts = " ".join(["'self'", "'wasm-unsafe-eval'", *(f"'sha256-{h}'" for h in script_hashes)])
    return ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            f"script-src {scripts}; worker-src 'self' blob:; connect-src 'self' blob:; object-src 'none'; frame-ancestors 'self'; base-uri 'none'")


@lru_cache(maxsize=64)
def page_script_hashes(path, modified_ns):
    """Hashes of a static page's inline scripts (Next.js bootstrap), recomputed when the file changes."""
    html = Path(path).read_text()
    return tuple(base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() for body in INLINE_SCRIPT.findall(html) if body.strip())


def create_app(settings=None):
    settings = settings or Settings.from_env()
    db.init(settings)
    logging.getLogger("uvicorn.error").info("Step Wise execution modes: %s", settings.modes())
    app = FastAPI(title="Step Wise", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.add_middleware(UploadLimit, limit=settings.max_file_bytes + 512 * 1024)

    def reviewer():
        return settings.reviewer_identity

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            if (settings.data / "baseline.json").exists():
                return JSONResponse({"error": "Frozen baseline is read-only; create a new case attempt"}, status_code=409)
            origin = request.headers.get("origin")
            same_origin = not origin or (urlsplit(origin).scheme, urlsplit(origin).netloc) == (request.url.scheme, request.url.netloc) or origin.rstrip("/") in settings.allowed_origins
            if request.headers.get("sec-fetch-site") == "cross-site" or not same_origin:
                return JSONResponse({"error": "Cross-origin mutation denied"}, status_code=403)
            if settings.demo and not DEMO_MUTATIONS.match(request.url.path):
                return JSONResponse({"error": "Nella demo pubblica si possono solo revisionare i pericoli e rilasciare il report."}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Exported frontend pages carry their own policy that admits only their inline scripts by hash.
        response.headers.setdefault("Content-Security-Policy", content_security_policy())
        return response

    @app.exception_handler(db.Missing)
    async def missing(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=404)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return JSONResponse({"error": exc.detail if isinstance(exc.detail, str) else "Invalid request"}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(db.Conflict)
    async def conflict(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"error": "Invalid input or incompatible evidence"}, status_code=422)

    @app.exception_handler(OverflowError)
    async def oversized(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=413)

    @app.exception_handler(ExternalFailure)
    async def external_failure(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=503)

    @app.exception_handler(clinical.ClinicalInputError)
    async def clinical_input_error(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({"error": "Invalid request", "fields": [".".join(map(str, e["loc"])) for e in exc.errors()]}, status_code=422)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "schema_version": 3, "modes": settings.modes(), "staged_data_only": True, "demo": settings.demo,
                "photo_limits": {"max_photos": settings.max_photos, "max_file_bytes": settings.max_file_bytes,
                                 "frame_cap": settings.frame_cap}}

    @app.post("/api/assessments", status_code=201)
    def create(payload: AssessmentInput, identity=Depends(reviewer)):
        return {"id": db.create_assessment(settings, payload.model_dump(mode="json"))}

    @app.post("/api/demo/session", status_code=201)
    def demo_session():
        if not settings.demo:
            raise db.Missing("Demo mode is off")
        snapshot.prune(settings)
        return {"id": snapshot.clone_case(settings)}

    @app.get("/api/assessments")
    def listing(identity=Depends(reviewer)):
        # Visitor copies are private: knowing an ID is the only way into one.
        if settings.demo:
            return []
        with db.connect(settings) as conn:
            return [{"id": r["id"], "label": json.loads(r["input_json"])["label"], "status": r["status"], "created_at": r["created_at"]} for r in conn.execute("SELECT * FROM assessments ORDER BY created_at DESC LIMIT 100")]

    @app.get("/api/assessments/{aid}")
    def get_assessment(aid: str, identity=Depends(reviewer)):
        with db.connect(settings) as conn:
            row = db.assessment(conn, aid)
            row["inputs"] = json.loads(row.pop("input_json"))
            row["photos"] = [{"id": p["id"], "scene_id": p["scene_id"], "role": p["role"], "asset_url": f"/api/assets/{p['asset_id']}", **json.loads(p["payload"])} for p in conn.execute("SELECT * FROM photos WHERE assessment_id=? AND active=1 ORDER BY rowid", (aid,))]
            row["clinical_documents"] = [document_metadata(d) for d in conn.execute("SELECT * FROM clinical_documents WHERE assessment_id=? ORDER BY created_at", (aid,))]
            row["jobs"] = [dict(j) for j in conn.execute("SELECT id,kind,status,stage,error,created_at FROM jobs WHERE assessment_id=? ORDER BY created_at DESC", (aid,))]
            row["reports"] = [dict(r) for r in conn.execute("SELECT id,status,input_revision,review_revision FROM reports WHERE assessment_id=? ORDER BY rowid DESC", (aid,))]
            return row

    @app.patch("/api/assessments/{aid}")
    def edit(aid: str, payload: AssessmentEdit, identity=Depends(reviewer)):
        with db.connect(settings, write=True) as conn:
            row = db.editable(conn, aid, payload.expected_input_revision)
            previous = json.loads(row["input_json"])
            updated = payload.model_dump(mode="json", exclude={"expected_input_revision"})
            cited = [claim for claim in updated["clinical_facts"] if claim["origin"] == "document"]
            prior_cited = [claim for claim in previous.get("clinical_facts", []) if claim["origin"] == "document"]
            if cited and (sorted(map(db.dumps, cited)) != sorted(map(db.dumps, prior_cited)) or updated["profile"] != previous["profile"]):
                raise ValueError("Use profile confirmation to change a document-derived profile or its cited facts")
            if any(updated.get(key) != previous.get(key) for key in ("profile", "clinical_facts", "profile_confirmed")):
                updated["clinical_external_processing_consent"] = False
            conn.execute("UPDATE assessments SET input_json=? WHERE id=?", (db.dumps(updated), aid))
            db.invalidate(conn, aid, preserve_scenes=True)
        return {"status": "saved"}

    @app.post("/api/assessments/{aid}/photos", status_code=201)
    async def upload(aid: str, file: UploadFile = File(...), scene_label: str = Form(..., min_length=1, max_length=120), role: str = Form("overview"), expected_input_revision: int = Form(...), identity=Depends(reviewer)):
        try:
            if role not in {"overview", "detail"}:
                raise ValueError("Invalid photo role")
            data = bytearray()
            while chunk := await file.read(64 * 1024):
                data.extend(chunk)
                if len(data) > settings.max_file_bytes:
                    raise OverflowError("Photo exceeds file limit")
            photo_id = await run_in_threadpool(ingest, settings, aid, bytes(data), scene_label, role, expected_input_revision)
            return {"id": photo_id}
        finally:
            await file.close()

    @app.post("/api/assessments/{aid}/clinical-documents", status_code=201)
    async def upload_clinical(aid: str, file: UploadFile = File(...), expected_input_revision: int = Form(...), identity=Depends(reviewer)):
        try:
            data = bytearray()
            while chunk := await file.read(64 * 1024):
                data.extend(chunk)
                if len(data) > settings.max_file_bytes:
                    raise OverflowError("Clinical report exceeds file limit")
            document_id = await run_in_threadpool(clinical.ingest, settings, aid, bytes(data), file.filename or "", expected_input_revision)
            return {"id": document_id}
        finally:
            await file.close()

    @app.get("/api/assessments/{aid}/clinical-documents")
    def clinical_documents(aid: str, identity=Depends(reviewer)):
        with db.connect(settings) as conn:
            db.assessment(conn, aid)
            return [document_metadata(r)
                    for r in conn.execute("SELECT * FROM clinical_documents WHERE assessment_id=? ORDER BY created_at", (aid,))]

    @app.post("/api/assessments/{aid}/profile/draft")
    def profile_draft(aid: str, payload: Revisions, identity=Depends(reviewer)):
        with db.connect(settings) as conn:
            db.editable(conn, aid, payload.expected_input_revision, payload.expected_review_revision)
        return clinical.create_draft(settings, aid, payload.expected_input_revision)

    @app.get("/api/assessments/{aid}/profile/draft")
    def get_profile_draft(aid: str, identity=Depends(reviewer)):
        with db.connect(settings) as conn:
            row = db.assessment(conn, aid)
            draft = conn.execute("SELECT input_revision,draft_json FROM clinical_profile_drafts WHERE assessment_id=?", (aid,)).fetchone()
            if draft is None or draft["input_revision"] != row["input_revision"]:
                raise db.Missing("No current clinical profile draft")
            return json.loads(draft["draft_json"])

    @app.put("/api/assessments/{aid}/profile/confirm")
    def confirm_profile(aid: str, payload: ProfileConfirmation, identity=Depends(reviewer)):
        with db.connect(settings, write=True) as conn:
            row = db.editable(conn, aid, payload.expected_input_revision, payload.expected_review_revision)
            draft_row = conn.execute("SELECT input_revision,draft_json FROM clinical_profile_drafts WHERE assessment_id=?", (aid,)).fetchone()
            docs = [{"id": d["id"], "pages": json.loads(d["extracted_json"])} for d in conn.execute("SELECT * FROM clinical_documents WHERE assessment_id=?", (aid,))]
            source_claims = [c.model_dump(mode="json") for c in payload.claims]
            inputs = json.loads(row["input_json"])
            if any(c["origin"] == "document" for c in source_claims):
                allowed = [c for c in inputs.get("clinical_facts", []) if c.get("reviewer_confirmed") and c["origin"] == "document"]
                if draft_row is not None and draft_row["input_revision"] == row["input_revision"]:
                    allowed.extend(json.loads(draft_row["draft_json"])["claims"])
                if not allowed:
                    raise db.Conflict("Create a current source-checked profile draft before confirming document-derived claims")
                clinical.validate_confirmed_claims(payload.claims, docs, {"claims": allowed})
            elif any(not c.reviewer_confirmed for c in payload.claims):
                raise ValueError("Every profile claim must be explicitly reviewer-confirmed")
            inputs["profile"] = payload.profile.model_dump(mode="json")
            inputs["clinical_facts"] = source_claims
            inputs["profile_confirmed"] = True
            inputs["clinical_external_processing_consent"] = False
            conn.execute("UPDATE assessments SET input_json=? WHERE id=?", (db.dumps(inputs), aid))
            db.invalidate(conn, aid, preserve_scenes=True)
        return {"status": "confirmed", "input_revision": payload.expected_input_revision + 1}

    @app.delete("/api/assessments/{aid}/photos/{photo_id}")
    def remove_photo(aid: str, photo_id: str, expected_input_revision: int, identity=Depends(reviewer)):
        with db.connect(settings, write=True) as conn:
            db.editable(conn, aid, expected_input_revision)
            photo = conn.execute("SELECT scene_id FROM photos WHERE id=? AND assessment_id=? AND active=1", (photo_id, aid)).fetchone()
            if photo is None:
                raise db.Missing("Photo not found")
            conn.execute("UPDATE photos SET active=0 WHERE id=? AND assessment_id=?", (photo_id, aid))
            db.invalidate_scene(conn, aid, photo["scene_id"])
        return {"status": "removed"}

    @app.post("/api/assessments/{aid}/analyze", status_code=202)
    def analyze(aid: str, payload: Revisions, identity=Depends(reviewer)):
        return {"job_id": db.enqueue(settings, aid, "analyze", payload.expected_input_revision, payload.expected_review_revision)}

    @app.get("/api/jobs/{jid}")
    def job(jid: str, identity=Depends(reviewer)):
        with db.connect(settings) as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
            if row is None:
                raise db.Missing("Job not found")
            value = dict(row)
            # Manifests contain internal artifact paths; expose only stage status/timing/provenance.
            value["stages"] = {k: {f: v.get(f) for f in ("status", "elapsed_seconds", "provenance", "warnings")} for k, v in json.loads(value["stages"]).items()}
            value["failed_stages"] = [stage for stage, manifest in value["stages"].items() if manifest["status"] == "failed"]
            value["output"] = {"report_id": json.loads(value["output"]).get("report_id")} if value["kind"] == "report" else {}
            return value

    @app.get("/api/assessments/{aid}/findings")
    def findings(aid: str, identity=Depends(reviewer)):
        with db.connect(settings) as conn:
            row = db.assessment(conn, aid)
            output_row = conn.execute("SELECT output FROM jobs WHERE id=?", (row["analysis_job_id"],)).fetchone()
            output = json.loads(output_row[0]) if output_row else {}
            hazards = current_findings(conn, row["analysis_job_id"], output)
            return {"hazards": hazards, "candidates": [json.loads(c[0]) for c in conn.execute("SELECT payload FROM candidates WHERE job_id=?", (row["analysis_job_id"],))], "coverage": output.get("coverage", []), "follow_up_requests": output.get("follow_up_requests", []), "provenance": output.get("provenance"), "modes": output.get("modes", settings.modes())}

    @app.get("/api/assessments/{aid}/scenes")
    def scenes(aid: str, identity=Depends(reviewer)):
        with db.connect(settings) as conn:
            assessment = db.assessment(conn, aid)
            result = []
            for s in db.active_scenes(conn, aid):
                payload = json.loads(s["payload"])
                result.append({"id": s["id"], "label": s["label"], **{k: v for k, v in payload.items() if not k.endswith("path") and k not in {"images", "presentation"}},
                               "presentation": room_presentation(payload, assessment),
                               "ply_url": f"/api/assets/{payload['ply_asset_id']}" if payload.get("ply_asset_id") else None})
            return result

    @app.get("/api/assessments/{aid}/scene-links")
    def scene_links(aid: str, identity=Depends(reviewer)):
        with db.connect(settings) as conn:
            db.assessment(conn, aid)
            scenes = {scene["id"]: json.loads(scene["payload"]) for scene in db.active_scenes(conn, aid)}
            return db.current_scene_links(conn, aid, scenes)

    @app.put("/api/assessments/{aid}/scenes/{scene_id}/calibration")
    def calibrate_scene(aid: str, scene_id: str, payload: CalibrationInput, identity=Depends(reviewer)):
        from step_wise.pipeline.calibration import validate_calibration

        with db.connect(settings, write=True) as conn:
            row = db.editable(conn, aid, payload.expected_input_revision, payload.expected_review_revision)
            scene_row = next((scene for scene in db.active_scenes(conn, aid) if scene["id"] == scene_id), None)
            if scene_row is None:
                raise db.Missing("Room not found")
            scene = json.loads(scene_row["payload"])
            if (scene.get("status") != "ready" or scene.get("provenance") != "live"
                    or scene.get("reconstruction_kind") != "multiview" or scene.get("raw_coordinate_units") != "relative"):
                raise db.Conflict("Metric calibration requires a ready live multiview scene in relative coordinates")
            active_photos = {photo["id"] for photo in conn.execute(
                "SELECT id FROM photos WHERE assessment_id=? AND scene_id=? AND active=1", (aid, scene_id))}
            selected_photos = set(scene.get("selected_photo_ids", []))
            supplied_photos = {photo_id for segment in payload.fit_segments + payload.holdout_segments
                               for photo_id in segment.source_photo_ids}
            if not supplied_photos.issubset(active_photos & selected_photos):
                raise ValueError("Calibration evidence must reference active reconstruction photos from this room")

            result = validate_calibration(payload)
            calibration = {"method": "reviewer_measured_holdout", "reviewer": identity,
                           "created_at": db.now(), "source": "reviewer-entered raw scene point pairs",
                           "coordinate_space": "raw_scene_coordinates_before_display_transform",
                           "fit_segment_count": len(payload.fit_segments),
                           "holdout_segment_count": len(payload.holdout_segments),
                           "maximum_holdout_relative_error": payload.maximum_holdout_relative_error, **result}
            for measurement in conn.execute("SELECT id,payload FROM measurements WHERE assessment_id=? AND scene_id=?", (aid, scene_id)).fetchall():
                if json.loads(measurement["payload"]).get("kind") == "scale_calibration":
                    conn.execute("DELETE FROM measurements WHERE id=?", (measurement["id"],))
            measurement_id = db.uid()
            conn.execute("INSERT INTO measurements VALUES(?,?,?,?)",
                         (measurement_id, aid, scene_id, db.dumps({"kind": "scale_calibration", **calibration})))
            scene.update({"scale_state": "metric_calibrated", "scale_factor": result["scale_factor_m_per_unit"],
                          "calibration": {**calibration, "measurement_id": measurement_id}})
            conn.execute("UPDATE scenes SET payload=? WHERE id=? AND assessment_id=?", (db.dumps(scene), scene_id, aid))
            next_review_revision = row["review_revision"] + 1
            status = "review_ready" if row["analysis_job_id"] else row["status"]
            conn.execute("UPDATE assessments SET review_revision=?,status=?,updated_at=? WHERE id=?",
                         (next_review_revision, status, db.now(), aid))
        return {"status": "calibrated", "scene_id": scene_id,
                "scale_factor_m_per_unit": result["scale_factor_m_per_unit"],
                "holdout_max_relative_error": result["holdout_max_relative_error"],
                "review_revision": next_review_revision}

    @app.delete("/api/assessments/{aid}/scenes/{scene_id}/calibration")
    def remove_scene_calibration(aid: str, scene_id: str, expected_input_revision: int,
                                 expected_review_revision: int, identity=Depends(reviewer)):
        with db.connect(settings, write=True) as conn:
            row = db.editable(conn, aid, expected_input_revision, expected_review_revision)
            scene_row = next((scene for scene in db.active_scenes(conn, aid) if scene["id"] == scene_id), None)
            if scene_row is None:
                raise db.Missing("Room not found")
            scene = json.loads(scene_row["payload"])
            removed = scene.get("scale_state") == "metric_calibrated"
            if not removed:
                raise db.Missing("Room calibration not found")
            for measurement in conn.execute("SELECT id,payload FROM measurements WHERE assessment_id=? AND scene_id=?", (aid, scene_id)).fetchall():
                if json.loads(measurement["payload"]).get("kind") == "scale_calibration":
                    conn.execute("DELETE FROM measurements WHERE id=?", (measurement["id"],))
            scene.update({"scale_state": "unknown", "scale_factor": None, "calibration": None})
            conn.execute("UPDATE scenes SET payload=? WHERE id=? AND assessment_id=?", (db.dumps(scene), scene_id, aid))
            next_review_revision = row["review_revision"] + 1
            status = "review_ready" if row["analysis_job_id"] else row["status"]
            conn.execute("UPDATE assessments SET review_revision=?,status=?,updated_at=? WHERE id=?",
                         (next_review_revision, status, db.now(), aid))
        return {"status": "removed", "review_revision": next_review_revision}

    @app.put("/api/assessments/{aid}/scene-links/{from_scene_id}/{to_scene_id}")
    def put_scene_link(aid: str, from_scene_id: str, to_scene_id: str, payload: SceneLinkInput, identity=Depends(reviewer)):
        if from_scene_id == to_scene_id:
            raise ValueError("A room cannot be linked to itself")
        with db.connect(settings, write=True) as conn:
            row = db.editable(conn, aid, payload.expected_input_revision, payload.expected_review_revision)
            scenes = {scene["id"]: json.loads(scene["payload"]) for scene in db.active_scenes(conn, aid)}
            if from_scene_id not in scenes or to_scene_id not in scenes:
                raise db.Missing("Room not found")
            if conn.execute("SELECT 1 FROM scene_links WHERE assessment_id=? AND from_scene_id=? AND to_scene_id=?",
                            (aid, to_scene_id, from_scene_id)).fetchone():
                raise db.Conflict("A link already exists in the opposite direction")
            expected_photo_scenes = {}
            photo_ids = []
            for from_photo_id, to_photo_id in payload.overlap_photo_pairs:
                photo_ids.extend((from_photo_id, to_photo_id))
                expected_photo_scenes[from_photo_id] = from_scene_id
                expected_photo_scenes[to_photo_id] = to_scene_id
            if len(photo_ids) != len(set(photo_ids)):
                raise ValueError("Each room-link evidence photo may appear only once")
            actual_photo_scenes = {r["id"]: r["scene_id"] for r in conn.execute(
                f"SELECT id,scene_id FROM photos WHERE assessment_id=? AND active=1 AND id IN ({','.join('?' for _ in expected_photo_scenes)})",
                (aid, *expected_photo_scenes))} if expected_photo_scenes else {}
            if actual_photo_scenes != expected_photo_scenes:
                raise ValueError("Room-link evidence must use active photos from the specified rooms")
            value = payload.model_dump(mode="json", exclude={"expected_input_revision", "expected_review_revision"})
            try:
                value = bind_metric_link(scenes, from_scene_id, to_scene_id, value)
            except ValueError as exc:
                raise db.Conflict(str(exc)) from None
            other_links = [link for link in db.current_scene_links(conn, aid, scenes)
                           if (link["from_scene_id"], link["to_scene_id"]) != (from_scene_id, to_scene_id)]
            compose_rooms(scenes, [*other_links, {"from_scene_id": from_scene_id, "to_scene_id": to_scene_id, **value}])
            existing = conn.execute("SELECT id FROM scene_links WHERE assessment_id=? AND from_scene_id=? AND to_scene_id=?",
                                    (aid, from_scene_id, to_scene_id)).fetchone()
            if existing:
                link_id = existing["id"]
                conn.execute("UPDATE scene_links SET payload=?,created_at=? WHERE id=?", (db.dumps(value), db.now(), link_id))
            else:
                link_id = db.uid()
                conn.execute("INSERT INTO scene_links VALUES(?,?,?,?,?,?)",
                             (link_id, aid, from_scene_id, to_scene_id, db.dumps(value), db.now()))
            next_review_revision = row["review_revision"] + 1
            status = "review_ready" if row["analysis_job_id"] else row["status"]
            conn.execute("UPDATE assessments SET review_revision=?,status=?,updated_at=? WHERE id=?",
                         (next_review_revision, status, db.now(), aid))
        return {"id": link_id, "status": "saved", "review_revision": next_review_revision}

    @app.delete("/api/assessments/{aid}/scene-links/{from_scene_id}/{to_scene_id}")
    def delete_scene_link(aid: str, from_scene_id: str, to_scene_id: str, expected_input_revision: int,
                          expected_review_revision: int, identity=Depends(reviewer)):
        with db.connect(settings, write=True) as conn:
            row = db.editable(conn, aid, expected_input_revision, expected_review_revision)
            changed = conn.execute("DELETE FROM scene_links WHERE assessment_id=? AND from_scene_id=? AND to_scene_id=?",
                                   (aid, from_scene_id, to_scene_id)).rowcount
            if not changed:
                raise db.Missing("Room link not found")
            next_review_revision = row["review_revision"] + 1
            status = "review_ready" if row["analysis_job_id"] else row["status"]
            conn.execute("UPDATE assessments SET review_revision=?,status=?,updated_at=? WHERE id=?",
                         (next_review_revision, status, db.now(), aid))
        return {"status": "removed", "review_revision": next_review_revision}

    def analysis_output(conn, row):
        """Current analysis output, else the preserved local artifacts of this revision's latest failed attempt."""
        job = conn.execute("SELECT output FROM jobs WHERE id=?", (row["analysis_job_id"],)).fetchone() if row["analysis_job_id"] else None
        if job:
            return json.loads(job["output"])
        partial = conn.execute("SELECT output FROM jobs WHERE assessment_id=? AND kind='analyze' AND input_revision=? AND status='failed' ORDER BY created_at DESC LIMIT 1",
                               (row["id"], row["input_revision"])).fetchone()
        return json.loads(partial["output"]).get("partial_results", {}) if partial else {}

    def avatar_view(output):
        avatar = output.get("avatar", {})

        def url(key):
            return f"/api/assets/{avatar[key]}" if avatar.get(key) else None
        return {**avatar, "glb_url": url("asset_id"), "preview_url": url("preview_asset_id"),
                "profile_json_url": url("profile_json_asset_id")}

    @app.get("/api/assessments/{aid}/avatar", response_model=AvatarArtifact)
    def avatar_artifact(aid: str, identity=Depends(reviewer)):
        with db.connect(settings) as conn:
            return avatar_view(analysis_output(conn, db.assessment(conn, aid)))

    @app.get("/api/assessments/{aid}/bundle", response_model=AssessmentBundle)
    def bundle(aid: str, identity=Depends(reviewer)):
        with db.connect(settings) as conn:
            row = db.assessment(conn, aid)
            inputs = json.loads(row["input_json"])
            output = analysis_output(conn, row)
            scene_rows = db.active_scenes(conn, aid)
            scene_payloads = {scene["id"]: json.loads(scene["payload"]) for scene in scene_rows}
            links = db.current_scene_links(conn, aid, scene_payloads)
            rooms, disconnected = [], []
            components, placements = compose_rooms(scene_payloads, links)
            linked_scene_ids = {scene_id for component in components if len(component) > 1 for scene_id in component}
            for scene_row in scene_rows:
                scene = json.loads(scene_row["payload"])
                photos = [{"id": photo["id"], "role": photo["role"], "sha256": json.loads(photo["payload"]).get("upright_sha256"),
                           "asset_url": f"/api/assets/{photo['asset_id']}"} for photo in conn.execute("SELECT * FROM photos WHERE scene_id=? AND active=1 ORDER BY rowid", (scene_row["id"],))]
                state = scene.get("status", "unknown")
                rooms.append({"id": scene_row["id"], "label": scene_row["label"],
                              "status": "ready" if state == "ready" else "partial" if scene.get("ply_asset_id") else "unknown",
                              "reconstruction_status": state,
                              "provenance": scene.get("provenance"), "anchor_eligibility": scene.get("anchor_eligibility"),
                              "selected_photo_ids": scene.get("selected_photo_ids", []),
                              "analysis_sha256": scene.get("analysis_sha256"), "checkpoint_sha256": scene.get("checkpoint_sha256"),
                              "vggt_commit": scene.get("vggt_commit"), "runtime": scene.get("runtime"),
                              "reconstruction_kind": scene.get("reconstruction_kind"), "scale_state": scene.get("scale_state", "unknown"),
                              "scale_factor": scene.get("scale_factor"), "calibration": scene.get("calibration"),
                              "floor_state": scene.get("floor_state", "unknown"), "gravity_state": scene.get("gravity_state", "unknown"),
                              "gravity_up": scene.get("gravity_up"), "floor_plane": scene.get("floor_plane"),
                              "alignment_state": "reviewer_linked" if scene_row["id"] in linked_scene_ids else scene.get("alignment_state", "unregistered"), "coordinate_convention": scene.get("coordinate_convention"),
                              "raw_coordinate_units": scene.get("raw_coordinate_units", "relative"),
                              "display_transform": scene.get("display_transform"), "bounds": scene.get("bounds"),
                              "presentation": room_presentation(scene, row),
                              "input_revision": scene.get("input_revision"), "reconstruction_job_id": scene.get("reconstruction_job_id"),
                              "manifest_url": f"/api/assets/{scene['manifest_asset_id']}" if scene.get("manifest_asset_id") else None,
                              "analysis_url": f"/api/assets/{scene['analysis_asset_id']}" if scene.get("analysis_asset_id") else None,
                              "warnings": scene.get("warnings", [scene["reason"]] if scene.get("reason") else []),
                              "ply_url": f"/api/assets/{scene['ply_asset_id']}" if scene.get("ply_asset_id") else None, "photos": photos})
                if scene_row["id"] not in linked_scene_ids:
                    disconnected.append(scene_row["id"])
            findings = current_findings(conn, row["analysis_job_id"], output)
            profile = inputs["profile"]
            facts = inputs.get("clinical_facts", [])
            confirmed_facts = [fact for fact in facts if inputs.get("profile_confirmed") and fact.get("reviewer_confirmed")]
            return {"schema_version": 1, "assessment_id": aid, "input_revision": row["input_revision"], "review_revision": row["review_revision"],
                    "profile_confirmed": bool(inputs.get("profile_confirmed")), "profile": profile, "clinical_facts": inputs.get("clinical_facts", []),
                    "functional_profile": {"schema_version": 1, "confirmed": bool(inputs.get("profile_confirmed")),
                        "profile": profile, "confirmed_facts": confirmed_facts,
                        "unconfirmed_facts": [fact for fact in facts if fact not in confirmed_facts],
                        "unknown_fields": [field for field in clinical.PROFILE_FIELDS if not profile.get(field)
                            and not any(fact["field"] == field and fact["status"] in {"current", "historical", "negated"}
                                        for fact in confirmed_facts)]},
                    "clinical_documents": [document_metadata(d) for d in conn.execute("SELECT * FROM clinical_documents WHERE assessment_id=? ORDER BY created_at", (aid,))],
                    "rooms": rooms, "house": {"status": "unknown" if not rooms else "single_room" if len(rooms) == 1 else "connected" if len(components) == 1 else "partial",
                    "components": components, "links": links, "placements": placements,
                    "reconstruction_state": "ready" if rooms and all(room["status"] == "ready" for room in rooms)
                        else "partial" if any(room["status"] == "ready" for room in rooms) else "unknown",
                                                "registered_scene_ids": sorted(linked_scene_ids), "unregistered_scene_ids": disconnected,
                                                "composition": "transform_to_reference maps raw scene coordinates directly into the stated component units, including calibration scale before rigid alignment. Do not apply scale or room display transforms twice; room point clouds remain separate assets."},
                    "hazards": findings, "coverage": output.get("coverage", []), "follow_up_requests": output.get("follow_up_requests", []),
                    "anchor_abstentions": output.get("anchor_abstentions", []),
                    "avatar": avatar_view(output),
                    "provenance": {"modes": output.get("modes", settings.modes()), "producer": output.get("producer"),
                                   "provenance": output.get("provenance"), "provider": output.get("provider"),
                                   "requested_hub_revision": output.get("requested_hub_revision"), "served_revision": output.get("served_revision"),
                                   "warnings": output.get("warnings", [])}}

    @app.put("/api/assessments/{aid}/reviews/{hid}")
    def review(aid: str, hid: str, payload: ReviewInput, identity=Depends(reviewer)):
        with db.connect(settings, write=True) as conn:
            row = db.assessment(conn, aid)
            if row["status"] not in {"review_ready", "report_ready", "report_drafting"}:
                raise db.Conflict("Current analysis is not reviewable")
            db.revisions(row, payload.expected_input_revision, payload.expected_review_revision)
            source = conn.execute("SELECT payload FROM hazards WHERE id=? AND assessment_id=? AND job_id=?", (hid, aid, row["analysis_job_id"])).fetchone()
            if source is None:
                raise db.Missing("Current finding not found")
            previous = conn.execute("SELECT effective FROM reviews WHERE hazard_id=? ORDER BY review_revision DESC LIMIT 1", (hid,)).fetchone()
            effective = json.loads(previous[0] if previous else source[0])
            for field in ("observation", "priority", "suggested_action"):
                if getattr(payload, field) is not None:
                    effective[field] = getattr(payload, field)
            if payload.remove_anchor:
                effective["anchor"] = None
            elif payload.anchor:
                if payload.anchor.method != "reviewer_corrected":
                    raise ValueError("Review anchor method must be reviewer_corrected")
                effective["anchor"] = payload.anchor.model_dump(mode="json")
            photo_rows = {r["id"]: {**dict(r), "payload": json.loads(r["payload"])} for r in conn.execute("SELECT * FROM photos WHERE assessment_id=? AND active=1", (aid,))}
            scene_rows = {r["id"]: json.loads(r["payload"]) for r in conn.execute("SELECT * FROM scenes WHERE assessment_id=?", (aid,))}
            from step_wise.config import ROOT
            guide = {g["id"]: g for g in json.loads((ROOT / "guidance/checklist.json").read_text())["entries"]}
            validate_hazard(Hazard.model_validate(effective), photo_rows, scene_rows, guide,
                            json.loads(row["input_json"])["profile"], db.assessment_measurements(conn, aid))
            revision = row["review_revision"] + 1
            conn.execute("INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?,?)", (db.uid(), hid, aid, row["input_revision"], revision, payload.decision, db.dumps(effective), identity, payload.note, db.now()))
            conn.execute("UPDATE assessments SET review_revision=?,status=CASE WHEN status='report_drafting' THEN status ELSE 'review_ready' END,updated_at=? WHERE id=?", (revision, db.now(), aid))
        return {"review_revision": revision, "status": "saved"}

    @app.post("/api/assessments/{aid}/reports", status_code=202)
    def report(aid: str, payload: Revisions, identity=Depends(reviewer)):
        return {"job_id": db.enqueue(settings, aid, "report", payload.expected_input_revision, payload.expected_review_revision)}

    @app.get("/api/reports/{rid}")
    def get_report(rid: str, identity=Depends(reviewer)):
        with db.connect(settings) as conn:
            r = conn.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
            if r is None:
                raise db.Missing("Report not found")
            return {k: r[k] for k in ("id", "assessment_id", "input_revision", "review_revision", "status", "snapshot_sha256", "approver", "released_at", "family_expires_at")} | {"revoked": bool(r["revoked"]), "snapshot": json.loads(r["snapshot"]), "html_url": f"/api/assets/{r['html_asset_id']}", "pdf_url": f"/api/assets/{r['pdf_asset_id']}"}

    @app.post("/api/reports/{rid}/release")
    def release_report(rid: str, payload: Revisions, identity=Depends(reviewer)):
        return release(settings, rid, payload.expected_input_revision, payload.expected_review_revision, identity)

    @app.post("/api/reports/{rid}/revoke")
    def revoke(rid: str, identity=Depends(reviewer)):
        with db.connect(settings, write=True) as conn:
            if not conn.execute("UPDATE reports SET revoked=1 WHERE id=?", (rid,)).rowcount:
                raise db.Missing("Report not found")
        return {"status": "revoked"}

    def file_asset(asset):
        path = db.asset_path(settings, asset["path"])
        if not path.is_file():
            raise db.Missing("Asset unavailable")
        return FileResponse(path, media_type=asset["media_type"], filename="step-wise.pdf" if asset["media_type"] == "application/pdf" else None, content_disposition_type="inline")

    @app.get("/api/assets/{asset_id}")
    def asset(asset_id: str, identity=Depends(reviewer)):
        with db.connect(settings) as conn:
            a = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
            if a is None:
                raise db.Missing("Asset not found")
            return file_asset(a)

    @app.get("/api/family/{token}")
    def family(token: str, format: str = "html"):
        if format not in {"html", "pdf"}:
            raise HTTPException(422, "Unknown report format")
        with db.connect(settings) as conn:
            r = conn.execute("SELECT * FROM reports WHERE family_hash=? AND status='released' AND revoked=0 AND family_expires_at>?", (db.digest(token.encode()), db.now())).fetchone()
            if r is None:
                raise db.Missing("Released report link is unavailable or expired")
            a = conn.execute("SELECT * FROM assets WHERE id=? AND assessment_id=? AND scope='report'", (r[f"{format}_asset_id"], r["assessment_id"])).fetchone()
            if a is None:
                raise db.Missing("Released asset unavailable")
            return file_asset(a)

    @app.delete("/api/assessments/{aid}")
    def delete(aid: str, identity=Depends(reviewer)):
        db.delete_assessment(settings, aid)
        return {"status": "deleted"}

    # HEAD too: the Next.js router probes pages before navigating.
    @app.api_route("/{path:path}", methods=["GET", "HEAD"])
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "API route not found")
        root = settings.web_dist.resolve()
        # Next.js export: /pazienti is pazienti.html, / is index.html, assets keep their names.
        for candidate in (path, f"{path}.html", f"{path}/index.html") if path else ("index.html",):
            file = (root / candidate).resolve()
            if file.is_relative_to(root) and file.is_file():
                if file.suffix != ".html":
                    return FileResponse(file)
                hashes = page_script_hashes(str(file), file.stat().st_mtime_ns)
                return FileResponse(file, headers={"Content-Security-Policy": content_security_policy(hashes)})
        if Path(path).suffix:
            raise HTTPException(404, "Frontend asset not found")
        missing = root / "404.html"
        if missing.is_file():
            hashes = page_script_hashes(str(missing), missing.stat().st_mtime_ns)
            return HTMLResponse(missing.read_text(), status_code=404, headers={"Content-Security-Policy": content_security_policy(hashes)})
        return HTMLResponse("<h1>Step Wise</h1><p>Build the frontend with npm --prefix frontend run build.</p>")

    return app
