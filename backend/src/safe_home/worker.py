import fcntl
import json
import os
import threading
import time
from contextlib import contextmanager

from safe_home import db
from safe_home.config import ROOT, Settings
from safe_home.pipeline import gemma, geometry, jev, photos, reconstruction
from safe_home.pipeline.http import ExternalFailure
from safe_home.models import GuidanceChecklist


STAGES = ("prepare", "jev", "reconstruct", "geometry", "gemma", "report")
DEPENDENCIES = {"prepare": (), "jev": ("prepare",), "reconstruct": ("prepare",),
                "geometry": ("prepare", "jev", "reconstruct"), "gemma": ("prepare", "jev", "geometry"), "report": ("gemma",)}


@contextmanager
def worker_lock(settings):
    settings.data.mkdir(parents=True, exist_ok=True)
    lock_path = settings.worker_lock_path or settings.data / "worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise db.Conflict("Another worker or case run owns the worker/GPU lock") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(db.dumps(value) + "\n")
    os.replace(temporary, path)


def load_context(settings, job, directory=None):
    with db.connect(settings) as conn:
        assessment = db.assessment(conn, job["assessment_id"])
        selected = []
        for row in conn.execute("SELECT p.*,a.path FROM photos p JOIN assets a ON a.id=p.asset_id WHERE p.assessment_id=? AND p.active=1 ORDER BY p.rowid", (assessment["id"],)):
            selected.append({**dict(row), "payload": json.loads(row["payload"])})
        scenes = [dict(r) for r in conn.execute("SELECT * FROM scenes WHERE assessment_id=? ORDER BY rowid", (assessment["id"],))]
    checklist = GuidanceChecklist.model_validate_json((ROOT / "guidance/checklist.json").read_text()).model_dump(mode="json")
    context = {"settings": settings, "job": job, "assessment": assessment, "assessment_input": json.loads(assessment["input_json"]),
            "photos": selected, "scenes": scenes, "guidance": {g["id"]: {**g, "source": checklist["source"]} for g in checklist["entries"]},
            "directory": directory or settings.data / "assessments" / assessment["id"] / "jobs" / job["id"], "outputs": {}}
    if job.get("kind") == "report":
        with db.connect(settings) as conn:
            snapshot = db.accepted_snapshot(conn, assessment)
        snapshot.pop("created_at", None)
        context["review_fingerprint"] = snapshot
    return context


