import fcntl
import json
import os
from copy import deepcopy
import threading
import time
from contextlib import contextmanager

from step_wise import db
from step_wise.config import ROOT, Settings
from step_wise.pipeline import gemma, geometry, jev, photos, reconstruction
from step_wise.pipeline.http import ExternalFailure
from step_wise.models import GuidanceChecklist, Hazard
from step_wise.validation import OutputValidationFailure, validate_hazard


STAGES = ("prepare", "jev", "reconstruct", "gemma", "geometry", "avatar", "report")
DEPENDENCIES = {"prepare": (), "jev": ("prepare",), "reconstruct": ("prepare",),
                "gemma": ("prepare", "jev"), "geometry": ("prepare", "jev", "reconstruct", "gemma"),
                "avatar": (), "report": ("gemma", "geometry", "avatar")}


def job_stages(kind):
    return ["report"] if kind == "report" else [stage for stage in STAGES if stage != "report"]


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
        scenes = db.active_scenes(conn, assessment["id"])
        measurements = db.assessment_measurements(conn, assessment["id"])
    checklist = GuidanceChecklist.model_validate_json((ROOT / "guidance/checklist.json").read_text()).model_dump(mode="json")
    context = {"settings": settings, "job": job, "assessment": assessment, "assessment_input": json.loads(assessment["input_json"]),
            "photos": selected, "scenes": scenes, "guidance": {g["id"]: {**g, "source": checklist["source"]} for g in checklist["entries"]},
            "directory": directory or settings.data / "assessments" / assessment["id"] / "jobs" / job["id"],
            "measurements": measurements, "outputs": {}}
    if job.get("kind") == "report":
        with db.connect(settings) as conn:
            snapshot = db.accepted_snapshot(conn, assessment)
        snapshot.pop("created_at", None)
        context["review_fingerprint"] = snapshot
    return context


def fingerprint(stage, context, manifests):
    """Fixed stage-specific inputs; keep reconstruction independent of profile/Jev."""
    settings = context["settings"]
    files = {"prepare": ["pipeline/photos.py"], "jev": ["pipeline/jev.py", "prompts/jev-visual.json"], "reconstruct": ["pipeline/reconstruction.py", "pipeline/vggt_worker.py"],
             "geometry": ["pipeline/geometry.py"], "gemma": ["pipeline/gemma.py", "validation.py", "prompts/assessment.txt"],
             "avatar": ["pipeline/avatar.py", "pipeline/avatar_blender.py", "pipeline/avatar_style.py", "pipeline/mpfb_export.py"],
             "report": ["pipeline/reports.py", "prompts/report.txt", "templates/report.html"]}[stage]
    common = ["models.py", "schema.sql", "worker.py"]
    if stage in {"jev", "gemma", "report"}:
        common.append("pipeline/http.py")
    code = {f: db.digest((ROOT / "backend/src/step_wise" / f).read_bytes()) for f in files + common if (ROOT / "backend/src/step_wise" / f).exists()}
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
        value["max_pixels"] = settings.max_pixels
    if stage in {"jev", "gemma", "report"}:
        value["guidance"] = context["guidance"]
        value["checklist_sha256"] = db.digest((ROOT / "guidance/checklist.json").read_bytes())
    if stage == "jev":
        value.update({"enabled": settings.jev_enabled, "fixture_mode": settings.gemma_backend == "fixture",
                      "mode": "live" if settings.jev_enabled else "disabled", "model": "jev-1.14.0", "fixture": db.digest((ROOT / "demo/fixtures/candidates.json").read_bytes())})
    if stage == "reconstruct":
        value.update({"mode": settings.recon_backend, "checkpoint": settings.vggt_checkpoint, "checkpoint_sha256": settings.vggt_checkpoint_sha256, "commit": settings.vggt_commit, "frame_cap": settings.frame_cap})
    if stage in {"gemma", "report"}:
        value.update({"profile": context["assessment_input"]["profile"],
                      "clinical_facts": context["assessment_input"].get("clinical_facts", []),
                      "mode": settings.gemma_backend, "model": settings.gemma_model, "revision": settings.gemma_revision})
        if settings.gemma_backend == "http":
            value["gemma_identity"] = settings.gemma_identity()
    if stage == "avatar":
        value.update({"profile": context["assessment_input"]["profile"], "clinical_facts": context["assessment_input"].get("clinical_facts", []),
                      "profile_confirmed": context["assessment_input"]["profile_confirmed"], "avatar_backend": settings.avatar_backend,
                      "blender_bin": settings.blender_bin,
                      "mpfb_asset_pack_sha256": settings.mpfb_asset_pack_sha256})
    if stage == "report":
        value["reviews"] = context.get("review_fingerprint")
    return db.digest(db.dumps(value).encode()), value


