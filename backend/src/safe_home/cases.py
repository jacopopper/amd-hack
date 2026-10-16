import html
import json
import os
import re
import shlex
import shutil
import sys
import platform
from importlib.metadata import version
from dataclasses import replace
from pathlib import Path

from safe_home import db, worker
from safe_home.config import ROOT, Settings
from safe_home.demo import load_case, seed_case
from safe_home.pipeline.geometry import box_overlap


def find_run(settings, run_id):
    if not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", run_id):
        raise ValueError("Invalid run ID")
    matches = list((settings.data / "case-runs").glob(f"*/{run_id}/run.json"))
    if len(matches) != 1:
        raise db.Missing("Case run not found")
    return matches[0].parent


def read(path):
    return json.loads(path.read_text())


def run_settings(settings, directory):
    return replace(settings, data=directory, database=directory / "assessment.sqlite3", worker_lock_path=settings.worker_lock_path or settings.data / "worker.lock")


def verify_artifacts(directory, manifest):
    if manifest.get("status") != "succeeded":
        raise db.Conflict("Cached stage did not succeed")
    for relative, checksum in manifest.get("artifacts", {}).items():
        path = db.asset_path(Settings(data=directory), relative)
        if not path.is_file() or db.digest(path.read_bytes()) != checksum:
            raise db.Conflict("Cached artifact missing or checksum changed")


