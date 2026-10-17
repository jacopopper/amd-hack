import io
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from step_wise.api import create_app
from step_wise import db, worker
from step_wise.config import Settings

def picture():
    image = Image.new("RGB", (32, 24), "#c1ad8d")
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()

class BundleTests(unittest.TestCase):
    def calibrate_fixture_scenes(self, settings, aid, rooms, scales):
        """Install known synthetic calibration metadata; this is not live geometry evidence."""
        with db.connect(settings, write=True) as conn:
            for room, scale in zip(rooms, scales, strict=True):
                payload = {"status": "ready", "provenance": "live", "reconstruction_kind": "multiview",
                           "raw_coordinate_units": "relative", "scale_state": "metric_calibrated", "scale_factor": scale,
                           "selected_photo_ids": [p["id"] for p in conn.execute(
                               "SELECT id FROM photos WHERE scene_id=? AND active=1", (room["id"],))],
                           "calibration": {"measurement_id": f"synthetic-{room['id']}", "scale_factor_m_per_unit": scale}}
                conn.execute("UPDATE scenes SET payload=? WHERE id=?", (db.dumps(payload), room["id"]))

    def make_two_rooms(self, client):
        created = client.post("/api/assessments", json={
            "label": "Casa fittizia", "profile": {"description": "Profilo sintetico"},
            "profile_confirmed": True, "staged_data_confirmed": True, "processing_consent": True,
        })
        self.assertEqual(created.status_code, 201, created.text)
        aid = created.json()["id"]
        photo_ids = []
        for revision, room in ((1, "Cucina"), (2, "Camera")):
            result = client.post(f"/api/assessments/{aid}/photos", data={
                "scene_label": room, "role": "overview", "expected_input_revision": revision,
            }, files={"file": ("view.png", picture(), "image/png")})
            self.assertEqual(result.status_code, 201, result.text)
            photo_ids.append(result.json()["id"])
        rooms = client.get(f"/api/assessments/{aid}/scenes").json()
        return aid, photo_ids, rooms

    def test_unregistered_rooms_remain_separate_in_house_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(data=Path(temporary))
            with TestClient(create_app(settings)) as client:

                aid, _, rooms = self.make_two_rooms(client)

                bundle = client.get(f"/api/assessments/{aid}/bundle")
                self.assertEqual(bundle.status_code, 200, bundle.text)
                value = bundle.json()
                self.assertEqual(value["schema_version"], 1)
                self.assertEqual(value["house"]["status"], "partial")
                self.assertEqual(value["house"]["registered_scene_ids"], [])
                self.assertEqual(len(value["house"]["unregistered_scene_ids"]), 2)
                self.assertEqual({room["label"] for room in value["rooms"]}, {"Cucina", "Camera"})
                self.assertTrue(all(room["floor_state"] == "unknown" and room["gravity_state"] == "unknown"
                                    for room in value["rooms"]))

    def test_reviewer_link_connects_room_transforms_and_delete_restores_partial_house(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(data=Path(temporary))
            with TestClient(create_app(settings)) as client:

                aid, photo_ids, rooms = self.make_two_rooms(client)
                from_scene, to_scene = (room["id"] for room in rooms)
                self.calibrate_fixture_scenes(settings, aid, rooms, (1, 2))
                payload = {
                    "expected_input_revision": 3, "expected_review_revision": 0,
                    "coordinate_space": "metric_scene_coordinates",
                    "transform_from_to": [[1, 0, 0, 1.2], [0, 1, 0, 0], [0, 0, 1, -0.4], [0, 0, 0, 1]],
                    "overlap_photo_pairs": [[photo_ids[0], photo_ids[1]]], "residual": 0.01,
                    "residual_unit": "m", "uncertainty": "Synthetic room-to-room transform in calibrated metric coordinates.",
                }
                linked = client.put(f"/api/assessments/{aid}/scene-links/{from_scene}/{to_scene}", json=payload)
                self.assertEqual(linked.status_code, 200, linked.text)
                self.assertEqual(linked.json()["review_revision"], 1)
                value = client.get(f"/api/assessments/{aid}/bundle").json()
                self.assertEqual(value["house"]["status"], "connected")
                self.assertEqual(value["house"]["links"][0]["transform_from_to"][0][3], 1.2)
                self.assertEqual(set(value["house"]["registered_scene_ids"]), {from_scene, to_scene})
                self.assertEqual({room["alignment_state"] for room in value["rooms"]}, {"reviewer_linked"})
                self.assertIn("remain separate assets", value["house"]["composition"])
                placements = {p["scene_id"]: p for p in value["house"]["placements"]}
                self.assertEqual(placements[from_scene]["transform_to_reference"][0], [1, 0, 0, 0])
                self.assertEqual(placements[to_scene]["transform_to_reference"][0], [2, 0, 0, -1.2])
                self.assertEqual({p["coordinate_units"] for p in placements.values()}, {"m"})

                bad = {**payload, "transform_from_to": [[2, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]}
                self.assertEqual(client.put(f"/api/assessments/{aid}/scene-links/{from_scene}/{to_scene}", json=bad).status_code, 422)
                removed = client.delete(f"/api/assessments/{aid}/scene-links/{from_scene}/{to_scene}", params={
                    "expected_input_revision": 3, "expected_review_revision": 1,
                })
                self.assertEqual(removed.status_code, 200, removed.text)
                value = client.get(f"/api/assessments/{aid}/bundle").json()
                self.assertEqual(value["house"]["status"], "partial")
                self.assertEqual(value["house"]["links"], [])

    def test_bundle_preserves_producer_coverage_and_latest_reviewer_correction(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(data=Path(temporary))
            with TestClient(create_app(settings)) as client:

                aid, _, _ = self.make_two_rooms(client)
                queued = client.post(f"/api/assessments/{aid}/analyze", json={
                    "expected_input_revision": 3, "expected_review_revision": 0})
                self.assertEqual(queued.status_code, 202, queued.text)
                worker.process_job(settings, worker.claim(settings))
                initial = client.get(f"/api/assessments/{aid}/bundle").json()
                hazard = initial["hazards"][0]
                self.assertEqual(hazard["provenance"], "fixture")
                self.assertEqual(hazard["producer"], "synthetic-contract-fixture-v1")
                self.assertIsNone(hazard["review"])
                self.assertTrue(initial["coverage"])
                self.assertTrue(initial["follow_up_requests"])
                corrected = "Osservazione corretta dal revisore nella prova sintetica."
                review = client.put(f"/api/assessments/{aid}/reviews/{hazard['id']}", json={
                    "expected_input_revision": 3, "expected_review_revision": 0,
                    "decision": "edit", "observation": corrected})
                self.assertEqual(review.status_code, 200, review.text)
                current = client.get(f"/api/assessments/{aid}/bundle").json()
                updated = next(h for h in current["hazards"] if h["id"] == hazard["id"])
                self.assertEqual(current["review_revision"], 1)
                self.assertEqual(updated["review"]["decision"], "edit")
                self.assertEqual(updated["review"]["effective"]["observation"], corrected)
                self.assertEqual(updated["observation"], hazard["observation"])

    def test_room_links_require_both_calibrations_and_explicit_metric_coordinates(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(data=Path(temporary))
            with TestClient(create_app(settings)) as client:

                aid, photo_ids, rooms = self.make_two_rooms(client)
                path = f"/api/assessments/{aid}/scene-links/{rooms[0]['id']}/{rooms[1]['id']}"
                payload = {"expected_input_revision": 3, "expected_review_revision": 0,
                           "coordinate_space": "metric_scene_coordinates",
                           "transform_from_to": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
                           "overlap_photo_pairs": [photo_ids], "residual_unit": "m",
                           "uncertainty": "Known synthetic alignment"}
                self.assertEqual(client.put(path, json=payload).status_code, 409)
                self.calibrate_fixture_scenes(settings, aid, rooms[:1], (1,))
                self.assertEqual(client.put(path, json=payload).status_code, 409)
                self.calibrate_fixture_scenes(settings, aid, rooms[1:], (2,))
                legacy = {key: value for key, value in payload.items() if key != "coordinate_space"}
                self.assertEqual(client.put(path, json=legacy).status_code, 422)
                self.assertEqual(client.put(path, json={**payload, "residual_unit": "relative"}).status_code, 422)
                self.assertEqual(client.get(f"/api/assessments/{aid}").json()["review_revision"], 0)
                self.assertEqual(client.get(f"/api/assessments/{aid}/scene-links").json(), [])

    def test_legacy_links_need_review_and_changed_or_removed_calibrations_disconnect_rooms(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(data=Path(temporary))
            with TestClient(create_app(settings)) as client:

                aid, photo_ids, rooms = self.make_two_rooms(client)
                self.calibrate_fixture_scenes(settings, aid, rooms, (1, 2))
                a, b = (room["id"] for room in rooms)
                value = {"transform_from_to": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
                         "overlap_photo_pairs": [photo_ids], "residual_unit": "relative", "uncertainty": "Legacy synthetic link"}
                with db.connect(settings, write=True) as conn:
                    conn.execute("INSERT INTO scene_links VALUES(?,?,?,?,?,?)",
                                 (db.uid(), aid, a, b, db.dumps(value), db.now()))
                bundle_path = f"/api/assessments/{aid}/bundle"
                bundle = client.get(bundle_path).json()
                self.assertEqual(bundle["house"]["status"], "partial")
                self.assertEqual(bundle["house"]["links"][0]["state"], "requires_metric_review")
                link_path = f"/api/assessments/{aid}/scene-links/{a}/{b}"
                payload = {**value, "coordinate_space": "metric_scene_coordinates", "residual_unit": "m",
                           "expected_input_revision": 3, "expected_review_revision": 0}
                self.assertEqual(client.put(link_path, json=payload).status_code, 200)
                self.assertEqual(client.get(bundle_path).json()["house"]["status"], "connected")
                calibration = {
                    "expected_input_revision": 3, "expected_review_revision": 1,
                    "fit_segments": [
                        {"label": "fit-a", "point_a": [0, 0, 0], "point_b": [1, 0, 0], "actual_distance_m": 3,
                         "source_photo_ids": [photo_ids[1]]},
                        {"label": "fit-b", "point_a": [0, 0, 1], "point_b": [2, 0, 1], "actual_distance_m": 6,
                         "source_photo_ids": [photo_ids[1]]}],
                    "holdout_segments": [
                        {"label": "holdout", "point_a": [0, 1, 0], "point_b": [0, 1, 0.5], "actual_distance_m": 1.5,
                         "source_photo_ids": [photo_ids[1]]}]}
                calibrated = client.put(f"/api/assessments/{aid}/scenes/{b}/calibration", json=calibration)
                self.assertEqual(calibrated.status_code, 200, calibrated.text)
                bundle = client.get(bundle_path).json()
                self.assertEqual(bundle["house"]["status"], "partial")
                self.assertEqual(bundle["house"]["links"][0]["state"], "calibration_changed")
                payload["expected_review_revision"] = 2
                self.assertEqual(client.put(link_path, json=payload).status_code, 200)
                bundle = client.get(bundle_path).json()
                self.assertEqual(bundle["house"]["status"], "connected")
                placement = next(p for p in bundle["house"]["placements"] if p["scene_id"] == b)
                self.assertEqual(placement["transform_to_reference"][0][0], 3)
                removed = client.delete(f"/api/assessments/{aid}/scenes/{b}/calibration", params={
                    "expected_input_revision": 3, "expected_review_revision": 3})
                self.assertEqual(removed.status_code, 200, removed.text)
                bundle = client.get(bundle_path).json()
                self.assertEqual(bundle["house"]["status"], "partial")
                self.assertEqual(bundle["house"]["links"][0]["state"], "scale_unavailable")
                self.assertEqual(client.get(f"/api/assessments/{aid}/scene-links").json()[0]["state"], "scale_unavailable")

    def test_empty_rooms_leave_current_bundle_and_worker_but_preserve_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(data=Path(temporary))
            with TestClient(create_app(settings)) as client:

                aid, photo_ids, rooms = self.make_two_rooms(client)
                queued = client.post(f"/api/assessments/{aid}/analyze", json={
                    "expected_input_revision": 3, "expected_review_revision": 0})
                self.assertEqual(queued.status_code, 202, queued.text)
                worker.process_job(settings, worker.claim(settings))
                removed = client.delete(f"/api/assessments/{aid}/photos/{photo_ids[1]}",
                                        params={"expected_input_revision": 3})
                self.assertEqual(removed.status_code, 200, removed.text)
                bundle = client.get(f"/api/assessments/{aid}/bundle").json()
                self.assertEqual([room["id"] for room in bundle["rooms"]], [rooms[0]["id"]])
                self.assertEqual(bundle["house"]["status"], "single_room")
                self.assertEqual(bundle["house"]["reconstruction_state"], "ready")
                self.assertEqual(len(client.get(f"/api/assessments/{aid}/scenes").json()), 1)
                context = worker.load_context(settings, {"assessment_id": aid, "id": "read-only-check"})
                self.assertEqual([scene["id"] for scene in context["scenes"]], [rooms[0]["id"]])
                queued = client.post(f"/api/assessments/{aid}/analyze", json={
                    "expected_input_revision": 4, "expected_review_revision": 0})
                self.assertEqual(queued.status_code, 202, queued.text)
                worker.process_job(settings, worker.claim(settings))
                self.assertEqual(client.get(f"/api/jobs/{queued.json()['job_id']}").json()["status"], "succeeded")
                self.assertEqual(len(client.get(f"/api/assessments/{aid}/bundle").json()["rooms"]), 1)
                self.assertEqual(client.delete(f"/api/assessments/{aid}/photos/{photo_ids[0]}",
                                              params={"expected_input_revision": 4}).status_code, 200)
                empty = client.get(f"/api/assessments/{aid}/bundle").json()
                self.assertEqual(empty["rooms"], [])
                self.assertEqual(empty["house"]["status"], "unknown")
                self.assertEqual(empty["house"]["components"], [])
                with db.connect(settings) as conn:
                    self.assertEqual(conn.execute("SELECT count(*) FROM scenes WHERE assessment_id=?", (aid,)).fetchone()[0], 2)
                    self.assertEqual(conn.execute("SELECT count(*) FROM photos WHERE assessment_id=?", (aid,)).fetchone()[0], 2)
                restored = client.post(f"/api/assessments/{aid}/photos",
                    data={"scene_label": rooms[1]["label"], "expected_input_revision": 5},
                    files={"file": ("new.png", picture(), "image/png")})
                self.assertEqual(restored.status_code, 201, restored.text)
                self.assertEqual(client.get(f"/api/assessments/{aid}/scenes").json()[0]["id"], rooms[1]["id"])

    def test_metric_scale_requires_independent_holdout_and_is_reversible(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(data=Path(temporary))
            with TestClient(create_app(settings)) as client:

                aid, photo_ids, rooms = self.make_two_rooms(client)
                scene_id = rooms[0]["id"]
                with db.connect(settings, write=True) as conn:
                    payload = json.loads(conn.execute("SELECT payload FROM scenes WHERE id=?", (scene_id,)).fetchone()[0])
                    payload.update({"status": "ready", "provenance": "live", "reconstruction_kind": "multiview",
                                    "raw_coordinate_units": "relative", "selected_photo_ids": [photo_ids[0]],
                                    "scale_state": "unknown", "scale_factor": None})
                    conn.execute("UPDATE scenes SET payload=? WHERE id=?", (db.dumps(payload), scene_id))
                calibration = {
                    "expected_input_revision": 3, "expected_review_revision": 0,
                    "fit_segments": [
                        {"label": "door-width", "point_a": [0, 0, 0], "point_b": [1, 0, 0], "actual_distance_m": 2,
                         "source_photo_ids": [photo_ids[0]]},
                        {"label": "counter-width", "point_a": [0, 0, 1], "point_b": [2, 0, 1], "actual_distance_m": 4,
                         "source_photo_ids": [photo_ids[0]]},
                    ],
                    "holdout_segments": [{"label": "hall-width", "point_a": [0, 1, 0], "point_b": [0, 1, 0.5],
                                          "actual_distance_m": 1, "source_photo_ids": [photo_ids[0]]}],
                }
                path = f"/api/assessments/{aid}/scenes/{scene_id}/calibration"
                failed = {**calibration, "holdout_segments": [{**calibration["holdout_segments"][0], "actual_distance_m": 1.2}]}
                self.assertEqual(client.put(path, json=failed).status_code, 422)
                calibrated = client.put(path, json=calibration)
                self.assertEqual(calibrated.status_code, 200, calibrated.text)
                self.assertEqual(calibrated.json()["scale_factor_m_per_unit"], 2)
                self.assertEqual(calibrated.json()["review_revision"], 1)
                room = client.get(f"/api/assessments/{aid}/bundle").json()["rooms"][0]
                self.assertEqual(room["scale_state"], "metric_calibrated")
                self.assertEqual(room["scale_factor"], 2)
                self.assertEqual(room["calibration"]["holdout_max_relative_error"], 0)
                self.assertEqual(len(room["calibration"]["segments"]), 3)
                with db.connect(settings) as conn:
                    self.assertEqual(conn.execute("SELECT count(*) FROM measurements WHERE assessment_id=?", (aid,)).fetchone()[0], 1)

                removed = client.delete(path, params={"expected_input_revision": 3, "expected_review_revision": 1})
                self.assertEqual(removed.status_code, 200, removed.text)
                room = client.get(f"/api/assessments/{aid}/bundle").json()["rooms"][0]
                self.assertEqual(room["scale_state"], "unknown")
                self.assertIsNone(room["calibration"])
                with db.connect(settings) as conn:
                    self.assertEqual(conn.execute("SELECT count(*) FROM measurements WHERE assessment_id=?", (aid,)).fetchone()[0], 0)

    def test_schema_v1_marker_migrates_to_v3_without_losing_assessment(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(data=Path(temporary))
            db.init(settings)
            aid = db.create_assessment(settings, {"label": "Legacy", "profile": {"description": "Test"}})
            with db.connect(settings, write=True) as conn:
                conn.execute("DROP TABLE scene_links")
                conn.execute("ALTER TABLE schema_version RENAME TO schema_version_current")
                conn.execute("CREATE TABLE schema_version(version INTEGER PRIMARY KEY CHECK(version=1))")
                conn.execute("INSERT INTO schema_version VALUES(1)")
                conn.execute("DROP TABLE schema_version_current")
            db.init(settings)
            with db.connect(settings) as conn:
                self.assertEqual(conn.execute("SELECT version FROM schema_version").fetchone()[0], 3)
                self.assertEqual(db.assessment(conn, aid)["input_json"], db.dumps({"label": "Legacy", "profile": {"description": "Test"}}))
                self.assertIsNotNone(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scene_links'").fetchone())

if __name__ == "__main__":
    unittest.main()
