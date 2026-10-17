import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from step_wise import db
from step_wise.api import room_presentation
from step_wise.config import Settings
from step_wise.pipeline.geometry import estimate_floor, geometry
from step_wise.pipeline.photos import ingest
from step_wise.pipeline.reconstruction import live_reconstruct


class PhotoReconstructionReadinessTests(unittest.TestCase):
    def assessment(self, directory):
        settings = Settings(data=Path(directory), recon_backend="vggt", frame_cap=2)
        db.init(settings)
        aid = db.create_assessment(settings, {"profile_confirmed": True})
        return settings, aid

    def upload(self, settings, aid, label="Soggiorno", role="overview"):
        image = io.BytesIO()
        Image.new("RGB", (60, 90), "#9a8872").save(image, "PNG")
        with db.connect(settings) as conn:
            revision = db.assessment(conn, aid)["input_revision"]
        return ingest(settings, aid, image.getvalue(), label, role, revision)

    def test_frame_cap_preflight_counts_active_overviews_per_room_before_queueing(self):
        cases = [
            ([("Room", "overview")] * 3, False, None, False),
            ([("Room", "overview")] * 2 + [("Room", "detail")], False, None, True),
            ([("Room", "overview")] * 2 + [("Other", "overview")] * 2, False, None, True),
            ([("Room", "overview")] * 3, True, None, True),
            ([("Room", "overview")] * 3, False, ["prepare"], True),
        ]
        for photos, deactivate_last, scope, accepted in cases:
            with self.subTest(photos=photos, inactive=deactivate_last, scope=scope), tempfile.TemporaryDirectory() as tmp:
                settings, aid = self.assessment(tmp)
                for label, role in photos:
                    photo_id = self.upload(settings, aid, label, role)
                with db.connect(settings, write=True) as conn:
                    if deactivate_last:
                        conn.execute("UPDATE photos SET active=0 WHERE id=?", (photo_id,))
                    before = db.assessment(conn, aid)
                args = (settings, aid, "analyze", before["input_revision"], before["review_revision"])
                if accepted:
                    job = db.enqueue(*args, stage_scope=scope)
                    with db.connect(settings) as conn:
                        self.assertEqual(conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0], "queued")
                else:
                    with self.assertRaisesRegex(db.Conflict, "Room Room has 3 overview photos.*at most 2"):
                        db.enqueue(*args, stage_scope=scope)
                    with db.connect(settings) as conn:
                        self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
                        self.assertEqual(db.assessment(conn, aid), before)

    def test_worker_uses_all_overviews_and_refuses_over_cap_without_dropping_frames(self):
        photos = [{"id": str(i), "scene_id": "room", "role": "overview"} for i in range(2)]
        context = {"settings": Settings(recon_backend="vggt", frame_cap=2),
                   "scenes": [{"id": "room"}], "photos": photos + [{"id": "detail", "scene_id": "room", "role": "detail"}]}
        with patch("step_wise.pipeline.reconstruction.live_scene", return_value={"id": "room", "status": "ready"}) as inference:
            self.assertEqual(live_reconstruct(context)["status"], "succeeded")
            self.assertEqual(inference.call_args.args[2], photos)
            inference.reset_mock()
            context["photos"].append({"id": "third", "scene_id": "room", "role": "overview"})
            result = live_reconstruct(context)
            inference.assert_not_called()
            self.assertEqual(result["status"], "degraded")
            self.assertIn("3 overview photos", result["warnings"][0]["message"])

    def test_floor_estimation_ignores_padding_and_nonpositive_depth(self):
        v, u = np.mgrid[:30, :30]
        points = np.empty((2, 30, 30, 3))
        points[:] = np.stack((np.full_like(u, -2.), v / 29 * 2.5, u / 29 * 4 - 2), -1)
        points[:, :, :10] = np.stack((u[:, :10] / 9 * 4 - 2, np.zeros((30, 10)), v[:, :10] / 29 * 4 - 2), -1)
        rotation = np.diag([1., -1., -1.])
        cameras = np.stack([np.column_stack((rotation, -rotation @ center)) for center in [[0., 2., 0.], [.5, 2.1, .4]]])
        confidence = np.ones((2, 30, 30))
        # The invalid columns resemble a broad floor; the actual photographed region is only a wall.
        self.assertEqual(estimate_floor(points, confidence, cameras)["floor_state"], "estimated_relative")
        for padding, valid_depth in [(True, True), (False, False)]:
            with self.subTest(padding=padding), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "analysis.npz"
                depth = np.ones((2, 30, 30, 1))
                if not valid_depth:
                    depth[:, :, :10] = 0
                np.savez(path, world_points=points, depth=depth, confidence=confidence,
                         intrinsics=np.repeat(np.eye(3)[None], 2, axis=0), extrinsics=cameras)
                scene = {"id": "room", "status": "ready", "provenance": "live", "reconstruction_kind": "multiview",
                         "analysis_path": path.name, "analysis_sha256": db.digest(path.read_bytes()), "confidence_threshold": .5,
                         "images": [{"photo_id": str(i), "tensor_index": i,
                                     "transform": {"content_box": [10 if padding else 0, 0, 30, 30]}} for i in range(2)]}
                result = geometry({"settings": Settings(data=Path(tmp)), "outputs": {
                    "reconstruct": {"scenes": [scene]}, "jev": {"candidates": []}, "gemma": {"hazards": []}}})
                self.assertEqual(result["scenes"][0]["floor_state"], "unknown")

    def test_new_photo_hides_stale_completion_and_preserves_its_immutable_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings, aid = self.assessment(tmp)
            first_photo = self.upload(settings, aid)
            model = settings.data / "completed.glb"
            original = b"previous completed room asset"
            model.write_bytes(original)
            with db.connect(settings, write=True) as conn:
                scene = db.active_scenes(conn, aid)[0]
                row = db.assessment(conn, aid)
                asset = db.store_asset(settings, conn, aid, model.name, "model/gltf-binary", "photo_completed", "derived")
                presentation = {"asset_id": asset, "provenance": "photo_completed", "coordinate_space": "raw_scene_relative",
                                "source_job_id": "previous-job", "input_revision": row["input_revision"], "hazard_positions": {}}
                conn.execute("UPDATE assessments SET analysis_job_id='previous-job',status='review_ready' WHERE id=?", (aid,))
                conn.execute("UPDATE scenes SET payload=? WHERE id=?", (db.dumps({"presentation": presentation}), scene["id"]))
                self.assertIsNotNone(room_presentation({"presentation": presentation}, db.assessment(conn, aid)))
            self.upload(settings, aid)
            with db.connect(settings) as conn:
                current = db.assessment(conn, aid)
                updated = json.loads(db.active_scenes(conn, aid)[0]["payload"])
                self.assertEqual(current["input_revision"], row["input_revision"] + 1)
                self.assertIsNone(current["analysis_job_id"])
                self.assertIsNone(room_presentation(updated, current))
                self.assertEqual(conn.execute("SELECT sha256 FROM assets WHERE id=?", (asset,)).fetchone()[0], db.digest(original))
                self.assertEqual(conn.execute("SELECT active FROM photos WHERE id=?", (first_photo,)).fetchone()[0], 1)
            self.assertEqual(model.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
