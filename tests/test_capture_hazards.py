"""The committed demo findings must trace to recorded live Jev responses.

Every finding in frontend/public/capture-data/hazards.json has to reproduce,
field by field, from the raw response kept in the repository under
pitch/jev-results/2026-10-17/room-capture/. This is what makes the demo case's
provenance checkable instead of claimed.
"""
import json
import math
import unittest
from pathlib import Path

from step_wise.pipeline.photos import canonical_box

ROOT = Path(__file__).resolve().parents[1]
RECORDS = ROOT / "pitch/jev-results/2026-10-17/room-capture"
FINDING_TARGETS = {"carpet": ("rug_near", "rug_far"), "cord": ("floor_cable",)}
FINDING_GUIDANCE = {"carpet": ("floor-rug",), "cord": ("floor-cable",)}


class CaptureHazardsProvenanceTests(unittest.TestCase):
    def test_findings_trace_to_recorded_live_runs(self):
        findings = json.loads((ROOT / "frontend/public/capture-data/hazards.json").read_text())
        self.assertEqual({finding["id"] for finding in findings}, set(FINDING_TARGETS))
        for finding in findings:
            self.assertEqual(finding["provenance"], "live")
            self.assertTrue(finding["producer"].startswith("jev-"))
            record = json.loads((ROOT / finding["detection_record"]).read_text())
            self.assertEqual(record["status"], "succeeded")
            self.assertEqual(record["response"]["model"], finding["producer"])
            self.assertTrue(all(len(record[key]) == 64 for key in ("source_sha256", "prepared_sha256")))
            targets = FINDING_TARGETS[finding["id"]]
            flagged = [c for c in record["candidates"] if c["question_id"] in targets and c["presence"] == "present"]
            self.assertTrue(flagged, f"{finding['id']}: the recorded run flagged nothing")
            primary = max((c for c in flagged if c["photo_id"] == finding["photo_id"]),
                          key=lambda c: c["provider_response"]["box"]["confidence"])
            self.assertAlmostEqual(finding["presence"], primary["provider_response"]["presence"]["noul"])
            self.assertAlmostEqual(finding["box_confidence"], primary["provider_response"]["box"]["confidence"])
            self.assertGreaterEqual(finding["presence"], 0.75)  # production presence gate
            width, height = record["upright_size"]
            if primary.get("evidence"):
                self.assertEqual(finding["localization"], "gate_passed")
                self.assertGreaterEqual(finding["box_confidence"], 0.5)  # production box gate
                box = [v / s for v, s in zip(primary["evidence"]["box"], (width, height, width, height))]
            else:
                self.assertEqual(finding["localization"], "below_box_gate")
                raw = primary["provider_response"]["box"]["box"]
                mapped = canonical_box([raw[k] for k in ("left", "top", "right", "bottom")], record["transform"], record["upright_size"])
                box = [v / s for v, s in zip(mapped, (width, height, width, height))]
            self.assertAlmostEqual(finding["box"][0], box[0], places=3)
            self.assertAlmostEqual(finding["box"][3], box[3], places=3)
            self.assertTrue(all(0 <= v <= 1 for v in finding["box"]))
            self.assertTrue(all(math.isfinite(v) for v in finding["position_world"]))
            self.assertTrue(finding["uncertainty"].strip())
            # The suggested action must be the cited guidance entry verbatim, as the product enforces.
            guidance = json.loads((ROOT / "guidance/checklist.json").read_text())["entries"]
            self.assertIn(finding["action"], [g["action"] for g in guidance if g["id"] in FINDING_GUIDANCE[finding["id"]]])

    def test_recorded_runs_cover_every_case_photo(self):
        photos = sorted(p.name for p in (ROOT / "demo/capture/images").glob("*.jpg"))
        records = sorted(p.stem for p in RECORDS.glob("IMG_*.json"))
        self.assertEqual([Path(name.split("-", 1)[1]).stem for name in photos], records)
        summary = json.loads((RECORDS / "summary.json").read_text())
        self.assertEqual(len(summary["runs"]), len(photos))
        self.assertEqual(summary["model"], "jev-1.14.0")


if __name__ == "__main__":
    unittest.main()
