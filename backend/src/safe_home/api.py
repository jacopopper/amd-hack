import json
import secrets
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.concurrency import run_in_threadpool
from pydantic import Field

from safe_home import db
from safe_home.config import Settings
from safe_home.models import AssessmentEdit, AssessmentInput, Hazard, ReviewInput, Revisions, StrictModel
from safe_home.pipeline.photos import ingest
from safe_home.pipeline.reports import release
from safe_home.validation import validate_hazard


class Login(StrictModel):
    username: str = Field(max_length=200)
    password: str = Field(max_length=500)


class UploadLimit:
    def __init__(self, app, limit):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or not scope["path"].endswith("/photos"):
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


def create_app(settings=None):
    settings = settings or Settings.from_env()
    if len(settings.reviewer_password) < 12:
        raise RuntimeError("Set REVIEWER_PASSWORD to at least 12 characters before starting the API")
    db.init(settings)
    logging.getLogger("uvicorn.error").info("Safe Home execution modes: %s", settings.modes())
    app = FastAPI(title="Safe Home", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.add_middleware(UploadLimit, limit=settings.max_file_bytes + 512 * 1024)
    basic = HTTPBasic(auto_error=False)

    def credentials_ok(username, password):
        return secrets.compare_digest(username.encode(), settings.reviewer_user.encode()) & secrets.compare_digest(password.encode(), settings.reviewer_password.encode())

    def reviewer(request: Request, credentials: HTTPBasicCredentials | None = Depends(basic)):
        if credentials and credentials_ok(credentials.username, credentials.password):
            return settings.reviewer_identity
        cookie = request.cookies.get("safe_home_session")
        if cookie:
            with db.connect(settings) as conn:
                session = conn.execute("SELECT 1 FROM sessions WHERE token_hash=? AND expires_at>?", (db.digest(cookie.encode()), db.now())).fetchone()
            if session:
                return settings.reviewer_identity
        raise HTTPException(401, "Reviewer login required")

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            if (settings.data / "baseline.json").exists() and request.url.path not in {"/api/login", "/api/logout"}:
                return JSONResponse({"error": "Frozen baseline is read-only; create a new case attempt"}, status_code=409)
            origin = request.headers.get("origin")
            if request.headers.get("sec-fetch-site") == "cross-site" or (origin and (urlsplit(origin).scheme, urlsplit(origin).netloc) != (request.url.scheme, request.url.netloc)):
                return JSONResponse({"error": "Cross-origin mutation denied"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'self'; base-uri 'none'"
        return response

    @app.exception_handler(db.Missing)
    async def missing(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=404)

    @app.exception_handler(db.Conflict)
    async def conflict(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"error": "Invalid input or incompatible evidence"}, status_code=422)

    @app.exception_handler(OverflowError)
    async def oversized(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=413)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({"error": "Invalid request", "fields": [".".join(map(str, e["loc"])) for e in exc.errors()]}, status_code=422)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "schema_version": 1, "modes": settings.modes(), "staged_data_only": True}

    @app.post("/api/login")
    def login(payload: Login, response: Response, request: Request):
        if not credentials_ok(payload.username, payload.password):
            raise HTTPException(401, "Invalid login")
        token = secrets.token_urlsafe(32)
        with db.connect(settings, write=True) as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at<=?", (db.now(),))
            conn.execute("INSERT INTO sessions VALUES(?,?)", (db.digest(token.encode()), (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()))
        response.set_cookie("safe_home_session", token, httponly=True, secure=request.url.scheme == "https", samesite="strict", max_age=8 * 3600)
        return {"reviewer": settings.reviewer_identity}

    @app.post("/api/logout")
    def logout(request: Request, response: Response, identity=Depends(reviewer)):
        with db.connect(settings, write=True) as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (db.digest(request.cookies.get("safe_home_session", "").encode()),))
        response.delete_cookie("safe_home_session")
        return {"status": "ok"}

    @app.post("/api/assessments", status_code=201)
    def create(payload: AssessmentInput, identity=Depends(reviewer)):
        return {"id": db.create_assessment(settings, payload.model_dump(mode="json"))}

    @app.get("/api/assessments")
    def listing(identity=Depends(reviewer)):
        with db.connect(settings) as conn:
            return [{"id": r["id"], "label": json.loads(r["input_json"])["label"], "status": r["status"], "created_at": r["created_at"]} for r in conn.execute("SELECT * FROM assessments ORDER BY created_at DESC LIMIT 100")]

    @app.get("/api/assessments/{aid}")
    def get_assessment(aid: str, identity=Depends(reviewer)):
        with db.connect(settings) as conn:
            row = db.assessment(conn, aid)
            row["inputs"] = json.loads(row.pop("input_json"))
            row["photos"] = [{"id": p["id"], "scene_id": p["scene_id"], "role": p["role"], "asset_url": f"/api/assets/{p['asset_id']}", **json.loads(p["payload"])} for p in conn.execute("SELECT * FROM photos WHERE assessment_id=? AND active=1 ORDER BY rowid", (aid,))]
            row["jobs"] = [dict(j) for j in conn.execute("SELECT id,kind,status,stage,error,created_at FROM jobs WHERE assessment_id=? ORDER BY created_at DESC", (aid,))]
            row["reports"] = [dict(r) for r in conn.execute("SELECT id,status,input_revision,review_revision FROM reports WHERE assessment_id=? ORDER BY rowid DESC", (aid,))]
            return row

    @app.patch("/api/assessments/{aid}")
    def edit(aid: str, payload: AssessmentEdit, identity=Depends(reviewer)):
        with db.connect(settings, write=True) as conn:
            db.editable(conn, aid, payload.expected_input_revision)
            conn.execute("UPDATE assessments SET input_json=? WHERE id=?", (db.dumps(payload.model_dump(mode="json", exclude={"expected_input_revision"})), aid))
            db.invalidate(conn, aid)
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

    @app.delete("/api/assessments/{aid}/photos/{photo_id}")
    def remove_photo(aid: str, photo_id: str, expected_input_revision: int, identity=Depends(reviewer)):
        with db.connect(settings, write=True) as conn:
            db.editable(conn, aid, expected_input_revision)
            changed = conn.execute("UPDATE photos SET active=0 WHERE id=? AND assessment_id=? AND active=1", (photo_id, aid)).rowcount
            if not changed:
                raise db.Missing("Photo not found")
            db.invalidate(conn, aid)
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
            value["output"] = {"report_id": json.loads(value["output"]).get("report_id")} if value["kind"] == "report" else {}
            return value

    @app.get("/api/assessments/{aid}/findings")
    def findings(aid: str, identity=Depends(reviewer)):
        with db.connect(settings) as conn:
            row = db.assessment(conn, aid)
            output_row = conn.execute("SELECT output FROM jobs WHERE id=?", (row["analysis_job_id"],)).fetchone()
            output = json.loads(output_row[0]) if output_row else {}
            hazards = []
            for h in conn.execute("SELECT * FROM hazards WHERE job_id=?", (row["analysis_job_id"],)):
                review = conn.execute("SELECT * FROM reviews WHERE hazard_id=? ORDER BY review_revision DESC LIMIT 1", (h["id"],)).fetchone()
                hazards.append({"id": h["id"], **json.loads(h["payload"]), "producer": output.get("producer"), "provenance": output.get("provenance"), "review": {**dict(review), "effective": json.loads(review["effective"])} if review else None})
            output = conn.execute("SELECT output FROM jobs WHERE id=?", (row["analysis_job_id"],)).fetchone()
            output = json.loads(output[0]) if output else {}
            return {"hazards": hazards, "candidates": [json.loads(c[0]) for c in conn.execute("SELECT payload FROM candidates WHERE job_id=?", (row["analysis_job_id"],))], "coverage": output.get("coverage", []), "follow_up_requests": output.get("follow_up_requests", []), "provenance": output.get("provenance"), "modes": output.get("modes", settings.modes())}

    @app.get("/api/assessments/{aid}/scenes")
    def scenes(aid: str, identity=Depends(reviewer)):
        with db.connect(settings) as conn:
            db.assessment(conn, aid)
            result = []
            for s in conn.execute("SELECT * FROM scenes WHERE assessment_id=?", (aid,)):
                payload = json.loads(s["payload"])
                result.append({"id": s["id"], "label": s["label"], **{k: v for k, v in payload.items() if not k.endswith("path") and k not in {"images"}}, "ply_url": f"/api/assets/{payload['ply_asset_id']}" if payload.get("ply_asset_id") else None})
            return result

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
            from safe_home.config import ROOT
            guide = {g["id"]: g for g in json.loads((ROOT / "guidance/checklist.json").read_text())["entries"]}
            validate_hazard(Hazard.model_validate(effective), photo_rows, scene_rows, guide, json.loads(row["input_json"])["profile"], {})
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
            return {k: r[k] for k in ("id", "assessment_id", "input_revision", "review_revision", "status", "snapshot_sha256", "approver", "released_at")} | {"snapshot": json.loads(r["snapshot"]), "html_url": f"/api/assets/{r['html_asset_id']}", "pdf_url": f"/api/assets/{r['pdf_asset_id']}"}

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
        return FileResponse(path, media_type=asset["media_type"], filename="safe-home.pdf" if asset["media_type"] == "application/pdf" else None, content_disposition_type="inline")

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

    @app.get("/{path:path}")
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "API route not found")
        file = (settings.web_dist / path).resolve()
        if file.is_relative_to(settings.web_dist.resolve()) and file.is_file():
            return FileResponse(file)
        if Path(path).suffix:
            raise HTTPException(404, "Frontend asset not found")
        if (settings.web_dist / "index.html").is_file():
            return HTMLResponse((settings.web_dist / "index.html").read_text())
        return HTMLResponse("<h1>Safe Home</h1><p>Build the frontend with npm --prefix web run build.</p>")

    return app