def validate_request(stage, context):
    request = json.loads(context["job"].get("output", "{}"))
    if request.get("requested_modes") != context["settings"].modes():
        raise ExternalFailure("Worker backends differ from the enqueued request; configure matching modes and start an explicit new attempt")
    if request.get("requested_stages") is not None and stage not in request["requested_stages"]:
        raise ExternalFailure("Stage is outside the explicitly queued scope; create a matching attempt")
    if stage in {"gemma", "report"} and context["settings"].gemma_backend == "http" and request.get("requested_gemma") != context["settings"].gemma_identity():
        raise ExternalFailure("Gemma provider, endpoint or model differs from the enqueued request; create an explicit new attempt")


def reuse_stage(stage, context, manifests):
    """Reuse only intact, successful outputs belonging to this assessment."""
    try:
        validate_request(stage, context)
    except ExternalFailure:
        return False  # execute_stage records the explicit queued-request failure.
    checksum, inputs = fingerprint(stage, context, manifests)
    settings, job = context["settings"], context["job"]
    with db.connect(settings) as conn:
        previous = [dict(row) for row in conn.execute(
            "SELECT id,stages FROM jobs WHERE assessment_id=? AND kind='analyze' AND id<>? ORDER BY created_at DESC",
            (job["assessment_id"], job["id"]))]
    for row in previous:
        saved = json.loads(row["stages"]).get(stage, {})
        if saved.get("status") != "succeeded" or saved.get("fingerprint") != checksum:
            continue
        output_relative = saved.get("output_path")
        if not output_relative or saved.get("artifacts", {}).get(output_relative) is None:
            continue  # Legacy attempts without an explicit output artifact are not cache entries.
        artifacts = saved["artifacts"]
        try:
            if any(not db.asset_path(settings, path).is_file() or db.digest(db.asset_path(settings, path).read_bytes()) != digest
                   for path, digest in artifacts.items()):
                continue
            output = json.loads(db.asset_path(settings, output_relative).read_text())
        except (OSError, ValueError):
            continue
        if output.get("status") in {"failed", "degraded"} or output.get("errors"):
            continue
        directory = context["directory"] / "stages" / stage
        if directory.exists():
            raise db.Conflict("Stage artifacts already exist; create a new attempt")
        atomic_json(directory / "input.json", inputs)
        atomic_json(directory / "output.json", output)
        output_path = (directory / "output.json").relative_to(settings.data).as_posix()
        reused_artifacts = {path: digest for path, digest in artifacts.items() if path != output_relative}
        manifest = {**saved, "artifacts": {**reused_artifacts, output_path: db.digest((directory / "output.json").read_bytes())},
                    "output_path": output_path, "origin_job_id": saved.get("origin_job_id", row["id"]),
                    "reused_from_job_id": row["id"], "started_at": db.now(), "finished_at": db.now(), "elapsed_seconds": 0}
        atomic_json(directory / "manifest.json", manifest)
        context["outputs"][stage], manifests[stage] = output, manifest
        return True
    return False


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
        validate_request(stage, context)
        if stage == "report":
            from step_wise.pipeline.reports import draft_report
            output = draft_report(context)
        else:
            from step_wise.pipeline import avatar
            output = {"prepare": photos.prepare, "jev": jev.inspect, "reconstruct": reconstruction.reconstruct,
                      "geometry": geometry.geometry, "gemma": gemma.assess, "avatar": avatar.generate}[stage](context)
        atomic_json(directory / "output.json", output)
        context["outputs"][stage] = output
        # Hash outputs and generated assets, including all files referenced by prior stages.
        artifacts = {p.relative_to(context["settings"].data).as_posix(): db.digest(p.read_bytes()) for p in (context["directory"] / "assets").glob("*") if p.is_file()}
        artifacts[(directory / "output.json").relative_to(context["settings"].data).as_posix()] = db.digest((directory / "output.json").read_bytes())
        artifacts.update({p.relative_to(context["settings"].data).as_posix(): db.digest(p.read_bytes()) for p in (directory / "calls").rglob("*.json")})
        manifest.update({"status": "succeeded", "provenance": output.get("provenance", manifest["provenance"]),
                         "output_path": (directory / "output.json").relative_to(context["settings"].data).as_posix(),
                         "artifacts": artifacts, "warnings": output.get("warnings", output.get("errors", []))})
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


