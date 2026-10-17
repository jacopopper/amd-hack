import json
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch

from fastapi.testclient import TestClient

from step_wise import cases, db, worker
from step_wise.api import create_app
from step_wise.config import ROOT, Settings
from step_wise.demo import generate_controls, load_case, seed_case


class Cases(unittest.TestCase):
    def test_queued_provider_change_is_rejected_before_network_and_invalidates_only_model_cache(self):
        generate_controls()
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(data=Path(directory), gemma_backend="http", gemma_provider="provider-a")
            db.init(settings)
            case = json.loads((ROOT / "demo/cases/room-001.json").read_text())
            case["consents"] = {"external_processing_consent": True, "clinical_external_processing_consent": True}
            path = Path(directory) / "case.json"
            path.write_text(json.dumps(case))
            aid, _ = seed_case(settings, path)
            with db.connect(settings) as conn:
                row = db.assessment(conn, aid)
            db.enqueue(settings, aid, "analyze", row["input_revision"], row["review_revision"])
            job = worker.claim(settings)
            original = worker.load_context(settings, job)
            changed = worker.load_context(replace(settings, gemma_provider="provider-b"), job)
            worker.execute_stage("prepare", changed, {})  # local work is independent of the provider
            self.assertEqual(worker.fingerprint("reconstruct", original, {}), worker.fingerprint("reconstruct", changed, {}))
            for stage in ("gemma", "report"):
                self.assertNotEqual(worker.fingerprint(stage, original, {}), worker.fingerprint(stage, changed, {}))
            with patch("step_wise.pipeline.gemma.post_json") as post:
                with self.assertRaisesRegex(RuntimeError, "enqueued request"):
                    worker.execute_stage("gemma", changed, {})
                post.assert_not_called()
            endpoint = worker.load_context(replace(settings, gemma_base_url="https://other.invalid/v1?secret=synthetic"), job)
            self.assertNotEqual(worker.fingerprint("gemma", original, {}), worker.fingerprint("gemma", endpoint, {}))
            self.assertNotIn("synthetic", json.dumps(endpoint["settings"].gemma_identity()))

    def test_local_stage_does_not_request_external_consent_or_expand_queued_scope(self):
        generate_controls()
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(data=Path(directory))
            path = ROOT / "demo/cases/room-001.json"
            case, dataset, photos, profile = load_case(path)
            # Only the data-kind gate is replaced: no remote inference or GPU is exercised.
            with patch("step_wise.cases.load_case", return_value=(case, {**dataset, "data_kind": "single_image_experiment"}, photos, profile)):
                record, code = cases.run_case(settings, path, "live", "prepare")
            self.assertEqual((record["status"], code), ("partial", 0))
            local = cases.run_settings(settings, cases.find_run(settings, record["id"]))
            local = replace(local, recon_backend="vggt", gemma_backend="http")
            with db.connect(local) as conn:
                job = dict(conn.execute("SELECT * FROM jobs WHERE id=?", (record["job_id"],)).fetchone())
            self.assertNotIn("gemma", json.loads(job["output"])["requested_stages"])
            context = worker.load_context(local, job)
            with self.assertRaisesRegex(RuntimeError, "queued scope"):
                worker.execute_stage("jev", context, {})

    def test_case_consents_cover_the_complete_uploaded_collection(self):
        generate_controls()
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(data=Path(directory))
            db.init(settings)
            case = json.loads((ROOT / "demo/cases/room-001.json").read_text())
            case["consents"] = {"external_processing_consent": True, "clinical_external_processing_consent": True}
            path = Path(directory) / "case.json"
            path.write_text(json.dumps(case))
            aid, mapping = seed_case(settings, path)
            with db.connect(settings) as conn:
                row = db.assessment(conn, aid)
                inputs = json.loads(row["input_json"])
            self.assertEqual(len(mapping), 2)
            self.assertTrue(inputs["external_processing_consent"])
            self.assertTrue(inputs["clinical_external_processing_consent"])
            self.assertTrue(db.enqueue(replace(settings, gemma_backend="http"), aid, "analyze",
                                       row["input_revision"], row["review_revision"]))

    def test_report_snapshot_rerun_and_frozen_baseline(self):
        generate_controls()
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(data=Path(directory), reviewer_password="fixture-testing-password")
            path = ROOT / "demo/cases/room-001.json"
            first, _ = cases.run_case(settings, path, "fixture")
            local = cases.run_settings(settings, cases.find_run(settings, first["id"]))
            with worker.worker_lock(settings):
                with self.assertRaises(ValueError):
                    with worker.worker_lock(local):
                        pass
            aid = first["assessment_id"]
            with TestClient(create_app(local)) as client:
                client.auth = ("reviewer", settings.reviewer_password)
                findings = client.get(f"/api/assessments/{aid}/findings").json()
                for h in findings["hazards"]:
                    a = client.get(f"/api/assessments/{aid}").json()
                    response = client.put(f"/api/assessments/{aid}/reviews/{h['id']}", json={"expected_input_revision": a["input_revision"], "expected_review_revision": a["review_revision"], "decision": "approve"})
                    self.assertEqual(response.status_code, 200)
            reported, code = cases.run_case(settings, path, "fixture", "report", first["id"])
            self.assertEqual(reported["status"], "report_ready", reported)
            self.assertEqual(reported["stages"]["report"]["status"], "succeeded")
            local = cases.run_settings(settings, cases.find_run(settings, reported["id"]))
            with TestClient(create_app(local)) as client:
                client.auth = ("reviewer", settings.reviewer_password)
                a = client.get(f"/api/assessments/{aid}").json()
                response = client.post(f"/api/reports/{a['reports'][0]['id']}/release", json={"expected_input_revision": a["input_revision"], "expected_review_revision": a["review_revision"]})
                self.assertEqual(response.status_code, 200, response.text)
            for name in reported["case"]["manual_checks"]:
                cases.attest(settings, reported["id"], name, "Simulated manual record for this isolated automated lifecycle test only")
            baseline = cases.freeze(settings, reported["id"])
            self.assertEqual(baseline["mode"], "fixture")
            self.assertEqual(cases.check_run(local.data)[1], 0)
            with TestClient(create_app(local)) as client:
                client.auth = ("reviewer", settings.reviewer_password)
                self.assertEqual(client.delete(f"/api/assessments/{aid}").status_code, 409)
            self.assertEqual(cases.check_run(local.data)[1], 0)
            (local.data / "index.html").write_text("tampered")
            self.assertEqual(cases.check_run(local.data)[1], 2)

    def test_fixture_repeat_targeted_reuse_and_corruption(self):
        generate_controls()
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(data=Path(directory))
            path = ROOT / "demo/cases/room-001.json"
            first, code = cases.run_case(settings, path, "fixture")
            self.assertEqual(first["status"], "review_ready")
            self.assertEqual(code, 4)  # real manual inspection remains pending
            second, code = cases.run_case(settings, path, "fixture")
            comparison = cases.compare(settings, first["id"], second["id"])
            self.assertEqual(comparison["added"], [])
            self.assertEqual(comparison["removed"], [])
            targeted, code = cases.run_case(settings, path, "fixture", "gemma", first["id"])
            self.assertEqual(targeted["status"], "review_ready", targeted)
            self.assertEqual(targeted["stages"]["prepare"]["origin_run"], first["id"])
            partial, code = cases.run_case(settings, path, "fixture", "reconstruct", targeted["id"])
            self.assertEqual(partial["status"], "review_ready")
            self.assertEqual(partial["stages"]["gemma"]["status"], "succeeded")
            self.assertEqual(partial["stages"]["geometry"]["status"], "succeeded")
            self.assertEqual(partial["stages"]["jev"]["origin_run"], first["id"])
            rebuilt, code = cases.run_case(settings, path, "fixture", reuse_id=partial["id"])
            self.assertEqual(rebuilt["status"], "review_ready", rebuilt)
            jev_rerun, _ = cases.run_case(settings, path, "fixture", "jev", rebuilt["id"])
            self.assertEqual(jev_rerun["stages"]["reconstruct"]["status"], "succeeded")
            self.assertIn("origin_run", jev_rerun["stages"]["reconstruct"])
            first_dir = cases.find_run(settings, first["id"])
            output = first_dir / "stages/prepare/output.json"
            output.write_text("{}")
            corrupted, code = cases.run_case(settings, path, "fixture", "gemma", first["id"])
            self.assertEqual(code, 1)
            self.assertIn("checksum", corrupted["error"])
            live, code = cases.run_case(settings, path, "live", reuse_id=first["id"])
            self.assertEqual(code, 3)
            self.assertEqual(live["status"], "blocked")

    def test_stage_scoped_case_check_only_requires_its_prerequisites(self):
        generate_controls()
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(data=Path(directory))
            path = ROOT / "demo/cases/room-001.json"
            prepared, code = cases.run_case(settings, path, "fixture", "prepare")
            self.assertEqual(prepared["status"], "partial")
            self.assertEqual(code, 0)
            prepare_checks = cases.read(cases.find_run(settings, prepared["id"]) / "checks.json")
            self.assertEqual(next(c["status"] for c in prepare_checks if c["id"] == "stage-prepare"), "pass")
            self.assertTrue(all(c["status"] == "not_applicable" for c in prepare_checks if c["id"].startswith("stage-") and c["id"] != "stage-prepare"))

            reconstructed, code = cases.run_case(settings, path, "fixture", "reconstruct", prepared["id"])
            self.assertEqual(reconstructed["status"], "partial")
            self.assertEqual(code, 0, cases.read(cases.find_run(settings, reconstructed["id"]) / "checks.json"))
            checks = cases.read(cases.find_run(settings, reconstructed["id"]) / "checks.json")
            self.assertEqual(next(c["status"] for c in checks if c["id"] == "stage-prepare"), "pass")
            self.assertEqual(next(c["status"] for c in checks if c["id"] == "stage-reconstruct"), "pass")

    def test_wrong_expectation_is_visible(self):
        generate_controls()
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(data=Path(directory))
            path = Path(directory) / "failing-case.json"
            case = json.loads((ROOT / "demo/cases/room-001.json").read_text())
            case["expectations"][0]["presence"] = "absent"
            path.write_text(json.dumps(case))
            run, code = cases.run_case(settings, path, "fixture")
            self.assertEqual(code, 2)
            checks = cases.read(cases.find_run(settings, run["id"]) / "checks.json")
            failure = next(c for c in checks if c["id"] == "rug-presence")
            self.assertEqual(failure["status"], "fail")
            self.assertTrue(failure["observed"])
