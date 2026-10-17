import base64
import hashlib
import io
from contextlib import redirect_stdout
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from dataclasses import replace

from fastapi.testclient import TestClient
from PIL import Image

from step_wise import db, worker
from step_wise.api import create_app
from step_wise.config import Settings
from step_wise.models import Candidate, Evidence, GuidanceChecklist, Hazard, Profile
from step_wise.config import ROOT
from step_wise.pipeline.photos import canonical_box, decode, prepared_image
from step_wise.pipeline.photos import ingest
from step_wise.pipeline.reports import validate_draft

def picture():
    stream = io.BytesIO()
    Image.new("RGB", (960, 640), "#adbea1").save(stream, "PNG")
    return stream.getvalue()

class Workflow(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.settings = Settings(data=Path(self.temporary.name))
        self.client = TestClient(create_app(self.settings))

    def tearDown(self):
        self.client.close()
        self.temporary.cleanup()

    def create(self, **inputs):
        response = self.client.post("/api/assessments", json={"label": "Test fittizio", "profile": {"description": "Profilo fittizio"}, "profile_confirmed": True, "staged_data_confirmed": True, "processing_consent": True, **inputs})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def get(self, aid):
        return self.client.get(f"/api/assessments/{aid}").json()

    def revisions(self, aid):
        a = self.get(aid)
        return {"expected_input_revision": a["input_revision"], "expected_review_revision": a["review_revision"]}

    def ready(self, **inputs):
        aid = self.create(**inputs)
        self.assertEqual(self.client.post(f"/api/assessments/{aid}/photos", headers={"Content-Length": str(30 * 1024 * 1024)}, content=b"small").status_code, 413)
        self.assertEqual(self.client.post(f"/api/assessments/{aid}/photos", data={"scene_label": "room", "expected_input_revision": 1}, files={"file": ("ignored-path.png", picture(), "image/png")}).status_code, 201)
        response = self.client.post(f"/api/assessments/{aid}/analyze", json=self.revisions(aid))
        self.assertEqual(response.status_code, 202, response.text)
        worker.run(self.settings, once=True)
        self.assertEqual(self.get(aid)["status"], "review_ready")
        return aid

    def reviewed(self, **inputs):
        aid = self.ready(**inputs)
        findings = self.client.get(f"/api/assessments/{aid}/findings").json()
        self.assertEqual(findings["provenance"], "fixture")
        self.assertEqual(len(findings["hazards"]), 4)
        for candidate in findings["candidates"]:
            if candidate["presence"] == "absent":
                self.assertIsNone(candidate["evidence"])
        decisions = ["approve", "edit", "reject", "defer"]
        for h, decision in zip(findings["hazards"], decisions):
            payload = {**self.revisions(aid), "decision": decision}
            if decision == "edit":
                payload["suggested_action"] = "Azione modificata dal revisore simulato."
                payload["priority"] = "urgent"
            if decision == "defer":
                payload["note"] = "Richiedere una nuova foto."
            response = self.client.put(f"/api/assessments/{aid}/reviews/{h['id']}", json=payload)
            self.assertEqual(response.status_code, 200, response.text)
        return aid, findings

    def report(self, aid):
        r = self.client.post(f"/api/assessments/{aid}/reports", json=self.revisions(aid))
        self.assertEqual(r.status_code, 202, r.text)
        worker.run(self.settings, once=True)
        job = self.client.get(f"/api/jobs/{r.json()['job_id']}").json()
        self.assertEqual(job["status"], "succeeded", job)
        return self.client.get(f"/api/reports/{job['output']['report_id']}").json()

    def test_review_release_capability_and_delete(self):
        aid, findings = self.reviewed()
        injected = '<img src="https://external.invalid/private" onerror="alert(1)">'
        response = self.client.put(f"/api/assessments/{aid}/reviews/{findings['hazards'][0]['id']}", json={**self.revisions(aid), "decision": "edit", "observation": injected})
        self.assertEqual(response.status_code, 200)
        report = self.report(aid)
        accepted = report["snapshot"]["accepted"]
        self.assertEqual([h["priority"] for h in accepted], ["urgent", "important"])
        self.assertTrue(all(h["reviewer"] == self.settings.reviewer_identity for h in accepted))
        self.assertEqual({h["id"] for h in accepted}, {h["id"] for h in findings["hazards"][:2]})
        self.assertEqual(len(report["snapshot"]["deferred"]), 1)
        html = self.client.get(report["html_url"]).text
        self.assertIn("Step Wise", html)
        self.assertNotIn(injected, html)
        self.assertIn("&lt;img", html)
        self.assertIn(report["snapshot_sha256"], html)
        pdf = self.client.get(report["pdf_url"])
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertIn('filename="step-wise.pdf"', pdf.headers["content-disposition"])
        released = self.client.post(f"/api/reports/{report['id']}/release", json=self.revisions(aid))
        self.assertEqual(released.status_code, 200, released.text)
        token = released.json()["token"]
        with db.connect(self.settings) as conn:
            saved = conn.execute("SELECT family_hash FROM reports WHERE id=?", (report["id"],)).fetchone()[0]
            self.assertNotEqual(saved, token)
            self.assertEqual(saved, db.digest(token.encode()))
        family = TestClient(self.client.app)
        self.assertEqual(family.get(f"/api/family/{token}").status_code, 200)
        with db.connect(self.settings, write=True) as conn:
            expiry = conn.execute("SELECT family_expires_at FROM reports WHERE id=?", (report["id"],)).fetchone()[0]
            conn.execute("UPDATE reports SET family_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (report["id"],))
        self.assertEqual(family.get(f"/api/family/{token}").status_code, 404)
        with db.connect(self.settings, write=True) as conn:
            conn.execute("UPDATE reports SET family_expires_at=? WHERE id=?", (expiry, report["id"]))
        self.assertEqual(family.get(f"/api/family/{token}wrong").status_code, 404)
        self.assertEqual(family.get(f"/api/family/{token}?format=../source").status_code, 422)
        self.assertEqual(self.client.post(f"/api/reports/{report['id']}/release", json=self.revisions(aid)).status_code, 409)
        payload = {**self.get(aid)["inputs"], "expected_input_revision": self.get(aid)["input_revision"]}
        self.assertEqual(self.client.patch(f"/api/assessments/{aid}", json=payload).status_code, 409)
        released = self.client.get(f"/api/reports/{report['id']}").json()
        self.assertEqual(released["approver"], self.settings.reviewer_identity)
        self.assertFalse(released["revoked"])
        self.assertTrue(released["family_expires_at"])
        self.assertEqual(self.client.post(f"/api/reports/{report['id']}/revoke").status_code, 200)
        self.assertTrue(self.client.get(f"/api/reports/{report['id']}").json()["revoked"])
        self.assertEqual(family.get(f"/api/family/{token}").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/assessments/{aid}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/assessments/{aid}").status_code, 404)
        family.close()

    def test_stale_report_and_browser_writes(self):
        aid, findings = self.reviewed()
        report = self.report(aid)
        with patch("step_wise.pipeline.reports.generation_fingerprints", return_value={"template": "changed"}):
            self.assertEqual(self.client.post(f"/api/reports/{report['id']}/release", json=self.revisions(aid)).status_code, 409)
        before = self.revisions(aid)
        hid = findings["hazards"][0]["id"]
        self.assertEqual(self.client.put(f"/api/assessments/{aid}/reviews/{hid}", json={**before, "decision": "reject"}).status_code, 200)
        self.assertEqual(self.client.put(f"/api/assessments/{aid}/reviews/{hid}", json={**before, "decision": "approve"}).status_code, 409)
        self.assertEqual(self.client.post(f"/api/reports/{report['id']}/release", json=self.revisions(aid)).status_code, 409)
        report2 = self.report(aid)
        self.assertNotIn(hid, {h["id"] for h in report2["snapshot"]["accepted"]})

    def test_input_invalidates_results_and_pending_decisions_block_report(self):
        aid = self.ready()
        self.assertEqual(self.client.post(f"/api/assessments/{aid}/reports", json=self.revisions(aid)).status_code, 409)
        a = self.get(aid)
        self.assertEqual(self.client.patch(f"/api/assessments/{aid}", json={**a["inputs"], "label": "Changed", "expected_input_revision": a["input_revision"]}).status_code, 200)
        self.assertEqual(self.get(aid)["status"], "draft")
        self.assertEqual(self.client.get(f"/api/assessments/{aid}/findings").json()["hazards"], [])

    def test_review_change_during_render_discards_report_completion(self):
        from weasyprint import HTML
        aid, findings = self.reviewed()
        jid = self.client.post(f"/api/assessments/{aid}/reports", json=self.revisions(aid)).json()["job_id"]
        original = HTML.write_pdf

        def render_and_edit(document, *args, **kwargs):
            for forbidden in ("https://external.invalid/image.jpg", "file:///etc/passwd"):
                with self.assertRaises(ValueError):
                    document.url_fetcher(forbidden)
            result = original(document, *args, **kwargs)
            response = self.client.put(f"/api/assessments/{aid}/reviews/{findings['hazards'][0]['id']}", json={**self.revisions(aid), "decision": "reject"})
            self.assertEqual(response.status_code, 200)
            return result

        with patch.object(HTML, "write_pdf", render_and_edit):
            worker.run(self.settings, once=True)
        self.assertEqual(self.client.get(f"/api/jobs/{jid}").json()["status"], "failed")
        self.assertEqual(self.get(aid)["status"], "review_ready")
        self.assertEqual(self.get(aid)["reports"], [])

    def test_live_report_sends_reviewed_text_without_thumbnail_base64(self):
        aid, _ = self.reviewed(clinical_external_processing_consent=True)
        settings = replace(self.settings, gemma_backend="http", gemma_revision="mocked-contract-test")
        rev = self.revisions(aid)
        jid = db.enqueue(settings, aid, "report", rev["expected_input_revision"], rev["expected_review_revision"])

        def draft(config, schema, prompt, content, validate, audit):
            self.assertNotIn("data:image", content)
            self.assertNotIn("position_world", content)
            supplied = json.loads(content)
            result = schema.model_validate({"title": "Home action plan", "items": [{"hazard_id": h["id"], **{key: h[key] for key in ("observation", "priority", "suggested_action")}} for h in supplied["accepted"]]})
            validate(result)
            return result, [{"status": "mocked_contract_test"}]

        with patch("step_wise.pipeline.gemma.complete", side_effect=draft) as model:
            worker.run(settings, once=True)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(self.client.get(f"/api/jobs/{jid}").json()["status"], "succeeded")

    def test_upload_queue_and_restart_boundaries_without_login(self):
        aid = self.create()
        self.assertEqual(self.client.post(f"/api/assessments/{aid}/photos", data={"scene_label": "room", "expected_input_revision": 1}, files={"file": ("x.jpg", b"not an image")}).status_code, 422)
        self.assertEqual(self.get(aid)["photos"], [])
        self.client.post(f"/api/assessments/{aid}/photos", data={"scene_label": "room", "expected_input_revision": 1}, files={"file": ("../../secret.png", picture())})
        self.assertEqual(self.client.post(f"/api/assessments/{aid}/analyze", json=self.revisions(aid)).status_code, 202)
        restarted = TestClient(create_app(self.settings))

        self.assertEqual(restarted.get(f"/api/assessments/{aid}").json()["status"], "queued")
        restarted.close()
        self.assertEqual(self.client.post(f"/api/assessments/{aid}/analyze", json=self.revisions(aid)).status_code, 409)
        self.assertEqual(self.client.delete(f"/api/assessments/{aid}").status_code, 409)
        job = worker.claim(self.settings)
        assert job is not None
        with worker.worker_lock(self.settings):
            with self.assertRaises(db.Conflict):
                with worker.worker_lock(self.settings):
                    pass
            worker.recover(self.settings)
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}").json()["status"], "interrupted")
        self.assertEqual(self.get(aid)["status"], "failed")
        fresh_client = TestClient(self.client.app)
        self.assertEqual(fresh_client.get(f"/api/assessments/{aid}").status_code, 200)
        self.assertEqual(self.client.post("/api/assessments", headers={"Origin": "https://attacker.example"}, json={}).status_code, 403)
        self.assertEqual(self.client.get("/api/unknown").status_code, 404)
        fresh_client.close()

    def test_existing_login_sessions_are_removed_without_losing_assessments(self):
        aid = self.create()
        with db.connect(self.settings, write=True) as conn:
            conn.execute("CREATE TABLE sessions(token_hash TEXT PRIMARY KEY, expires_at TEXT NOT NULL)")
            conn.execute("INSERT INTO sessions VALUES('old-token-hash', '2099-01-01T00:00:00+00:00')")
        db.init(self.settings)
        with db.connect(self.settings) as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone())
            self.assertEqual(db.assessment(conn, aid)["id"], aid)

    def test_new_photo_invalidates_external_photo_consent_until_reacknowledged(self):
        created = self.client.post("/api/assessments", json={
            "label": "Consenso fittizio", "profile": {"description": "Profilo sintetico"},
            "profile_confirmed": True, "staged_data_confirmed": True, "processing_consent": True,
            "external_processing_consent": True,
        })
        aid = created.json()["id"]
        uploaded = self.client.post(f"/api/assessments/{aid}/photos", data={
            "scene_label": "Sala", "expected_input_revision": 1,
        }, files={"file": ("room.png", picture(), "image/png")})
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        assessment = self.get(aid)
        self.assertFalse(assessment["inputs"]["external_processing_consent"])
        live = replace(self.settings, gemma_backend="http")
        with self.assertRaisesRegex(db.Conflict, "external photo processing"):
            db.enqueue(live, aid, "analyze", assessment["input_revision"], assessment["review_revision"])

        consent = {**assessment["inputs"], "expected_input_revision": assessment["input_revision"],
                   "external_processing_consent": True}
        self.assertEqual(self.client.patch(f"/api/assessments/{aid}", json=consent).status_code, 200)
        current = self.get(aid)
        with self.assertRaisesRegex(db.Conflict, "clinical profile"):
            db.enqueue(live, aid, "analyze", current["input_revision"], current["review_revision"])
        consent = {**current["inputs"], "expected_input_revision": current["input_revision"],
                   "clinical_external_processing_consent": True}
        self.assertEqual(self.client.patch(f"/api/assessments/{aid}", json=consent).status_code, 200)
        current = self.get(aid)
        job_id = db.enqueue(live, aid, "analyze", current["input_revision"], current["review_revision"])
        self.assertTrue(job_id)

    def test_static_frontend_export_pages_assets_and_fallback(self):
        out = Path(self.temporary.name) / "out"
        (out / "pazienti").mkdir(parents=True)
        (out / "_next").mkdir()
        for name, text in {"index.html": "home", "pazienti.html": "<script>boot()</script><script src=\"/_next/app.js\"></script>space", "pazienti/nuovo.html": "intake", "404.html": "missing", "_next/app.js": "js"}.items():
            (out / name).write_text(text)
        (Path(self.temporary.name) / "secret.txt").write_text("private")
        with TestClient(create_app(replace(self.settings, web_dist=out))) as client:
            self.assertEqual(client.get("/").text, "home")
            page = client.get("/pazienti?id=abc")
            self.assertTrue(page.text.endswith("space"))
            # Only this page's inline bootstrap is admitted, by hash; API responses keep script-src 'self' plus WebAssembly for the splat viewer.
            inline = base64.b64encode(hashlib.sha256(b"boot()").digest()).decode()
            self.assertIn(f"script-src 'self' 'wasm-unsafe-eval' 'sha256-{inline}';", page.headers["content-security-policy"])
            self.assertNotIn("unsafe-inline';", page.headers["content-security-policy"].split("script-src")[1].split(";")[0])
            self.assertIn("script-src 'self' 'wasm-unsafe-eval';", client.get("/api/health").headers["content-security-policy"])
            self.assertEqual(client.get("/pazienti/nuovo").text, "intake")
            self.assertEqual(client.head("/pazienti/nuovo").status_code, 200)
            self.assertEqual(client.get("/_next/app.js").text, "js")
            missing = client.get("/unknown-page")
            self.assertEqual((missing.status_code, missing.text), (404, "missing"))
            self.assertEqual(client.get("/missing.js").status_code, 404)
            self.assertEqual(client.get("/..%2Fsecret.txt").status_code, 404)
            self.assertEqual(client.get("/api/unknown").status_code, 404)

    def test_configured_dev_origin_may_mutate_but_others_may_not(self):
        settings = replace(self.settings, allowed_origins=("http://127.0.0.1:3010",))
        with TestClient(create_app(settings)) as client:
            payload = {"label": "Origin test", "profile": {"description": "Fixture"}, "profile_confirmed": True,
                       "staged_data_confirmed": True, "processing_consent": True}
            self.assertEqual(client.post("/api/assessments", json=payload, headers={"Origin": "http://127.0.0.1:3010"}).status_code, 201)
            self.assertEqual(client.post("/api/assessments", json=payload, headers={"Origin": "http://127.0.0.1:3011"}).status_code, 403)
            self.assertEqual(client.post("/api/assessments", json=payload, headers={"Origin": "http://127.0.0.1:3010", "Sec-Fetch-Site": "cross-site"}).status_code, 403)

    def test_backend_change_cannot_turn_a_queued_fixture_into_live_calls(self):
        aid = self.create()
        self.client.post(f"/api/assessments/{aid}/photos", data={"scene_label": "room", "expected_input_revision": 1}, files={"file": ("control.png", picture())})
        jid = self.client.post(f"/api/assessments/{aid}/analyze", json=self.revisions(aid)).json()["job_id"]
        with patch("step_wise.pipeline.http.requests.post") as post:
            worker.run(replace(self.settings, gemma_backend="http"), once=True)
            post.assert_not_called()
        self.assertEqual(self.client.get(f"/api/jobs/{jid}").json()["status"], "failed")
        self.assertEqual(self.get(aid)["status"], "failed")

    def test_failed_upload_rolls_back_rows_and_partial_file(self):
        aid = self.create()
        content = picture()
        with self.assertRaises(db.Missing):
            ingest(self.settings, "../unknown-assessment", content, "room", "overview", 1)
        self.assertFalse((self.settings.data / "unknown-assessment").exists())
        with patch.object(Image.Image, "save", side_effect=OSError("simulated disk failure")):
            with self.assertRaises(OSError):
                ingest(self.settings, aid, content, "room", "overview", 1)
        self.assertEqual(self.get(aid)["photos"], [])
        self.assertEqual(self.get(aid)["input_revision"], 1)
        with db.connect(self.settings) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM assets").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM scenes").fetchone()[0], 0)

    def test_cli_reset_is_limited_to_the_selected_seeded_dataset(self):
        from step_wise.cli import main
        from step_wise.demo import seed_case
        normal = self.create()
        seeded, _ = seed_case(self.settings, ROOT / "demo/cases/room-001.json")
        with patch("step_wise.cli.Settings.from_env", return_value=self.settings), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["reset", "--assessment", normal]), 3)
            self.assertEqual(main(["reset", "--assessment", seeded]), 0)
        self.assertEqual(self.client.get(f"/api/assessments/{normal}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/assessments/{seeded}").status_code, 404)