def publish_local_artifacts(context, conn):
    """Expose successful local work even if an independent model stage failed."""
    settings, job = context["settings"], context["job"]
    scenes = context["outputs"].get("geometry", context["outputs"].get("reconstruct", {})).get("scenes", [])
    for source in scenes:
        scene = deepcopy(source)
        prior = conn.execute("SELECT payload FROM scenes WHERE id=? AND assessment_id=?", (scene["id"], job["assessment_id"])).fetchone()
        if prior is None:
            raise db.Missing("Reconstructed room no longer exists")
        previous = json.loads(prior["payload"])
        same_geometry = (scene.get("analysis_sha256") is not None
                         and scene["analysis_sha256"] == previous.get("analysis_sha256")
                         and scene.get("selected_photo_ids") == previous.get("selected_photo_ids"))
        if not scene.get("analysis_sha256") and scene.get("ply_path") and previous.get("ply_path"):
            same_geometry = (scene["ply_path"] == previous["ply_path"]
                             and scene.get("selected_photo_ids") == previous.get("selected_photo_ids"))
        if same_geometry and previous.get("calibration"):
            scene.update({key: previous[key] for key in ("scale_state", "scale_factor", "calibration")})
        elif previous and not same_geometry:
            conn.execute("DELETE FROM measurements WHERE assessment_id=? AND scene_id=?", (job["assessment_id"], scene["id"]))
            conn.execute("DELETE FROM scene_links WHERE assessment_id=? AND (from_scene_id=? OR to_scene_id=?)",
                         (job["assessment_id"], scene["id"], scene["id"]))
        for path_key, asset_key, media in (("ply_path", "ply_asset_id", "application/octet-stream"),
                                          ("analysis_path", "analysis_asset_id", "application/octet-stream")):
            if scene.get(path_key):
                scene[asset_key] = db.store_asset(settings, conn, job["assessment_id"], scene[path_key], media, scene["provenance"], "derived")
        scene["input_revision"] = job["input_revision"]
        scene["reconstruction_job_id"] = job["id"]
        scene["source_photos"] = [{"id": p["id"], "sha256": p["payload"]["upright_sha256"]}
                                 for p in context["photos"] if p["id"] in scene.get("selected_photo_ids", [])]
        manifest_path = context["directory"] / "assets" / f"{scene['id']}-{job['id']}-scene.json"
        atomic_json(manifest_path, {key: value for key, value in scene.items() if not key.endswith("path")})
        scene["manifest_asset_id"] = db.store_asset(settings, conn, job["assessment_id"],
            manifest_path.relative_to(settings.data).as_posix(), "application/json", scene["provenance"], "derived")
        conn.execute("UPDATE scenes SET payload=? WHERE id=? AND assessment_id=?", (db.dumps(scene), scene["id"], job["assessment_id"]))
    avatar = deepcopy(context["outputs"].get("avatar", {}))
    for path_key, asset_key, media in (("path", "asset_id", "model/gltf-binary"),
                                      ("preview_path", "preview_asset_id", "image/png"),
                                      ("profile_json_path", "profile_json_asset_id", "application/json")):
        if avatar.get(path_key):
            avatar[asset_key] = db.store_asset(settings, conn, job["assessment_id"], avatar[path_key], media, avatar["provenance"], "clinical")
    return {key: value for key, value in avatar.items() if not key.endswith("path")}


