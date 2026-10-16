import json
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace

from fastapi.testclient import TestClient

from safe_home import cases, worker
from safe_home.api import create_app
from safe_home.config import ROOT, Settings
from safe_home.demo import generate_controls


class Cases(unittest.TestCase):
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
            self.assertEqual(partial["status"], "partial")
            self.assertEqual(partial["stages"]["gemma"]["status"], "stale")
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
