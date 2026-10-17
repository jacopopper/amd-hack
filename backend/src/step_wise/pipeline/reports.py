import base64
import io
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image, ImageDraw

from step_wise import db
from step_wise.models import DraftOutput


def generation_fingerprints():
    from step_wise.config import ROOT
    package = Path(__file__).resolve().parents[1]
    return {"template": db.digest((package / "templates/report.html").read_bytes()), "prompt": db.digest((package / "prompts/report.txt").read_bytes()),
            "renderer": db.digest(Path(__file__).read_bytes()), "contracts": db.digest((package / "models.py").read_bytes()), "guidance": db.digest((ROOT / "guidance/checklist.json").read_bytes())}


def validate_draft(draft, accepted):
    parsed = DraftOutput.model_validate(draft)
    by_id = {h["id"]: h for h in accepted}
    if len(parsed.items) != len(by_id) or {i.hazard_id for i in parsed.items} != set(by_id):
        raise ValueError("Report must include every accepted ID exactly once")
    for item in parsed.items:
        source = by_id[item.hazard_id]
        if any(getattr(item, f) != source[f] for f in ("observation", "suggested_action", "priority")):
            raise ValueError("Draft changes reviewed meaning or trusted values")
    return parsed


def evidence_thumbnail(settings, conn, aid, evidence):
    row = conn.execute("SELECT p.payload,a.path FROM photos p JOIN assets a ON a.id=p.asset_id WHERE p.id=? AND p.assessment_id=?", (evidence["photo_id"], aid)).fetchone()
    if row is None:
        raise ValueError("Missing report evidence")
    with Image.open(db.asset_path(settings, row["path"])) as source:
        image = source.convert("RGB")
        if evidence["box"]:
            ImageDraw.Draw(image).rectangle(evidence["box"], outline="#ce583a", width=max(3, image.width // 200))
        image.thumbnail((480, 360))
        stream = io.BytesIO()
        image.save(stream, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(stream.getvalue()).decode()


def draft_report(context):
    from weasyprint import HTML
    from weasyprint.urls import URLFetcherResponse
    settings, job = context["settings"], context["job"]
    with db.connect(settings) as conn:
        row = db.assessment(conn, job["assessment_id"])
        db.revisions(row, job["input_revision"], job["review_revision"])
        snapshot = db.accepted_snapshot(conn, row)
        snapshot["generation_fingerprints"] = generation_fingerprints()
        for hazard in snapshot["accepted"]:
            hazard["evidence_thumbnails"] = [evidence_thumbnail(settings, conn, row["id"], e) for e in hazard["evidence"]]
            hazard["guidance"] = [context["guidance"][g] for g in hazard["guidance_ids"]]
    draft = {"title": "Home action plan", "items": [{"hazard_id": h["id"], **{key: h[key] for key in ("observation", "suggested_action", "priority")}} for h in snapshot["accepted"]]}
    validate_draft(draft, snapshot["accepted"])
    provenance = "deterministic_fixture"
    if settings.gemma_backend == "http":
        from step_wise.pipeline.gemma import complete
        from step_wise.pipeline.http import ExternalFailure
        if not context["assessment_input"].get("clinical_external_processing_consent"):
            raise ExternalFailure("External clinical-profile processing consent is required before Gemma drafts a report")
        prompt = (Path(__file__).resolve().parents[1] / "prompts/report.txt").read_text()
        try:
            reviewed = [{key: hazard[key] for key in ("id", "observation", "priority", "suggested_action", "profile_relevance", "uncertainty", "guidance_ids", "measurement_ids")} for hazard in snapshot["accepted"]]
            content = db.dumps({"accepted": reviewed, "profile": snapshot["profile"], "deferred": snapshot["deferred"]})
            if len(content) > 12_000:
                raise ExternalFailure("Reviewed text exceeds the bounded draft request; use the deterministic draft and inspect it")
            value, attempts = complete(settings, DraftOutput, prompt, content, lambda value: validate_draft(value.model_dump(mode="json"), snapshot["accepted"]), context["directory"] / "stages/report/calls")
            draft = value.model_dump(mode="json")
            snapshot["model_calls"] = attempts
            provenance = "live_gemma"
        except ExternalFailure as exc:
            provenance = "deterministic_fallback"
            snapshot["draft_warning"] = str(exc) + ". Deterministic reviewed values require final approval. No successful Gemma call is claimed."
    snapshot["draft"] = draft
    snapshot["draft_provenance"] = provenance
    scale_note = ("Metric scale is verified only in rooms with a calibration; no measurement is inferred by the model."
                  if snapshot.get("calibrations") else "Metric scale not validated; no dimensions are available.")
    snapshot["limitations"] = ["Demonstration data and a fictional profile.", "The reviewer’s role is declared by the person who releases the report.", scale_note]
    snapshot_hash = db.digest(db.dumps(snapshot).encode())
    template_path = Path(__file__).resolve().parents[1] / "templates"
    env = Environment(loader=FileSystemLoader(template_path), autoescape=select_autoescape(["html"]))
    html = env.get_template("report.html").render(snapshot=snapshot, snapshot_hash=snapshot_hash)
    assets = context["directory"] / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    html_path, pdf_path = assets / f"report-{job['id']}.html", assets / f"report-{job['id']}.pdf"
    html_path.write_text(html)
    allowed = {uri for h in snapshot["accepted"] for uri in h["evidence_thumbnails"]}

    def fetch(url):
        if url not in allowed:
            raise ValueError("Report renderer asset fetch denied")
        return URLFetcherResponse(url=url, body=base64.b64decode(url.split(",", 1)[1], validate=True), headers={"Content-Type": "image/jpeg"})

    HTML(string=html, url_fetcher=fetch).write_pdf(pdf_path)
    report_id = db.uid()
    with db.connect(settings, write=True) as conn:
        row = db.assessment(conn, job["assessment_id"])
        db.revisions(row, job["input_revision"], job["review_revision"])
        if snapshot["generation_fingerprints"] != generation_fingerprints():
            raise db.Conflict("Report configuration changed during rendering; regenerate")
        html_id = db.store_asset(settings, conn, row["id"], html_path.relative_to(settings.data).as_posix(), "text/html", snapshot["draft_provenance"], "report")
        pdf_id = db.store_asset(settings, conn, row["id"], pdf_path.relative_to(settings.data).as_posix(), "application/pdf", snapshot["draft_provenance"], "report")
        conn.execute("INSERT INTO reports(id,assessment_id,job_id,input_revision,review_revision,snapshot,snapshot_sha256,html_asset_id,pdf_asset_id) VALUES(?,?,?,?,?,?,?,?,?)", (report_id, row["id"], job["id"], job["input_revision"], job["review_revision"], db.dumps(snapshot), snapshot_hash, html_id, pdf_id))
        conn.execute("UPDATE assessments SET status='report_ready',updated_at=? WHERE id=?", (db.now(), row["id"]))
        conn.execute("UPDATE jobs SET status='succeeded',output=?,finished_at=? WHERE id=?", (db.dumps({"report_id": report_id}), db.now(), job["id"]))
    return {"report_id": report_id, "snapshot_sha256": snapshot_hash, "provenance": snapshot["draft_provenance"]}


def release(settings, report_id, expected_input, expected_review, approver):
    token = secrets.token_urlsafe(32)
    expiry = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    with db.connect(settings, write=True) as conn:
        report = conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
        if report is None:
            raise db.Missing("Report not found")
        row = db.editable(conn, report["assessment_id"], expected_input, expected_review)
        db.revisions(row, report["input_revision"], report["review_revision"])
        if report["status"] != "draft" or row["status"] != "report_ready":
            raise db.Conflict("Report is not ready for release")
        db.accepted_snapshot(conn, row)
        if db.digest(report["snapshot"].encode()) != report["snapshot_sha256"]:
            raise db.Conflict("Report snapshot changed")
        if json.loads(report["snapshot"]).get("generation_fingerprints") != generation_fingerprints():
            raise db.Conflict("Report prompt, renderer or guidance changed; regenerate the draft")
        for field in ("html_asset_id", "pdf_asset_id"):
            asset = conn.execute("SELECT * FROM assets WHERE id=? AND assessment_id=? AND scope='report'", (report[field], row["id"])).fetchone()
            if asset is None or db.digest(db.asset_path(settings, asset["path"]).read_bytes()) != asset["sha256"]:
                raise db.Conflict("Report artifact is missing or changed; regenerate the draft")
        conn.execute("UPDATE reports SET status='released',approver=?,released_at=?,family_hash=?,family_expires_at=? WHERE id=?", (approver, db.now(), db.digest(token.encode()), expiry, report_id))
        conn.execute("UPDATE assessments SET status='released',updated_at=? WHERE id=?", (db.now(), row["id"]))
    return {"token": token, "expires_at": expiry, "report_id": report_id}
