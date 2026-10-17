import tempfile
import unittest
from pathlib import Path

import numpy as np

from step_wise import db
from step_wise.config import Settings
from step_wise.pipeline.geometry import geometry
from step_wise.pipeline.reconstruction import validate_predictions


class MultiviewGeometry(unittest.TestCase):
    def run_scene(self, root, foreground_depth=4., baseline=.25, second_box=True, corrupt_room=False):
        settings = Settings(data=root)
        k = np.array([[500., 0, 259], [0, 500., 259], [0, 0, 1.]])
        cameras = np.stack([np.column_stack((np.eye(3), [-x, 0, 0])) for x in (0, baseline)])
        depth = np.full((2, 518, 518, 1), 5., dtype=np.float32)
        boxes = [(208, 208, 310, 310), (208 - 500 * baseline / foreground_depth, 208,
                                             310 - 500 * baseline / foreground_depth, 310)]
        yy, xx = np.mgrid[:518, :518]
        rays = (np.linalg.inv(k) @ np.stack((xx, yy, np.ones_like(xx))).reshape(3, -1)).T.reshape(518, 518, 3)
        points = []
        for index, (l, t, r, b) in enumerate(boxes):
            depth[index, int(t):int(b), int(l):int(r), 0] = foreground_depth
            points.append(rays * depth[index] - cameras[index, :, 3])
        points = np.asarray(points, dtype=np.float32)
        confidence = np.ones(depth.shape[:3], dtype=np.float32)
        intrinsics = np.stack((k, k))
        validate_predictions(depth, confidence, cameras, intrinsics, points, 2)
        path = root / "scene.npz"
        np.savez_compressed(path, world_points=points, depth=depth, confidence=confidence,
                            intrinsics=intrinsics, extrinsics=cameras)
        transform = {"scale_xy": [1, 1], "offset_xy": [0, 0], "content_box": [0, 0, 518, 518]}
        scene = {"id": "room", "status": "ready", "provenance": "live", "reconstruction_kind": "multiview",
                 "analysis_path": path.name, "analysis_sha256": db.digest(path.read_bytes()), "confidence_threshold": .5,
                 "images": [{"photo_id": f"photo-{i}", "tensor_index": i, "transform": transform} for i in range(2)]}
        evidence = [{"photo_id": f"photo-{i}", "box": box} for i, box in enumerate(boxes) if i == 0 or second_box]
        return geometry({"settings": settings, "outputs": {
            "reconstruct": {"scenes": [scene, {**scene, "id": "broken-room", "analysis_path": "missing.npz"}] if corrupt_room else [scene]}, "jev": {"candidates": []},
            "gemma": {"hazards": [{"category": "visible_feature", "evidence": evidence}]}}})

    def test_foreground_needs_depth_separation_boxed_second_view_and_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            supported = self.run_scene(root)
            self.assertEqual(len(supported["anchors"]), 2)
            self.assertEqual(set(supported["anchors"][0]["anchor"]["source_photo_ids"]), {"photo-0", "photo-1"})
            for kwargs in ({"foreground_depth": 5.}, {"foreground_depth": 4.99},
                           {"baseline": 0.}, {"second_box": False}):
                result = self.run_scene(root, **kwargs)
                self.assertEqual(result["anchors"], [], kwargs)
                self.assertTrue(result["anchor_abstentions"], kwargs)

    def test_corrupt_room_does_not_erase_an_independent_successful_scene(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_scene(Path(temporary), corrupt_room=True)
            self.assertEqual(result["status"], "degraded")
            self.assertEqual([scene["status"] for scene in result["scenes"]], ["ready", "failed"])
            self.assertEqual(len(result["anchors"]), 2)


if __name__ == "__main__":
    unittest.main()