def copy_immutable(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


def replay_output(value):
    if isinstance(value, dict):
        return {key: "replay" if key == "provenance" and item == "live" else replay_output(item) for key, item in value.items()}
    if isinstance(value, list):
        return [replay_output(item) for item in value]
    return value


def related_stages(stage):
    required, stale = set(), {stage}
    for _ in worker.STAGES:
        required.update(dependency for name in required | {stage} for dependency in worker.DEPENDENCIES[name])
        stale.update(name for name, dependencies in worker.DEPENDENCIES.items() if set(dependencies) & stale)
    return required, stale - {stage}


def run_case(settings, case_path, mode, stage=None, reuse_id=None):
    case, dataset, selected, profile = load_case(case_path)
    if stage and not reuse_id and stage != "prepare":
        raise db.Conflict(f"Missing prerequisites; run: python -m safe_home.cli case run {shlex.quote(str(case_path))} --mode {mode}")
    with worker.worker_lock(settings):
        run_id = db.uid()
        directory = settings.data / "case-runs" / case["id"] / run_id
        directory.mkdir(parents=True)
        local = run_settings(settings, directory)
        local = replace(local, recon_backend="fixture" if mode == "fixture" else "vggt", gemma_backend="fixture" if mode == "fixture" else "http", jev_enabled=False if mode == "fixture" else settings.jev_enabled)
        input_hash = db.digest(db.dumps({"case": case, "dataset": dataset, "profile": profile}).encode())
        visual_hash = db.digest(db.dumps({"photos": selected, "ids": case["photo_ids"], "scene_label": case["scene_label"]}).encode())
        record = {"schema_version": 1, "id": run_id, "case": case, "case_path": str(case_path), "mode": mode, "modes": local.modes(),
                  "input_hash": input_hash, "visual_input_hash": visual_hash, "created_at": db.now(), "reuse_run": reuse_id, "selected_stage": stage, "status": "running"}
        record["runtime"] = {"python": sys.version, "platform": platform.platform(), "packages": {name: version(name) for name in ("safe-home", "Pillow", "numpy", "pydantic", "requests")}, "model": local.gemma_model, "model_revision": local.gemma_revision}
        worker.atomic_json(directory / "run.json", record)
        manifests = {}
        try:
            if mode == "live" and dataset["data_kind"] != "staged_capture":
                record.update({"status": "blocked", "error": "Real overlapping staged capture and compatible AMD runtime are unavailable. Synthetic controls cannot validate live quality."})
                return finish(directory, record, {}, 3)
            prior = find_run(settings, reuse_id) if reuse_id else None
            if prior:
                prior_record = read(prior / "run.json")
                if prior_record.get("visual_input_hash") != visual_hash:
                    raise db.Conflict("Photo input hash changed; rebuild prepare in a fresh run without --reuse-run")
                if prior_record["mode"] != mode:
                    raise db.Conflict("Cannot reuse fixture prerequisites in live mode")
                old_settings = run_settings(settings, prior)
                with db.connect(old_settings) as old, db.connect(local) as new:
                    old.backup(new)
                for p in prior.rglob("*"):
                    if p.is_file() and (p.relative_to(prior).parts[0] in {"assets", "assessments"}):
                        copy_immutable(p, directory / p.relative_to(prior))
                record["assessment_id"], record["photo_mapping"] = prior_record["assessment_id"], prior_record["photo_mapping"]
                with db.connect(local, write=True) as conn:
                    previous = db.assessment(conn, record["assessment_id"])
                    previous_input = json.loads(previous["input_json"])
                    if previous_input["profile"] != profile:
                        previous_input["profile"] = profile
                        conn.execute("UPDATE assessments SET input_json=?,input_revision=input_revision+1,review_revision=0,analysis_job_id=NULL WHERE id=?", (db.dumps(previous_input), record["assessment_id"]))
                    conn.execute("UPDATE assessments SET status=? WHERE id=?", ("review_ready" if stage == "report" else "draft", record["assessment_id"]))
                    conn.execute("UPDATE jobs SET status='interrupted' WHERE status IN ('running','queued')")
            else:
                db.init(local)
                record["assessment_id"], record["photo_mapping"] = seed_case(local, case_path)
            with db.connect(local) as conn:
                assessment = db.assessment(conn, record["assessment_id"])
            jid = db.enqueue(local, assessment["id"], "report" if stage == "report" else "analyze", assessment["input_revision"], assessment["review_revision"])
            job = worker.claim(local)
            record["job_id"] = jid
            context = worker.load_context(local, job, directory=directory)
            prerequisites, invalidated = related_stages(stage) if stage else (set(), set())
            for name in worker.STAGES if stage == "report" else worker.STAGES[:5]:
                if name in invalidated:
                    manifests[name] = {"stage": name, "status": "stale", "reason": f"Rerun of {stage}; rebuild dependent outputs"}
                    continue
                cached = prior / "stages" / name / "manifest.json" if prior else None
                required = name in prerequisites
                can_reuse = cached and cached.is_file() and name != stage and read(cached).get("status") == "succeeded"
                if can_reuse:
                    saved = read(cached)
                    expected, _ = worker.fingerprint(name, context, manifests)
                    if expected != saved["fingerprint"]:
                        if required:
                            raise db.Conflict(f"Incompatible {name}; rerun with --stage {name} --reuse-run {reuse_id}")
                        can_reuse = False
                    else:
                        verify_artifacts(prior, saved)
                        for relative in saved["artifacts"]:
                            if not (directory / relative).exists():
                                copy_immutable(prior / relative, directory / relative)
                        context["outputs"][name] = read(cached.with_name("output.json"))
                        saved.update({"provenance": "fixture" if mode == "fixture" else "replay", "origin_run": saved.get("origin_run", reuse_id), "reused_in_run": run_id})
                        if mode == "live":
                            context["outputs"][name] = replay_output(context["outputs"][name])
                            output_path = directory / "stages" / name / "output.json"
                            worker.atomic_json(output_path, context["outputs"][name])
                            saved["artifacts"][output_path.relative_to(directory).as_posix()] = db.digest(output_path.read_bytes())
                        # These are new attempt metadata; never hardlink writable manifests.
                        worker.atomic_json(directory / "stages" / name / "manifest.json", saved)
                        copy_immutable(cached.with_name("input.json"), directory / "stages" / name / "input.json")
                        manifests[name] = saved
                if not can_reuse:
                    if required:
                        raise db.Conflict(f"Missing prerequisite {name}; rerun with --stage {name} --reuse-run {reuse_id}")
                    if stage and name != stage:
                        manifests[name] = {"stage": name, "status": "stale", "reason": "Independent cached output unavailable; no implicit model call"}
                        continue
                    worker.execute_stage(name, context, manifests)
            if "report" in invalidated:
                manifests["report"] = {"stage": "report", "status": "stale", "reason": "Upstream evidence changed; compatible review is required"}
            if stage == "report":
                record["status"] = "report_ready"
            elif all(manifests.get(s, {}).get("status") == "succeeded" for s in worker.STAGES[:5]):
                worker.publish_analysis(context)
                record["status"] = "review_ready"
            else:
                with db.connect(local, write=True) as conn:
                    conn.execute("UPDATE jobs SET status='interrupted',error='Targeted stage complete; downstream stages stale' WHERE id=?", (jid,))
                    conn.execute("UPDATE assessments SET status='draft',analysis_job_id=NULL WHERE id=?", (assessment["id"],))
                record["status"] = "partial"
            with db.connect(local, write=True) as conn:
                conn.execute("UPDATE jobs SET stages=? WHERE id=?", (db.dumps(manifests), jid))
        except Exception as exc:
            record.update({"status": "failed", "error": str(exc) if isinstance(exc, (db.Conflict, db.Missing)) else f"{type(exc).__name__}: case stage failed; see stages/*/error.json"})
            if record.get("job_id"):
                with db.connect(local, write=True) as conn:
                    conn.execute("UPDATE jobs SET status='failed',error=?,stages=? WHERE id=?", (record["error"], db.dumps(manifests), record["job_id"]))
                    conn.execute("UPDATE assessments SET status='failed' WHERE id=?", (record["assessment_id"],))
            return finish(directory, record, manifests, 1)
        return finish(directory, record, manifests)


def finish(directory, record, manifests, forced_code=None):
    record["stages"] = manifests
    worker.atomic_json(directory / "run.json", record)
    checks, code = check_run(directory)
    gallery(directory, record, checks)
    return record, forced_code if forced_code is not None else code


def check_run(directory):
    record = read(directory / "run.json")
    if (directory / "baseline.json").exists():
        baseline = read(directory / "baseline.json")
        valid = baseline["records_sha256"] == records_hash(directory) and all((directory / p).is_file() and db.digest((directory / p).read_bytes()) == checksum for p, checksum in baseline["files"].items())
        return read(directory / "checks.json") if valid else [{"id": "baseline-integrity", "status": "fail", "observed": "Frozen baseline changed"}], 0 if valid else 2
    checks = []
    outputs = {}
    for stage in worker.STAGES[:5]:
        p = directory / "stages" / stage / "output.json"
        manifest = record["stages"].get(stage, {})
        state = manifest.get("status", "blocked" if record["status"] == "blocked" else "skipped")
        if state == "succeeded":
            try:
                verify_artifacts(directory, manifest)
                outputs[stage] = read(p)
            except (ValueError, db.Conflict):
                state = "failed"
        checks.append({"id": f"stage-{stage}", "status": {"succeeded": "pass", "failed": "fail", "stale": "blocked"}.get(state, state), "evidence": f"stages/{stage}/manifest.json"})
    candidates = outputs.get("jev", {}).get("candidates", [])
    scenes = outputs.get("geometry", {}).get("scenes", [])
    for expectation in record["case"]["expectations"]:
        observed, passed = None, False
        if expectation["kind"] == "candidate" and "jev" in outputs:
            photo = record["photo_mapping"][expectation["photo_id"]]
            match = [c for c in candidates if c["photo_id"] == photo and c["category"] == expectation["category"]]
            observed = match
            passed = len(match) == 1 and all(match[0].get(k) == v for k, v in expectation.items() if k in {"presence", "coverage", "attachment", "evidence"})
            if passed and expectation.get("box"):
                with db.connect(Settings(data=directory)) as conn:
                    size = json.loads(conn.execute("SELECT payload FROM photos WHERE id=?", (photo,)).fetchone()[0])
                actual = match[0]["evidence"]["box"] if match[0]["evidence"] else None
                expected = [expectation["box"][i] * (size["width"] if i % 2 == 0 else size["height"]) for i in range(4)]
                passed = actual is not None and box_overlap(actual, expected) >= expectation["min_iou"]
        elif expectation["kind"] == "unknown_scale" and "geometry" in outputs:
            observed = [{"scale_state": s.get("scale_state"), "scale_factor": s.get("scale_factor"), "measurements": s.get("measurements")} for s in scenes]
            passed = bool(scenes) and all(s.get("scale_state") == "unknown" and s.get("scale_factor") is None and not s.get("measurements") for s in scenes)
        elif expectation["kind"] == "finite_geometry" and "reconstruct" in outputs:
            import numpy as np
            observed = [s.get("bounds") for s in outputs["reconstruct"]["scenes"]]
            passed = bool(observed) and all(b is not None and np.asarray(b).shape == (2, 3) and np.isfinite(b).all() and np.max(np.abs(b)) < 1e6 for b in observed)
        elif expectation["kind"] == "source_validity" and "gemma" in outputs:
            from safe_home.validation import validate_output
            from safe_home.models import AssessmentOutput
            local = Settings(data=directory)
            context = worker.load_context(local, {"assessment_id": record["assessment_id"]}, directory)
            value = {k: outputs["gemma"][k] for k in ("hazards", "coverage", "follow_up_requests")}
            try:
                validate_output(AssessmentOutput.model_validate(value), context, {s["id"]: s for s in scenes}, context["guidance"])
                observed, passed = "All source references validated", True
            except ValueError:
                observed = "Invalid source reference"
        checks.append({"id": expectation["id"], "status": "pass" if passed else ("fail" if observed is not None else "blocked"), "observed": observed, "evidence": "stages/jev/output.json" if expectation["kind"] == "candidate" else "stages/geometry/output.json", "scope": "fixture contract" if record["mode"] == "fixture" else "live expectation"})
    checks.append({"id": "real-model-quality", "status": "blocked" if record["mode"] == "fixture" else "manual_pending", "required": False, "observed": "Fixtures cannot establish visual accuracy or AMD execution"})
    attestations = read(directory / "manual_checks.json") if (directory / "manual_checks.json").is_file() else {}
    revision = None
    if record.get("assessment_id"):
        with db.connect(Settings(data=directory)) as conn:
            row = db.assessment(conn, record["assessment_id"])
            revision = {key: row[key] for key in ("input_revision", "review_revision", "analysis_job_id")}
    for name in record["case"]["manual_checks"]:
        current = name in attestations and attestations[name].get("revision") == revision
        checks.append({"id": name, "status": "pass" if current else "manual_pending", "evidence": attestations.get(name, "index.html and normal reviewer UI")})
    code = 1 if record["status"] == "failed" else 2 if any(c["status"] == "fail" for c in checks) else 3 if any(c["status"] == "blocked" and c.get("required", True) for c in checks) else 4 if any(c["status"] == "manual_pending" and c.get("required", True) for c in checks) else 0
    worker.atomic_json(directory / "checks.json", checks)
    return checks, code


def gallery(directory, record, checks):
    from PIL import Image, ImageDraw
    prepared_path = directory / "stages/prepare/output.json"
    candidates_path = directory / "stages/jev/output.json"
    prepared = read(prepared_path).get("photos", []) if prepared_path.exists() else []
    candidates = read(candidates_path).get("candidates", []) if candidates_path.exists() else []
    reverse_mapping = {v: k for k, v in record.get("photo_mapping", {}).items()}
    overlays = []
    for photo in prepared:
        source = db.asset_path(Settings(data=directory), photo["prepared_path"])
        if not source.exists():
            continue
        with Image.open(source) as original:
            image = original.copy()
        draw = ImageDraw.Draw(image)
        sx, sy = photo["transform"]["scale_xy"]
        ox, oy = photo["transform"]["offset_xy"]
        for candidate in candidates:
            if candidate["photo_id"] == photo["photo_id"] and candidate["evidence"] and candidate["evidence"]["box"]:
                l, t, r, b = candidate["evidence"]["box"]
                draw.rectangle([l * sx + ox, t * sy + oy, r * sx + ox, b * sy + oy], outline="#e47796", width=3)
        w, h = photo["upright_size"]
        for expected in record["case"]["expectations"]:
            if expected.get("photo_id") == reverse_mapping.get(photo["photo_id"]) and expected.get("box"):
                l, t, r, b = expected["box"]
                draw.rectangle([l * w * sx + ox, t * h * sy + oy, r * w * sx + ox, b * h * sy + oy], outline="#42b586", width=2)
        target = directory / "assets" / f"{photo['photo_id']}-overlay.jpg"
        temporary = target.with_suffix(".tmp")
        image.save(temporary, "JPEG", quality=90)
        os.replace(temporary, target)
        overlays.append((target, photo["photo_id"]))
    images = "".join(f'<figure><img style="max-width:100%" src="{html.escape(str(p.relative_to(directory)))}"><figcaption>{html.escape(photo_id)} · green: expected / pink: observed after gates</figcaption></figure>' for p, photo_id in overlays)
    page = '<!doctype html><meta charset="utf-8"><title>Safe Home case evidence</title><style>body{font:16px sans-serif;max-width:960px;margin:40px auto;background:#f4f1e7;color:#243b31}pre{white-space:pre-wrap;overflow-wrap:anywhere}figure{margin:24px 0}</style>'
    page += f'<h1>{html.escape(record["case"]["id"])} / {html.escape(record["mode"])}</h1><p>{html.escape(record["status"])} · Synthetic fixture output is not live inference. Use the normal reviewer UI to inspect 3D.</p>'
    page += '<pre>' + html.escape(json.dumps(checks, ensure_ascii=False, indent=2)) + '</pre>' + images
    (directory / "index.html").write_text(page)


def inspect(settings, run_id):
    directory = find_run(settings, run_id)
    record = read(directory / "run.json")
    command = f"SAFE_HOME_DATA={shlex.quote(str(directory))} SAFE_HOME_DATABASE={shlex.quote(str(directory / 'assessment.sqlite3'))} SAFE_HOME_WORKER_LOCK={shlex.quote(str(settings.worker_lock_path or settings.data / 'worker.lock'))} .venv/bin/uvicorn safe_home.api:create_app --factory --no-access-log --host 127.0.0.1 --port 8000"
    return {"id": run_id, "status": record["status"], "mode": record["mode"], "gallery": str(directory / "index.html"), "checks": read(directory / "checks.json"), "reviewer_launch": command, "assessment_url": f"http://localhost:8000/?assessment={record.get('assessment_id', '')}", "error": record.get("error")}


def records_hash(directory):
    with db.connect(Settings(data=directory)) as conn:
        records = {table: [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")] for table in ("assessments", "photos", "assets", "scenes", "hazards", "reviews", "reports")}
    return db.digest(db.dumps(records).encode())


def attest(settings, run_id, check_id, note):
    directory = find_run(settings, run_id)
    if (directory / "baseline.json").exists():
        raise db.Conflict("Baseline is frozen; create a new attempt")
    record = read(directory / "run.json")
    if check_id not in record["case"]["manual_checks"] or not note.strip():
        raise ValueError("Use a named manual check and a concrete evidence note")
    if check_id == "final-report":
        with db.connect(Settings(data=directory)) as conn:
            row = db.assessment(conn, record["assessment_id"])
            if row["status"] != "released" or not conn.execute("SELECT 1 FROM reports WHERE status='released' AND assessment_id=? AND input_revision=? AND review_revision=?", (row["id"], row["input_revision"], row["review_revision"])).fetchone():
                raise db.Conflict("Inspect and release the report in the normal reviewer app first")
    with db.connect(Settings(data=directory)) as conn:
        row = db.assessment(conn, record["assessment_id"])
        revision = {key: row[key] for key in ("input_revision", "review_revision", "analysis_job_id")}
    values = read(directory / "manual_checks.json") if (directory / "manual_checks.json").exists() else {}
    values[check_id] = {"note": note, "reviewer": settings.reviewer_identity, "at": db.now(), "scope": record["mode"], "revision": revision}
    worker.atomic_json(directory / "manual_checks.json", values)
    checks, code = check_run(directory)
    gallery(directory, record, checks)
    return checks, code


def freeze(settings, run_id):
    directory = find_run(settings, run_id)
    if (directory / "baseline.json").exists():
        raise db.Conflict("Baseline is already frozen")
    checks, code = check_run(directory)
    if code != 0:
        raise db.Conflict("Required checks and manual inspection must pass before baseline selection")
    with db.connect(Settings(data=directory)) as conn:
        if conn.execute("SELECT 1 FROM jobs WHERE status IN ('running','queued')").fetchone():
            raise db.Conflict("Cannot freeze active work")
    files = {str(p.relative_to(directory)): db.digest(p.read_bytes()) for p in directory.rglob("*") if p.is_file() and (p.suffix in {".json", ".html", ".ply", ".jpg", ".png", ".npz", ".pdf"})}
    baseline = {"run_id": run_id, "selected_at": db.now(), "selected_by": settings.reviewer_identity, "mode": read(directory / "run.json")["mode"], "records_sha256": records_hash(directory), "files": files}
    worker.atomic_json(directory / "baseline.json", baseline)
    return {"run_id": run_id, "status": "frozen", "mode": baseline["mode"]}


def compare(settings, baseline, candidate):
    def semantic(directory):
        r = read(directory / "run.json")
        p = directory / "stages/gemma/output.json"
        if not p.exists():
            return r, []
        mapping = {v: k for k, v in r["photo_mapping"].items()}
        return r, sorted(db.dumps({"category": h["category"], "priority": h["priority"], "evidence": [{**e, "photo_id": mapping[e["photo_id"]]} for e in h["evidence"]], "measurement_ids": h["measurement_ids"], "anchor": bool(h["anchor"])}) for h in read(p)["hazards"])
    left, l = semantic(find_run(settings, baseline))
    right, r = semantic(find_run(settings, candidate))
    return {"baseline": baseline, "candidate": candidate, "same_case_input": left["input_hash"] == right["input_hash"], "removed": [v for v in l if v not in r], "added": [v for v in r if v not in l], "baseline_checks": read(find_run(settings, baseline) / "checks.json"), "candidate_checks": read(find_run(settings, candidate) / "checks.json")}