def fingerprint(stage, context, manifests):
    """Fixed stage-specific inputs; keep reconstruction independent of profile/Jev."""
    settings = context["settings"]
    files = {"prepare": ["pipeline/photos.py"], "jev": ["pipeline/jev.py"], "reconstruct": ["pipeline/reconstruction.py"],
             "geometry": ["pipeline/geometry.py"], "gemma": ["pipeline/gemma.py", "validation.py", "prompts/assessment.txt"],
             "report": ["pipeline/reports.py", "prompts/report.txt", "templates/report.html"]}[stage]
    common = ["models.py", "schema.sql", "worker.py"]
    if stage in {"jev", "gemma", "report"}:
        common.append("pipeline/http.py")
    code = {f: db.digest((ROOT / "backend/src/safe_home" / f).read_bytes()) for f in files + common if (ROOT / "backend/src/safe_home" / f).exists()}
    def source_value(value):
        if isinstance(value, dict):
            return {key: "live" if key == "provenance" and item == "replay" else source_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [source_value(item) for item in value]
        return value

    value = {"stage": stage, "schema_version": 1, "code": code,
             "dependencies": {s: {"fingerprint": manifests[s]["fingerprint"], "output_sha256": db.digest(db.dumps(source_value(context["outputs"][s])).encode())} for s in DEPENDENCIES[stage] if s in manifests}}
    if stage == "prepare":
        value["photos"] = [{"id": p["id"], "scene": p["scene_id"], "role": p["role"], "sha256": db.digest(db.asset_path(settings, p["path"]).read_bytes())} for p in context["photos"]]
    if stage in {"jev", "gemma", "report"}:
        value["guidance"] = context["guidance"]
        value["checklist_sha256"] = db.digest((ROOT / "guidance/checklist.json").read_bytes())
    if stage == "jev":
        value.update({"enabled": settings.jev_enabled, "mode": settings.gemma_backend, "model": "jev-1.14.0", "fixture": db.digest((ROOT / "demo/fixtures/candidates.json").read_bytes())})
    if stage == "reconstruct":
        value.update({"mode": settings.recon_backend, "checkpoint": settings.vggt_checkpoint, "checkpoint_sha256": settings.vggt_checkpoint_sha256, "commit": settings.vggt_commit, "frame_cap": settings.frame_cap})
    if stage in {"gemma", "report"}:
        value.update({"profile": context["assessment_input"]["profile"], "mode": settings.gemma_backend, "model": settings.gemma_model, "revision": settings.gemma_revision})
    if stage == "report":
        value["reviews"] = context.get("review_fingerprint")
    return db.digest(db.dumps(value).encode()), value


def execute_stage(stage, context, manifests):
    directory = context["directory"] / "stages" / stage
    if (directory / "manifest.json").exists():
        raise db.Conflict("Stage artifacts already exist; create a new attempt")
    checksum, inputs = fingerprint(stage, context, manifests)
    atomic_json(directory / "input.json", inputs)
    start = time.perf_counter()
    manifest = {"schema_version": 1, "stage": stage, "fingerprint": checksum, "dependencies": list(DEPENDENCIES[stage]), "started_at": db.now(),
                "provenance": "fixture" if context["settings"].gemma_backend == "fixture" else "live"}
    try:
        requested = json.loads(context["job"].get("output", "{}" )).get("requested_modes")
        if requested != context["settings"].modes():
            raise ExternalFailure("Worker backends differ from the enqueued request; configure matching modes and start an explicit new attempt")
        if stage == "report":
            from safe_home.pipeline.reports import draft_report
            output = draft_report(context)
        else:
            output = {"prepare": photos.prepare, "jev": jev.inspect, "reconstruct": reconstruction.reconstruct,
                      "geometry": geometry.geometry, "gemma": gemma.assess}[stage](context)
        atomic_json(directory / "output.json", output)
        context["outputs"][stage] = output
        # Hash outputs and generated assets, including all files referenced by prior stages.
        artifacts = {p.relative_to(context["settings"].data).as_posix(): db.digest(p.read_bytes()) for p in (context["directory"] / "assets").glob("*") if p.is_file()}
        artifacts[(directory / "output.json").relative_to(context["settings"].data).as_posix()] = db.digest((directory / "output.json").read_bytes())
        artifacts.update({p.relative_to(context["settings"].data).as_posix(): db.digest(p.read_bytes()) for p in (directory / "calls").rglob("*.json")})
        manifest.update({"status": "succeeded", "artifacts": artifacts, "warnings": output.get("warnings", output.get("errors", []))})
    except Exception as exc:
        # Never persist provider error bodies, credentials, patient text or traceback locals.
        error = {"stage": stage, "category": type(exc).__name__, "message": str(exc) if isinstance(exc, ExternalFailure) else "Stage failed; inspect configuration and local server diagnostics. No automatic retry or fixture fallback.", "detail": str(exc) if isinstance(exc, (db.Conflict, db.Missing)) else None}
        atomic_json(directory / "error.json", error)
        manifest.update({"status": "failed", "error": error})
        raise
    finally:
        manifest["elapsed_seconds"] = time.perf_counter() - start
        manifest["finished_at"] = db.now()
        atomic_json(directory / "manifest.json", manifest)
        manifests[stage] = manifest
    return output


def claim(settings):
    with db.connect(settings, write=True) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if row is None:
            return None
        conn.execute("UPDATE jobs SET status='running',heartbeat=? WHERE id=?", (db.now(), row["id"]))
        conn.execute("UPDATE assessments SET status=? WHERE id=?", ("processing" if row["kind"] == "analyze" else "report_drafting", row["assessment_id"]))
        return {**dict(row), "status": "running"}


def recover(settings):
    # Only called after acquiring the process-lifetime lock; heartbeat age is not authority.
    with db.connect(settings, write=True) as conn:
        for row in conn.execute("SELECT * FROM jobs WHERE status='running'").fetchall():
            conn.execute("UPDATE jobs SET status='interrupted',error=?,finished_at=? WHERE id=?", ("Worker stopped. Start an explicit new attempt.", db.now(), row["id"]))
            conn.execute("UPDATE assessments SET status=? WHERE id=?", ("failed" if row["kind"] == "analyze" else "review_ready", row["assessment_id"]))


def publish_analysis(context):
    settings, job = context["settings"], context["job"]
    output = context["outputs"]["gemma"]
    with db.connect(settings, write=True) as conn:
        row = db.assessment(conn, job["assessment_id"])
        db.revisions(row, job["input_revision"], job["review_revision"])
        state = conn.execute("SELECT status FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
        if state == "succeeded":
            return  # idempotent completion
        if state != "running":
            raise db.Conflict("Job is no longer running")
        for candidate in context["outputs"]["jev"]["candidates"]:
            conn.execute("INSERT INTO candidates VALUES(?,?,?,?,?)", (db.uid(), row["id"], job["id"], candidate["photo_id"], db.dumps(candidate)))
        for hazard in output["hazards"]:
            conn.execute("INSERT INTO hazards VALUES(?,?,?,?)", (db.uid(), row["id"], job["id"], db.dumps(hazard)))
        for scene in context["outputs"]["geometry"]["scenes"]:
            scene = dict(scene)
            if scene.get("ply_path"):
                scene["ply_asset_id"] = db.store_asset(settings, conn, row["id"], scene["ply_path"], "application/octet-stream", scene["provenance"], "derived")
            conn.execute("UPDATE scenes SET payload=? WHERE id=? AND assessment_id=?", (db.dumps(scene), scene["id"], row["id"]))
        conn.execute("UPDATE assessments SET analysis_job_id=?,status='review_ready',updated_at=? WHERE id=?", (job["id"], db.now(), row["id"]))
        conn.execute("UPDATE jobs SET output=?,status='succeeded',finished_at=? WHERE id=?", (db.dumps(output), db.now(), job["id"]))


def heartbeat(settings, jid, stop):
    while not stop.wait(10):
        with db.connect(settings, write=True) as conn:
            conn.execute("UPDATE jobs SET heartbeat=? WHERE id=? AND status='running'", (db.now(), jid))


def process_job(settings, job):
    context = None
    manifests = {}
    stop = threading.Event()
    thread = threading.Thread(target=heartbeat, args=(settings, job["id"], stop), daemon=True)
    thread.start()
    try:
        context = load_context(settings, job)
        for stage in STAGES[:5] if job["kind"] == "analyze" else ["report"]:
            with db.connect(settings, write=True) as conn:
                conn.execute("UPDATE jobs SET stage=?,heartbeat=? WHERE id=?", (stage, db.now(), job["id"]))
            execute_stage(stage, context, manifests)
            with db.connect(settings, write=True) as conn:
                conn.execute("UPDATE jobs SET stages=? WHERE id=?", (db.dumps(manifests), job["id"]))
        if job["kind"] == "analyze":
            publish_analysis(context)
    except Exception:
        with db.connect(settings, write=True) as conn:
            conn.execute("UPDATE jobs SET status='failed',stages=?,error=?,finished_at=? WHERE id=?", (db.dumps(manifests), "Processing failed. Inspect the failed stage; retry creates a new attempt.", db.now(), job["id"]))
            conn.execute("UPDATE assessments SET status=? WHERE id=?", ("failed" if job["kind"] == "analyze" else "review_ready", job["assessment_id"]))
    finally:
        stop.set()
        thread.join(timeout=2)
    return context


def run(settings, once=False):
    if (settings.data / "baseline.json").exists():
        raise db.Conflict("Frozen baseline is read-only; create a new case attempt")
    db.init(settings)
    with worker_lock(settings):
        recover(settings)
        while True:
            job = claim(settings)
            if job:
                process_job(settings, job)
            if once:
                return
            if not job:
                time.sleep(1)


if __name__ == "__main__":
    run(Settings.from_env())
