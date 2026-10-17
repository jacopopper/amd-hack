import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
import requests
from PIL import Image

from step_wise import db, worker
from step_wise.config import ROOT, Settings
from step_wise.demo import generate_controls, seed_case
from step_wise.models import AssessmentOutput, Coverage, Evidence, Hazard, HazardProposal
from step_wise.pipeline import gemma, geometry, jev, reconstruction
from step_wise.pipeline.http import ExternalFailure, post_json
from step_wise.validation import validate_hazard, validate_output


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
        prompt = (ROOT / "backend/src/step_wise/prompts/jev-general.txt").read_text().strip()
        self.assertEqual(payload["questions"]["hazard_review"]["instructions"], prompt)
        self.assertEqual(set(payload["questions"]), {"hazard_review", "hazard_region"})
        self.assertNotIn("rug", json.dumps(payload).lower())
        body = {"model": jev.MODEL, "answers": {
            "hazard_review": {"type": "noul", "noul": .9},
            "hazard_region": {"type": "box", "box": {"left": .2, "top": .2, "right": .4, "bottom": .6}},
        }}
        prep = {"upright_size": [960, 640], "transform": {"input_size": [960, 640], "scale_xy": [1, 1], "offset_xy": [0, 0], "content_box": [0, 0, 960, 640]}}
        candidate = jev.interpret(body, {"id": "p"}, prep, [entry])[0]
        self.assertEqual(candidate["presence"], "present")
        self.assertEqual(candidate["category"], "potential_home_safety_hazard")
        body["answers"]["hazard_review"]["noul"] = .01
        candidate = jev.interpret(body, {"id": "p"}, prep, [entry])[0]
        self.assertIsNone(candidate["evidence"])
        body["answers"]["hazard_region"]["box"]["right"] = 2
        with self.assertRaises(ValueError):
            jev.interpret(body, {"id": "p"}, prep, [entry])

    def test_no_ambiguous_transport_retry_and_one_schema_repair(self):
        with patch("step_wise.pipeline.http.requests.post", side_effect=requests.Timeout) as post:
            with self.assertRaises(ExternalFailure):
                post_json("http://internal.invalid", {})
            self.assertEqual(post.call_count, 1)
        settings = Settings()
        valid = {"hazards": [], "coverage": [], "follow_up_requests": []}
        make = lambda content, reason="stop": {"model": settings.gemma_model, "choices": [{"finish_reason": reason, "message": {"content": content, "reasoning": "private"}}], "usage": {"completion_tokens": 10}}
        with patch("step_wise.pipeline.gemma.post_json", side_effect=[make("{"), make(json.dumps(valid))]) as post:
            output, attempts = gemma.complete(settings, AssessmentOutput, "prompt", "data")
            self.assertEqual(len(attempts), 2)
            self.assertEqual(output.hazards, [])
            self.assertNotIn("reasoning", json.dumps(attempts))
            self.assertEqual(post.call_args.args[1]["response_format"]["type"], "json_schema")
            self.assertTrue(post.call_args.args[1]["response_format"]["json_schema"]["strict"])
            self.assertEqual([m["role"] for m in post.call_args.args[1]["messages"]], ["system", "user"])
        with patch("step_wise.pipeline.gemma.post_json", return_value=make(json.dumps(valid), "length")) as post:
            with self.assertRaises(ExternalFailure):
                gemma.complete(settings, AssessmentOutput, "prompt", "data")
            self.assertEqual(post.call_count, 2)

    def test_huggingface_auth_provider_pin_and_audit_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            token = "synthetic-secret-for-test-only"
            settings = replace(Settings(), gemma_base_url="https://router.huggingface.co/v1", gemma_provider="test-provider",
                               hf_token=token, gemma_timeout_seconds=37)
            # HF/Novita normalizes model IDs to lowercase in its response.
            body = {"model": settings.gemma_model.lower(), "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"hazards": [], "coverage": [], "follow_up_requests": []})}}]}
            with patch("step_wise.pipeline.gemma.post_json", return_value=body) as post:
                gemma.complete(settings, AssessmentOutput, "private prompt", "private content", audit_directory=Path(directory))
            self.assertEqual(post.call_args.args[1]["model"], settings.gemma_model + ":test-provider")
            self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer " + token)
            self.assertEqual(post.call_args.kwargs["timeout"], (10, 37))
            self.assertNotIn(token, "".join(p.read_text() for p in Path(directory).glob("*.json")))
            missing = replace(settings, hf_token="")
            with patch("step_wise.pipeline.gemma.post_json") as post:
                with self.assertRaises(ExternalFailure):
                    gemma.complete(missing, AssessmentOutput, "prompt", "content")
                post.assert_not_called()

    def test_validation_repair_does_not_echo_private_exception_values(self):
        with tempfile.TemporaryDirectory() as directory:
            private = "synthetic-private-value-in-exception"
            body = {"model": Settings().gemma_model, "choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps({"hazards": [], "coverage": [], "follow_up_requests": []})}}]}
            def invalid(_):
                raise ValueError(private)
            with patch("step_wise.pipeline.gemma.post_json", return_value=body) as post:
                with self.assertRaises(ExternalFailure):
                    gemma.complete(Settings(), AssessmentOutput, "prompt", "data", invalid, Path(directory))
            self.assertNotIn(private, json.dumps(post.call_args.args[1]))
            self.assertNotIn(private, "".join(p.read_text() for p in Path(directory).glob("*.json")))

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
            generic = response["hazards"][0]
            generic["category"] = "unstable_furniture"
            generic["guidance_ids"] = ["home-hazard-review"]
            response["hazards"] = [{key: value for key, value in h.items() if key in HazardProposal.model_fields} for h in response["hazards"]]
            prepared = {p["photo_id"]: p for p in context["outputs"]["prepare"]["photos"]}
            for hazard in response["hazards"]:
                wire = []
                for evidence in hazard["evidence"]:
                    width, height = prepared[evidence["photo_id"]]["upright_size"]
                    box = evidence["box"]
                    native = [box[1] / height * 1000, box[0] / width * 1000, box[3] / height * 1000, box[2] / width * 1000] if box else None
                    wire.append({"photo_id": evidence["photo_id"], "box_yxyx_1000": native})
                hazard["evidence"] = wire
            context["assessment_input"]["external_processing_consent"] = True
            context["assessment_input"]["clinical_external_processing_consent"] = True
            context["settings"] = replace(context["settings"], gemma_backend="http", gemma_revision="test-revision")
            body = {"model": context["settings"].gemma_model, "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(response)}}]}
            with patch("step_wise.pipeline.gemma.post_json", return_value=body) as post:
                output = gemma.assess(context)
                self.assertEqual(output["provenance"], "live")  # mocked transport, not hardware evidence
                content = post.call_args.args[1]["messages"][1]["content"]
                images = [c for c in content if c["type"] == "image_url"]
                self.assertEqual(len(images), 2)
                self.assertTrue(all(c["image_url"]["url"].startswith("data:image/jpeg;base64,") for c in images))
                self.assertTrue(all(c["coverage"] == "unknown" for c in output["coverage"]))
                self.assertEqual(output["hazards"][0]["suggested_action"], context["guidance"]["home-hazard-review"]["action"])
                self.assertEqual(output["hazards"][0]["profile_relevance"], gemma.relevance(context["assessment_input"]["profile"], output["hazards"][0]["profile_fields"]))
                np.testing.assert_allclose(output["hazards"][0]["evidence"][0]["box"], original["hazards"][0]["evidence"][0]["box"])
                fields = post.call_args.args[1]["response_format"]["json_schema"]["schema"]["$defs"]["HazardProposal"]["properties"]
                self.assertFalse({"anchor", "profile_relevance", "suggested_action", "measurement_ids"} & fields.keys())
            response["coverage"] = [{"photo_id": context["photos"][0]["id"], "guidance_id": "floor-rug",
                                     "coverage": "visible", "note": "Prova sintetica di copertura."}]
            body["choices"][0]["message"]["content"] = json.dumps(response)
            with patch("step_wise.pipeline.gemma.post_json", return_value=body):
                mapped = gemma.assess(context)
                self.assertTrue(any(c["category"] == "rug" and c["coverage"] == "visible" for c in mapped["coverage"]))
            response["hazards"][0]["evidence"][0]["photo_id"] = "another-assessment"
            body["choices"][0]["message"]["content"] = json.dumps(response)
            with patch("step_wise.pipeline.gemma.post_json", return_value=body):
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

    def test_open_ended_hazard_uses_only_the_generic_review_action(self):
        from step_wise.models import GuidanceChecklist

        checklist = GuidanceChecklist.model_validate_json((ROOT / "guidance/checklist.json").read_text()).model_dump(mode="json")
        guidance = {entry["id"]: entry for entry in checklist["entries"]}
        fallback = guidance["home-hazard-review"]
        profile = {"description": "Profilo fittizio"}
        context = {"photos": [{"id": "photo", "scene_id": "room", "payload": {"width": 100, "height": 100}}],
                   "assessment_input": {"profile": profile}}
        hazard = Hazard(category="unstable_furniture", observation="Possibile instabilità da verificare.",
                        profile_relevance="Non collegato al profilo.", profile_fields=[], priority="recommended",
                        evidence=[Evidence(photo_id="photo", box=[5, 5, 40, 40])], guidance_ids=[fallback["id"]],
                        uncertainty="La fotografia non consente di confermare la stabilità.", suggested_action=fallback["action"])
        output = AssessmentOutput(hazards=[hazard], coverage=[Coverage(photo_id="photo", category="rug", coverage="unknown", note="Non valutato.")], follow_up_requests=[])
        validate_output(output, context, {}, guidance)

        hazard.suggested_action = "Anchor it to the wall immediately."
        with self.assertRaises(ValueError):
            validate_output(output, context, {}, guidance)
        validate_hazard(hazard, {"photo": context["photos"][0]}, {}, guidance, profile, {})

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

    def test_floor_estimate_is_conservative_and_orients_normal_to_camera_up(self):
        rng = np.random.default_rng(12)
        floor_x, floor_z = np.meshgrid(np.linspace(-2, 2, 45), np.linspace(-2, 2, 45))
        floor = np.column_stack((floor_x.ravel(), np.zeros(floor_x.size), floor_z.ravel()))
        wall_y = rng.uniform(0, 2.5, 2400)
        wall_z = rng.uniform(-2, 2, 2400)
        wall = np.column_stack((np.full(len(wall_y), -2.), wall_y, wall_z))
        table_x, table_z = np.meshgrid(np.linspace(-1.2, 1.2, 30), np.linspace(-1.2, 1.2, 30))
        table = np.column_stack((table_x.ravel(), np.full(table_x.size, .8), table_z.ravel()))
        points = np.concatenate((floor, wall, table))
        rotation = np.diag([1., -1., -1.])  # OpenCV camera-up maps to world +Y.
        centers = np.array([[0., 2., 0.], [.5, 2.1, .4]])
        cameras = np.stack([np.column_stack((rotation, -rotation @ center)) for center in centers])
        estimate = geometry.estimate_floor(points, np.ones(len(points)), cameras)
        self.assertEqual(estimate["floor_state"], "estimated_relative")
        self.assertEqual(estimate["gravity_state"], "estimated_from_consistent_camera_up")
        np.testing.assert_allclose(estimate["gravity_up"], [0, 1, 0], atol=1e-8)
        self.assertGreater(estimate["floor_plane"]["normal"][1], .99)
        self.assertLess(abs(estimate["floor_plane"]["height_along_gravity"]), .03)
        inverted, offset = geometry.orient_plane(np.array([0., -1., 0.]), 2., np.array([0., 1., 0.]))
        np.testing.assert_allclose(inverted, [0, 1, 0])
        self.assertEqual(offset, -2.)

        wall_only = geometry.estimate_floor(wall, np.ones(len(wall)), cameras)
        self.assertEqual(wall_only["floor_state"], "unknown")
        table_only = geometry.estimate_floor(table, np.ones(len(table)), cameras)
        self.assertEqual(table_only["floor_state"], "unknown")
        tilted = floor.copy()
        tilted[:, 1] = .6 * tilted[:, 0]
        no_horizontal_floor = geometry.estimate_floor(tilted, np.ones(len(tilted)), cameras)
        self.assertEqual(no_horizontal_floor["floor_state"], "unknown")
        random_cloud = rng.normal(size=(1800, 3))
        no_plane = geometry.estimate_floor(random_cloud, np.ones(len(random_cloud)), cameras)
        self.assertEqual(no_plane["floor_state"], "unknown")

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