def publish_analysis(context):
    settings, job = context["settings"], context["job"]
    output = deepcopy(context["outputs"]["gemma"])
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
        photo_rows = {p["id"]: p for p in context["photos"]}
        scene_rows = {s["id"]: s for s in context["outputs"]["geometry"]["scenes"]}
        anchor_abstentions = list(context["outputs"]["geometry"].get("anchor_abstentions", []))
        output["evidence_markers"] = {}
        for hazard in output["hazards"]:
            hazard = dict(hazard)
            if not hazard.get("anchor"):
                for proposed in context["outputs"]["geometry"].get("anchors", []):
                    if hazard["category"] != proposed["category"]:
                        continue
                    from step_wise.pipeline.geometry import box_overlap
                    boxed_photos = {e["photo_id"] for e in hazard["evidence"] if e.get("box")}
                    if (set(proposed["anchor"]["source_photo_ids"]).issubset(boxed_photos)
                            and any(e["photo_id"] == proposed["photo_id"] and e.get("box") and box_overlap(e["box"], proposed["evidence_box"]) >= .5 for e in hazard["evidence"])):
                        anchored = {**hazard, "anchor": proposed["anchor"]}
                        try:
                            validate_hazard(Hazard.model_validate(anchored), photo_rows, scene_rows, context["guidance"],
                                            context["assessment_input"]["profile"], context["measurements"])
                        except OutputValidationFailure:
                            anchor_abstentions.append({"photo_id": proposed["photo_id"], "category": hazard["category"],
                                                       "reason": "Proposed geometry did not validate against the hazard evidence"})
                            continue
                        hazard = anchored
                        break
            validate_hazard(Hazard.model_validate(hazard), photo_rows, scene_rows, context["guidance"],
                            context["assessment_input"]["profile"], context["measurements"])
            hazard_id = db.uid()
            conn.execute("INSERT INTO hazards VALUES(?,?,?,?)", (hazard_id, row["id"], job["id"], db.dumps(hazard)))
            from step_wise.pipeline.geometry import box_overlap
            for proposed in context["outputs"]["geometry"].get("evidence_markers", []):
                marker = proposed["marker"]
                if proposed["category"] == hazard["category"] and any(
                    e["photo_id"] == marker["photo_id"] and e.get("box")
                    and box_overlap(e["box"], marker["evidence_box"]) >= .5 for e in hazard["evidence"]
                ):
                    output["evidence_markers"][hazard_id] = marker
                    break
        output["avatar"] = publish_local_artifacts(context, conn)
        output["geometry_status"] = context["outputs"]["geometry"].get("status", "unknown")
        output["anchor_abstentions"] = anchor_abstentions
        output["warnings"] = list(output.get("warnings", [])) + list(context["outputs"]["geometry"].get("warnings", []))
        output["modes"] = settings.modes()
        conn.execute("UPDATE assessments SET analysis_job_id=?,status='review_ready',updated_at=? WHERE id=?", (job["id"], db.now(), row["id"]))
        conn.execute("UPDATE jobs SET output=?,status='succeeded',finished_at=? WHERE id=?", (db.dumps(output), db.now(), job["id"]))


def heartbeat(settings, jid, stop):
    while not stop.wait(10):
        with db.connect(settings, write=True) as conn:
            conn.execute("UPDATE jobs SET heartbeat=? WHERE id=? AND status='running'", (db.now(), jid))


def process_job(settings, job):
    context = None
    manifests = {}
    active_stage = None
    stop = threading.Event()
    thread = threading.Thread(target=heartbeat, args=(settings, job["id"], stop), daemon=True)
    thread.start()
    try:
        context = load_context(settings, job)
        for stage in job_stages(job["kind"]):
            active_stage = stage
            with db.connect(settings, write=True) as conn:
                conn.execute("UPDATE jobs SET stage=?,heartbeat=? WHERE id=?", (stage, db.now(), job["id"]))
            blocked = [dep for dep in DEPENDENCIES[stage] if manifests.get(dep, {}).get("status") != "succeeded"]
            if job["kind"] == "analyze" and blocked:
                manifests[stage] = {"stage": stage, "status": "blocked", "dependencies": blocked}
                atomic_json(context["directory"] / "stages" / stage / "manifest.json", manifests[stage])
            else:
                try:
                    if job["kind"] != "analyze" or not reuse_stage(stage, context, manifests):
                        execute_stage(stage, context, manifests)
                except Exception:
                    if job["kind"] != "analyze" or stage not in manifests:
                        raise
            with db.connect(settings, write=True) as conn:
                conn.execute("UPDATE jobs SET stages=? WHERE id=?", (db.dumps(manifests), job["id"]))
        if job["kind"] == "analyze":
            active_stage = "publish"
            if any(manifests[stage]["status"] != "succeeded" for stage in job_stages("analyze")):
                with db.connect(settings, write=True) as conn:
                    row = db.assessment(conn, job["assessment_id"])
                    db.revisions(row, job["input_revision"], job["review_revision"])
                    avatar = publish_local_artifacts(context, conn)
                    request = json.loads(job["output"])
                    request["partial_results"] = {"avatar": avatar, "warnings": [
                        "Analysis is incomplete; any available local artifacts are provisional and findings cannot be reviewed or released."]}
                    conn.execute("UPDATE jobs SET output=? WHERE id=?", (db.dumps(request), job["id"]))
                raise ExternalFailure("Some analysis stages failed; local artifacts were preserved")
            publish_analysis(context)
    except Exception:
        failed_stage = next((stage for stage in job_stages(job["kind"])
                             if manifests.get(stage, {}).get("status") == "failed"), active_stage)
        with db.connect(settings, write=True) as conn:
            conn.execute("UPDATE jobs SET status='failed',stage=?,stages=?,error=?,finished_at=? WHERE id=?",
                         (failed_stage, db.dumps(manifests), "Processing failed. Inspect the failed stage; retry creates a new attempt.", db.now(), job["id"]))
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
