import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from pydantic import ValidationError

from scripts.attach_room_presentation import main as attach_room_presentation
from step_wise import db
from step_wise.api import room_presentation
from step_wise.config import Settings
from step_wise.models import RoomPresentation, VisualAssessmentProposal
from step_wise.pipeline.gemma import schema_failure_reason
from step_wise.pipeline.geometry import geometry
from step_wise.pipeline.http import ExternalFailure, post_chat_streaming
from step_wise.pipeline.reconstruction import observed_surface, write_ply


class RoomSurfaceTests(unittest.TestCase):
    def test_illustrative_presentation_expires_with_its_analysis(self):
        scene = {"presentation": {"source_job_id": "job", "input_revision": 1,
                                 "asset_id": "asset", "hazard_positions": {"hazard": [0, 0, 0]}}}
        self.assertEqual(room_presentation(scene, {"analysis_job_id": "job", "input_revision": 1})["provenance"], "illustrative")
        self.assertIsNone(room_presentation(scene, {"analysis_job_id": "new-job", "input_revision": 1}))
        self.assertIsNone(room_presentation(scene, {"analysis_job_id": "job", "input_revision": 2}))

    def test_photo_completion_requires_raw_coordinates_and_ignores_illustration_pins(self):
        scene = {"presentation": {"source_job_id": "job", "input_revision": 1,
                                 "asset_id": "asset", "provenance": "photo_completed",
                                 "hazard_positions": {"hazard": [9, 9, 9]}}}
        assessment = {"analysis_job_id": "job", "input_revision": 1}
        self.assertIsNone(room_presentation(scene, assessment))
        scene["presentation"]["coordinate_space"] = "raw_scene_relative"
        scene["presentation"]["display_transform"] = np.diag([1, -1, -1, 1]).tolist()
        presentation = RoomPresentation.model_validate(room_presentation(scene, assessment))
        self.assertEqual(presentation.provenance, "photo_completed")
        self.assertEqual(presentation.coordinate_space, "raw_scene_relative")
        self.assertEqual(presentation.hazard_positions, {})
        self.assertEqual(presentation.display_transform[1][1], -1)
        self.assertIsNone(room_presentation(scene, {**assessment, "input_revision": 2}))

    def test_photo_attachment_rejects_illustration_pins_and_nonrigid_transform_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chunk = b'{"asset":{"version":"2.0"}}'
            model = root / "room.glb"
            model.write_bytes(struct.pack("<4sIIII", b"glTF", 2, 20 + len(chunk), len(chunk), 0x4E4F534A) + chunk)
            (root / "completion.json").write_text(json.dumps({
                "provenance": "photo_completed", "coordinate_space": "raw_scene_relative",
                "assessment_id": "assessment", "scene_id": "scene", "input_revision": 1, "source_job_id": "job",
                "model_bytes": model.stat().st_size, "model_sha256": db.digest(model.read_bytes()),
                "display_transform": np.eye(4).tolist()}))
            pins, transform = root / "pins.json", root / "transform.json"
            pins.write_text(json.dumps({"hazard": [9, 9, 9]}))
            transform.write_text(json.dumps(np.diag([2, 1, 1, 1]).tolist()))
            args = ["attach", "--assessment", "assessment", "--scene", "scene", "--model", str(model),
                    "--provenance", "photo_completed", "--expected-input-revision", "1", "--expected-analysis-job", "job"]
            with patch("scripts.attach_room_presentation.Settings.from_env", return_value=Settings(data=root)), \
                    patch("scripts.attach_room_presentation.db.connect") as connect:
                for flag, path in [("--pins", pins), ("--display-transform", transform)]:
                    with patch("sys.argv", args + [flag, str(path)]), self.assertRaises(ValueError):
                        attach_room_presentation()
                connect.assert_not_called()

    def test_photo_surface_pointer_is_not_a_validated_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            y, x = np.mgrid[-.2:.2:32j, -.2:.2:32j]
            points = np.stack((x, y, np.full_like(x, 2)), -1)[None]
            path = Path(tmp) / "scene.npz"
            np.savez(path, world_points=points, depth=np.full((1, 32, 32, 1), 2.),
                     confidence=np.ones((1, 32, 32)), intrinsics=np.eye(3)[None],
                     extrinsics=np.eye(4)[None, :3])
            scene = {"id": "room", "status": "ready", "provenance": "live",
                     "analysis_path": "scene.npz", "analysis_sha256": db.digest(path.read_bytes()),
                     "confidence_threshold": .5, "reconstruction_kind": "single_view_estimate",
                     "images": [{"photo_id": "photo", "tensor_index": 0,
                                 "transform": {"scale_xy": [1, 1], "offset_xy": [0, 0], "content_box": [0, 0, 32, 32]}}]}
            result = geometry({"settings": Settings(data=Path(tmp)), "outputs": {
                "reconstruct": {"scenes": [scene]}, "jev": {"candidates": []},
                "gemma": {"hazards": [{"category": "path", "evidence": [{"photo_id": "photo", "box": [2, 2, 30, 30]}]}]}}})
            self.assertEqual(result["anchors"], [])
            self.assertEqual(len(result["evidence_markers"]), 1)
            self.assertEqual(result["evidence_markers"][0]["marker"]["method"], "photo_surface_estimate")

    def test_schema_repair_names_fields_without_echoing_model_values(self):
        invalid = {"hazards": [{"category": "path", "observation": "Object in path",
                   "profile_fields": ["description"], "priority": "important",
                   "evidence": [{"photo_id": "photo", "box_yxyx_1000": None}],
                   "guidance_ids": ["path-clear"], "uncertainty": None}],
                   "coverage": [], "follow_up_requests": [], "untrusted_model_text": "private"}
        with self.assertRaises(ValidationError) as raised:
            VisualAssessmentProposal.model_validate(invalid)
        reason = schema_failure_reason(VisualAssessmentProposal.model_json_schema(), raised.exception)
        self.assertIn("hazards.0.uncertainty must be a nonempty string", reason)
        self.assertNotIn("untrusted_model_text", reason)
        self.assertNotIn("private", reason)

    def test_observed_mesh_does_not_bridge_depth_edges_or_padding(self):
        y, x = np.mgrid[:4, :5]
        points = np.stack((x, y, np.ones_like(x)), -1)[None].astype(float)
        depth = np.ones((1, 4, 5, 1))
        depth[:, :, 3:] = 10
        mask = np.ones((1, 4, 5), bool)
        mask[:, 0] = False
        colors = np.full(points.shape, 128, np.uint8)
        vertices, rgb, faces = observed_surface(points, depth, np.ones(mask.shape), colors, mask, .5)
        self.assertTrue(len(faces))
        self.assertTrue((vertices[:, 1] >= 1).all())
        for triangle in vertices[faces]:
            self.assertFalse(triangle[:, 0].min() < 3 <= triangle[:, 0].max())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "room.ply"
            write_ply(path, vertices, rgb, faces)
            header = path.read_bytes().split(b"end_header\n")[0]
            self.assertIn(f"element face {len(faces)}".encode(), header)

    def test_stream_is_bounded_and_never_invents_completion(self):
        def event(value):
            return b"data: " + json.dumps(value).encode()

        lines = [
            event({"model": "test", "choices": [{"delta": {"content": "{}"}}]}),
            event({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            b"data: [DONE]",
        ]
        response = MagicMock(status_code=200)
        response.__enter__.return_value = response
        with patch("step_wise.pipeline.http.requests.post", return_value=response):
            response.iter_lines.return_value = iter(lines)
            self.assertEqual(post_chat_streaming("https://test.invalid", {})["choices"][0]["finish_reason"], "stop")
            response.iter_lines.return_value = iter(lines[:1])
            with self.assertRaises(ExternalFailure):
                post_chat_streaming("https://test.invalid", {})
            response.iter_lines.return_value = iter(lines)
            with self.assertRaises(ExternalFailure):
                post_chat_streaming("https://test.invalid", {}, max_bytes=20)
