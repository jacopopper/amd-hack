import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
import requests
from PIL import Image

from safe_home import db, worker
from safe_home.config import ROOT, Settings
from safe_home.demo import generate_controls, seed_case
from safe_home.models import AssessmentOutput, Hazard
from safe_home.pipeline import gemma, geometry, jev, reconstruction
from safe_home.pipeline.http import ExternalFailure, post_json
from safe_home.validation import validate_output


class ClientsGeometry(unittest.TestCase):
    def context(self, directory):
        settings = Settings(data=Path(directory))
        generate_controls()
        db.init(settings)
        aid, _ = seed_case(settings, ROOT / "demo/cases/room-001.json")
        with db.connect(settings) as conn:
            row = db.assessment(conn, aid)
        db.enqueue(settings, aid, "analyze", row["input_revision"], 0)
        job = worker.claim(settings)
        context = worker.process_job(settings, job)
        return context

    def test_jev_pixel_only_payload_and_saved_response_gates(self):
        entry = {"id": "floor-rug", "category": "rug", "question": "a visible rug"}
        payload = jev.request_payload(b"pixels", [entry])
        self.assertEqual(set(payload), {"model", "state", "assets", "questions"})
        self.assertEqual(len(payload["assets"]), 1)
        self.assertNotIn("profile", json.dumps(payload))
        saved = json.loads((ROOT / "eval/jev/results-2026-10-16/staged_room-1.json").read_text())["response"]
        body = {"model": jev.MODEL, "answers": {"rug_present": saved["answers"]["rug_present"], "rug_box": saved["answers"]["rug_box"], "rug_coverage": saved["answers"]["floor_view"], "rug_attachment": saved["answers"]["rug_attachment"]}}
        prep = {"upright_size": [960, 640], "transform": {"input_size": [960, 640], "scale_xy": [1, 1], "offset_xy": [0, 0], "content_box": [0, 0, 960, 640]}}
        candidate = jev.interpret(body, {"id": "p"}, prep, [entry])[0]
        self.assertEqual(candidate["presence"], "present")
        self.assertEqual(candidate["attachment"], "unknown")
        body["answers"]["rug_present"]["noul"] = .01
        body["answers"]["rug_attachment"]["choice"] = "loose"
        candidate = jev.interpret(body, {"id": "p"}, prep, [entry])[0]
        self.assertIsNone(candidate["evidence"])
        self.assertEqual(candidate["attachment"], "unknown")
        body["answers"]["rug_box"]["box"]["right"] = 2
        with self.assertRaises(ValueError):
            jev.interpret(body, {"id": "p"}, prep, [entry])

    def test_no_ambiguous_transport_retry_and_one_schema_repair(self):
        with patch("safe_home.pipeline.http.requests.post", side_effect=requests.Timeout) as post:
            with self.assertRaises(ExternalFailure):
                post_json("http://internal.invalid", {})
            self.assertEqual(post.call_count, 1)
        settings = Settings()
        valid = {"hazards": [], "coverage": [], "follow_up_requests": []}
        make = lambda content, reason="stop": {"model": settings.gemma_model, "choices": [{"finish_reason": reason, "message": {"content": content, "reasoning": "private"}}], "usage": {"completion_tokens": 10}}
        with patch("safe_home.pipeline.gemma.post_json", side_effect=[make("{"), make(json.dumps(valid))]) as post:
            output, attempts = gemma.complete(settings, AssessmentOutput, "prompt", "data")
            self.assertEqual(len(attempts), 2)
            self.assertEqual(output.hazards, [])
            self.assertNotIn("reasoning", json.dumps(attempts))
            self.assertEqual(post.call_args.args[1]["response_format"]["type"], "json_schema")
        with patch("safe_home.pipeline.gemma.post_json", return_value=make(json.dumps(valid), "length")) as post:
            with self.assertRaises(ExternalFailure):
                gemma.complete(settings, AssessmentOutput, "prompt", "data")
            self.assertEqual(post.call_count, 2)

    def test_live_gemma_images_refs_profile_and_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            context = self.context(directory)
            worker.publish_analysis(context)
            with db.connect(context["settings"]) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM hazards WHERE job_id=?", (context["job"]["id"],)).fetchone()[0], 4)
            original = context["outputs"]["gemma"]
            fields = ("hazards", "coverage", "follow_up_requests")
            response = {k: original[k] for k in fields}
            response["coverage"] = []  # must become unknown, never implicitly visible
            for hazard in response["hazards"]:
                hazard["profile_relevance"] = gemma.relevance(context["assessment_input"]["profile"], hazard["profile_fields"])
            context["settings"] = replace(context["settings"], gemma_backend="http", gemma_revision="test-revision")
            body = {"model": context["settings"].gemma_model, "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(response)}}]}
            with patch("safe_home.pipeline.gemma.post_json", return_value=body) as post:
                output = gemma.assess(context)
                self.assertEqual(output["provenance"], "live")  # mocked transport, not hardware evidence
                content = post.call_args.args[1]["messages"][1]["content"]
                images = [c for c in content if c["type"] == "image_url"]
                self.assertEqual(len(images), 2)
                self.assertTrue(all(c["image_url"]["url"].startswith("data:image/jpeg;base64,") for c in images))
                self.assertTrue(all(c["coverage"] == "unknown" for c in output["coverage"]))
            response["hazards"][0]["evidence"][0]["photo_id"] = "another-assessment"
            body["choices"][0]["message"]["content"] = json.dumps(response)
            with patch("safe_home.pipeline.gemma.post_json", return_value=body):
                with self.assertRaises(ExternalFailure):
                    gemma.assess(context)

    def test_cross_scene_bounds_guidance_and_unknown_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            context = self.context(directory)
            response = {k: context["outputs"]["gemma"][k] for k in ("hazards", "coverage", "follow_up_requests")}
            scenes = {s["id"]: s for s in context["outputs"]["geometry"]["scenes"]}
            valid = AssessmentOutput.model_validate(response)
            hazard = valid.hazards[0]
            hazard.guidance_ids = ["invented"]
            with self.assertRaises(ValueError):
                validate_output(valid, context, scenes, context["guidance"])
            hazard.guidance_ids = ["floor-rug"]
            hazard.profile_fields = ["vision"]
            with self.assertRaises(ValueError):
                validate_output(valid, context, scenes, context["guidance"])
            hazard.profile_fields = ["description"]
            hazard.measurement_ids = ["invented-measurement"]
            with self.assertRaises(ValueError):
                validate_output(valid, context, scenes, context["guidance"])

    def test_rotated_camera_and_ambiguous_anchor_abstention(self):
        rotation = np.array([[0., 0, 1], [0, 1, 0], [-1, 0, 0]])
        translation = np.array([1., 2, 3])
        camera = np.column_stack([rotation, translation])
        intrinsics = np.array([[500., 0, 100], [0, 500, 200], [0, 0, 1]])
        points_camera = np.array([[.2, .3, 2], [.3, -.1, 4]])
        pixels_h = (intrinsics @ points_camera.T).T
        pixels = pixels_h[:, :2] / pixels_h[:, 2, None]
        world = geometry.unproject(pixels, points_camera[:, 2], intrinsics, camera)
        np.testing.assert_allclose(world @ rotation.T + translation, points_camera)
        mixed = np.array([[0, 0, 1]] * 30 + [[0, 0, 4]] * 30)
        self.assertIsNone(geometry.supported_anchor(mixed, np.ones(60)))
        self.assertIsNone(geometry.supported_anchor(mixed, np.zeros(60)))

    def test_live_reconstruction_missing_prerequisite_never_uses_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            context = self.context(directory)
            context["settings"] = replace(context["settings"], recon_backend="vggt")
            result = reconstruction.reconstruct(context)
            self.assertEqual(result["status"], "degraded")
            self.assertTrue(all(s["status"] == "failed" and "ply_path" not in s for s in result["scenes"]))
            self.assertNotIn("fixture", json.dumps(result))
        image = Image.new("RGB", (900, 600))
        prepared, transform = reconstruction.aligned_input(image)
        self.assertEqual(prepared.size, (518, 518))
        self.assertEqual(transform["content_box"][2] - transform["content_box"][0], 518)


if __name__ == "__main__":
    unittest.main()
