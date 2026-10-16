#!/usr/bin/env python3
"""Explicit API integration check. Starts no services and performs no implicit model work."""
import argparse
import os
import time

import requests

from safe_home.config import ROOT
from safe_home.demo import load_case


def run(base_url, mode):
    session = requests.Session()
    session.auth = (os.getenv("REVIEWER_USER", "reviewer"), os.environ["REVIEWER_PASSWORD"])

    def call(method, path, **kwargs):
        response = session.request(method, base_url.rstrip("/") + "/api" + path, timeout=(5, 30), allow_redirects=False, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {path.split('?')[0]} returned {response.status_code}")
        return response.json()

    health = call("GET", "/health")
    expected = {"reconstruction": "fixture", "gemma": "fixture", "jev": "disabled"} if mode == "fixture" else {"reconstruction": "vggt", "gemma": "http"}
    if any(health["modes"].get(k) != v for k, v in expected.items()):
        raise RuntimeError("Configured API backends do not match the requested smoke mode")
    case, dataset, photos, profile = load_case(ROOT / "demo/cases/room-001.json")
    if mode == "live" and dataset["data_kind"] != "staged_capture":
        raise RuntimeError("Live smoke requires a real staged capture; current room-001 is synthetic")
    aid = call("POST", "/assessments", json={"label": f"Smoke {mode} room-001", "profile": profile, "profile_confirmed": True, "staged_data_confirmed": True, "processing_consent": True, "external_processing_consent": health["modes"].get("jev") == "live"})["id"]

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
    wait_job(call("POST", f"/assessments/{aid}/analyze", json=revisions())["job_id"])
    findings = call("GET", f"/assessments/{aid}/findings")
    if mode == "fixture":
        assert findings["provenance"] == "fixture"
        assert all(c["evidence"] is None for c in findings["candidates"] if c["presence"] != "present")
        assert all(s.get("scale_state") == "unknown" for s in call("GET", f"/assessments/{aid}/scenes"))
    assert findings["hazards"], "Expected at least one review candidate for room-001"
    rejected = set()
    for index, h in enumerate(findings["hazards"]):
        decision = ["approve", "edit", "reject", "defer"][index % 4]
        if decision == "reject":
            rejected.add(h["id"])
        call("PUT", f"/assessments/{aid}/reviews/{h['id']}", json={**revisions(), "decision": decision, **({"suggested_action": h["suggested_action"] + " Verifica del revisore simulato."} if decision == "edit" else {}), "note": "Simulated lifecycle smoke review; not clinical approval."})
    drafted = wait_job(call("POST", f"/assessments/{aid}/reports", json=revisions())["job_id"])
    report = call("GET", f"/reports/{drafted['output']['report_id']}")
    assert rejected.isdisjoint(h["id"] for h in report["snapshot"]["accepted"])
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
    args = parser.parse_args()
    run(args.base_url.rstrip("/"), args.mode)
