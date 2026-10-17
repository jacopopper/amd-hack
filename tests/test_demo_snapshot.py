import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from step_wise import snapshot, worker
from step_wise.api import create_app
from step_wise.config import Settings


@unittest.skipUnless((snapshot.SNAPSHOT / "case.json").is_file(), "no demo snapshot exported")
class DemoSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.settings = Settings(data=Path(self.temporary.name), demo=True)
        self.client = TestClient(create_app(self.settings))

    def tearDown(self):
        self.temporary.cleanup()

    def test_visitor_reviews_private_copy_through_release(self):
        first = self.client.post("/api/demo/session").json()["id"]
        second = self.client.post("/api/demo/session").json()["id"]
        self.assertNotEqual(first, second)
        self.assertEqual(self.client.get("/api/assessments").json(), [])
        case = self.client.get(f"/api/assessments/{first}").json()
        self.assertEqual(case["status"], "review_ready")
        self.assertEqual(self.client.get(case["photos"][0]["asset_url"]).status_code, 200)
        hazards = self.client.get(f"/api/assessments/{first}/findings").json()["hazards"]
        self.assertTrue(hazards and all(h["review"] is None for h in hazards))
        decisions = ["approve", "edit", "reject", "defer"]
        for index, hazard in enumerate(hazards):
            decision = decisions[index % 4]
            body = {"expected_input_revision": case["input_revision"], "expected_review_revision": index, "decision": decision, "note": "foto del bagno" if decision == "defer" else ""}
            if decision == "edit":
                body |= {"suggested_action": "Far valutare da un tecnico", "priority": "urgent", "observation": hazard["observation"]}
            self.assertEqual(self.client.put(f"/api/assessments/{first}/reviews/{hazard['id']}", json=body).status_code, 200)
        # The other visitor's copy is untouched.
        self.assertTrue(all(h["review"] is None for h in self.client.get(f"/api/assessments/{second}/findings").json()["hazards"]))
        revisions = {"expected_input_revision": case["input_revision"], "expected_review_revision": len(hazards)}
        self.assertEqual(self.client.post(f"/api/assessments/{first}/reports", json=revisions).status_code, 202)
        worker.run(self.settings, once=True)
        case = self.client.get(f"/api/assessments/{first}").json()
        self.assertEqual(case["status"], "report_ready")
        report = self.client.get(f"/api/reports/{case['reports'][0]['id']}").json()
        self.assertIn("Far valutare da un tecnico", self.client.get(report["html_url"]).text)
        token = self.client.post(f"/api/reports/{report['id']}/release", json=revisions).json()["token"]
        self.assertEqual(self.client.get(f"/api/family/{token}?format=pdf").status_code, 200)

    def test_demo_blocks_everything_but_review(self):
        aid = self.client.post("/api/demo/session").json()["id"]
        self.assertEqual(self.client.delete(f"/api/assessments/{aid}").status_code, 403)
        self.assertEqual(self.client.post("/api/assessments", json={}).status_code, 403)
        self.assertEqual(self.client.post(f"/api/assessments/{aid}/analyze", json={}).status_code, 403)
        self.assertTrue(self.client.get("/api/health").json()["demo"])


if __name__ == "__main__":
    unittest.main()
