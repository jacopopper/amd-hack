#!/usr/bin/env python3
"""Explicit API integration check. Starts no services and performs no implicit model work."""
import argparse
import os
import time
from pathlib import Path

import requests

from step_wise.config import ROOT
from step_wise.demo import load_case


def run(base_url, mode, case_path=None, allow_external=False):
    case_path = case_path or ROOT / "demo/cases/room-001.json"
    case, dataset, photos, profile = load_case(case_path)
    if mode == "live":
        if not allow_external or not all(case.get("consents", {}).get(key) for key in
                                         ("external_processing_consent", "clinical_external_processing_consent")):
            raise RuntimeError("Live smoke requires --allow-external and both explicit consents in the frozen case manifest")
        if dataset["data_kind"] not in {"staged_capture", "single_image_experiment", "multi_room_capture"}:
            raise RuntimeError("Live smoke requires a declared capture or single-image experiment, not synthetic controls")
        profile = {"description": "Fictional smoke profile, not a real patient.", "mobility_aid": "walker",
                   "relevant_history": "Fictional healed previous hip fracture; no current hip fracture stated."}
    session = requests.Session()
    session.auth = (os.getenv("REVIEWER_USER", "reviewer"), os.environ["REVIEWER_PASSWORD"])

    def call(method, path, **kwargs):
        response = session.request(method, base_url.rstrip("/") + "/api" + path,
                                   timeout=(5, 300 if path.endswith("/profile/draft") else 30),
                                   allow_redirects=False, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {path.split('?')[0]} returned {response.status_code}")
        return response.json()

    health = call("GET", "/health")
    expected = ({"reconstruction": "fixture", "gemma": "fixture", "jev": "disabled"} if mode == "fixture"
                else {"reconstruction": "vggt", "gemma": "http", "avatar": "mpfb", "jev": "live"})
    if any(health["modes"].get(k) != v for k, v in expected.items()):
        raise RuntimeError("Configured API backends do not match the requested smoke mode")
    aid = call("POST", "/assessments", json={"label": f"Smoke {mode} {case['id']}", "profile": profile,
               "profile_confirmed": mode == "fixture", "staged_data_confirmed": True, "processing_consent": True})["id"]

    def assessment():
        return call("GET", f"/assessments/{aid}")

    def revisions():
        a = assessment()
        return {"expected_input_revision": a["input_revision"], "expected_review_revision": a["review_revision"]}

    def wait_job(jid):
        deadline = time.monotonic() + (120 if mode == "fixture" else 900)
        while time.monotonic() < deadline:
            job = call("GET", f"/jobs/{jid}")
            if job["status"] == "succeeded":
                return job
            if job["status"] in {"failed", "interrupted"}:
                raise RuntimeError(f"Job failed at {job['stage']}; inspect job {jid}")
            time.sleep(.5)
        raise RuntimeError(f"Timed out observing job {jid}; do not automatically restart it")

    for p in photos:
        with (ROOT / p["path"]).open("rb") as source:
            call("POST", f"/assessments/{aid}/photos", data={"scene_label": p["scene_label"], "role": p["role"], "expected_input_revision": assessment()["input_revision"]}, files={"file": ("photo.png", source, "image/png")})

    def consent():
        current = assessment()
        call("PATCH", f"/assessments/{aid}", json={**current["inputs"], "expected_input_revision": current["input_revision"],
             "external_processing_consent": True, "clinical_external_processing_consent": True})

    if mode == "live":
        # Never accept a real clinical report for the smoke workflow.
        fictional = b"Fictional test report, not a real patient. Uses a walker. Previous hip fracture is healed. No current hip fracture."
        call("POST", f"/assessments/{aid}/clinical-documents",
             data={"expected_input_revision": assessment()["input_revision"]},
             files={"file": ("fictional-smoke.txt", fictional, "text/plain")})
        consent()
        draft = call("POST", f"/assessments/{aid}/profile/draft", json=revisions())
        if not draft.get("claims"):
            raise RuntimeError("Fictional clinical extraction abstained; inspect the draft before any further calls")
        if (not any(claim["field"] == "mobility_aid" and claim["status"] == "current" for claim in draft["claims"])
                or any(claim["field"] == "hip_condition" and claim["status"] == "current" for claim in draft["claims"])):
            raise RuntimeError("Fictional clinical draft did not distinguish current aid from hip history/negation")
        call("PUT", f"/assessments/{aid}/profile/confirm", json={**revisions(), "profile": profile,
             "claims": [{**claim, "reviewer_confirmed": True} for claim in draft["claims"]]})
        consent()
    wait_job(call("POST", f"/assessments/{aid}/analyze", json=revisions())["job_id"])
    bundle = call("GET", f"/assessments/{aid}/bundle")
    assert bundle["schema_version"] == 1 and bundle["profile_confirmed"]
    if mode == "live":
        assert bundle["avatar"].get("rigged") and bundle["avatar"].get("glb_url"), "Missing verified MPFB avatar"
        assert all(room["scale_state"] == "unknown" for room in bundle["rooms"]), "Uncalibrated smoke claimed metric scale"
        assert all(room["provenance"] == "live" and room["status"] == "ready" for room in bundle["rooms"]), "Reconstruction incomplete"
        if dataset["data_kind"] == "single_image_experiment":
            assert bundle["rooms"][0]["reconstruction_kind"] == "single_view_estimate"
            assert all(not hazard["anchor"] for hazard in bundle["hazards"]), "Single-view smoke produced a 3D anchor"
    findings = call("GET", f"/assessments/{aid}/findings")
    if mode == "fixture":
        assert findings["provenance"] == "fixture"
        assert all(c["evidence"] is None for c in findings["candidates"] if c["presence"] != "present")
        assert all(s.get("scale_state") == "unknown" for s in call("GET", f"/assessments/{aid}/scenes"))
    if mode == "fixture":
        assert findings["hazards"], "Expected synthetic review candidates"
    rejected = set()
    for index, h in enumerate(findings["hazards"]):
        decision = ["approve", "edit", "reject", "defer"][index % 4]
        if decision == "reject":
            rejected.add(h["id"])
        call("PUT", f"/assessments/{aid}/reviews/{h['id']}", json={**revisions(), "decision": decision, **({"suggested_action": h["suggested_action"] + " Verifica del revisore simulato."} if decision == "edit" else {}), "note": "Simulated lifecycle smoke review; not clinical approval."})
    drafted = wait_job(call("POST", f"/assessments/{aid}/reports", json=revisions())["job_id"])
    report = call("GET", f"/reports/{drafted['output']['report_id']}")
    assert rejected.isdisjoint(h["id"] for h in report["snapshot"]["accepted"])
    if mode == "live":
        # Generated findings and simulated decisions must never authorize a release.
        assert report["status"] == "draft"
        assert report["snapshot"]["draft_provenance"] == "live_gemma", "The draft used a deterministic fallback, not live Gemma"
        print(f"PASS: live API draft workflow; assessment={aid}; simulated review; no release; real-model quality requires manual inspection")
        return aid
    # Editing a review invalidates the earlier draft even when every reference exists.
    first = findings["hazards"][0]
    call("PUT", f"/assessments/{aid}/reviews/{first['id']}", json={**revisions(), "decision": "approve"})
    stale = session.post(base_url + f"/api/reports/{report['id']}/release", json=revisions(), timeout=10)
    assert stale.status_code == 409, "Stale report was releasable"
    drafted = wait_job(call("POST", f"/assessments/{aid}/reports", json=revisions())["job_id"])
    rid = drafted["output"]["report_id"]
    capability = call("POST", f"/reports/{rid}/release", json=revisions())
    # Never print/store plaintext family capabilities in smoke logs.
    public = requests.get(base_url + "/api/family/" + capability["token"] + "?format=pdf", timeout=30)
    assert public.status_code == 200 and public.content.startswith(b"%PDF")
    asset_url = assessment()["photos"][0]["asset_url"]
    assert requests.get(base_url + asset_url, timeout=10).status_code == 401
    assert requests.get(base_url + f"/api/reports/{rid}", timeout=10).status_code == 401
    print(f"PASS: {mode} API lifecycle; assessment={aid}; simulated review; real-model quality={'pending' if mode == 'fixture' else 'requires manual inspection'}")
    return aid


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fixture", "live"), required=True)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--case", type=Path, default=ROOT / "demo/cases/room-001.json")
    parser.add_argument("--allow-external", action="store_true", help="Authorize the selected case images and fictional profile/report to the configured providers")
    args = parser.parse_args()
    run(args.base_url.rstrip("/"), args.mode, args.case.resolve(), args.allow_external)