class Contracts(unittest.TestCase):
    def test_guidance_ids_and_source_integrity(self):
        source = json.loads((ROOT / "guidance/checklist.json").read_text())
        parsed = GuidanceChecklist.model_validate(source)
        self.assertTrue(all(entry.action and entry.question for entry in parsed.entries))
        source["entries"].append(source["entries"][0])
        with self.assertRaises(ValueError):
            GuidanceChecklist.model_validate(source)

    def test_finite_boxes_and_presence_gate(self):
        for box in ((10, 0, 2, 1), (0, 0, float("nan"), 10)):
            with self.assertRaises(ValueError):
                Evidence(photo_id="p", box=box)
        c = Candidate(photo_id="p", category="rug", question_id="q", presence="absent", coverage="visible", evidence=Evidence(photo_id="p", box=(0, 0, 20, 20)), attachment="loose", producer="fixture", provenance="fixture")
        self.assertIsNone(c.evidence)
        self.assertEqual(c.attachment, "unknown")
        self.assertIsNone(Profile(description="Fictional").mobility_aid)

    def test_photo_padding_roundtrip_and_metadata(self):
        image = Image.new("RGB", (1800, 100))
        prepared, transform = prepared_image(image)
        expected = [100, 10, 1000, 80]
        sx, sy = transform["scale_xy"]
        ox, oy = transform["offset_xy"]
        normalized = [(expected[0] * sx + ox) / prepared.width, (expected[1] * sy + oy) / prepared.height, (expected[2] * sx + ox) / prepared.width, (expected[3] * sy + oy) / prepared.height]
        recovered = canonical_box(normalized, transform, image.size)
        assert recovered is not None
        self.assertTrue(all(abs(a - b) < 1e-6 for a, b in zip(expected, recovered)))
        self.assertIsNone(canonical_box([0, 0, .5, .1], transform, image.size))
        source = Image.new("RGB", (300, 600))
        exif = source.getexif()
        exif[274] = 6
        stream = io.BytesIO()
        source.save(stream, "JPEG", exif=exif)
        clean, meta = decode(stream.getvalue())
        self.assertEqual(clean.size, (600, 300))
        self.assertEqual(dict(clean.getexif()), {})
        with self.assertRaises(ValueError):
            decode(picture(), max_pixels=10)

    def test_report_rejects_invented_ids_and_meaning(self):
        accepted = [{"id": "accepted", "observation": "Tappeto", "suggested_action": "Verificare", "priority": "important"}]
        item = {"hazard_id": "invented", "observation": "Tappeto", "suggested_action": "Verificare", "priority": "important"}
        with self.assertRaises(ValueError):
            validate_draft({"title": "Home action plan", "items": [item]}, accepted)
        item.update(hazard_id="accepted", suggested_action="Costa 200 euro")
        with self.assertRaises(ValueError):
            validate_draft({"title": "Home action plan", "items": [item]}, accepted)

if __name__ == "__main__":
    unittest.main()
