import io
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from step_wise import cases, db, worker
from step_wise.api import create_app
from step_wise.config import ROOT, Settings
from step_wise.demo import generate_controls
from step_wise.models import AssessmentBundle, Hazard
from step_wise.pipeline import avatar, gemma, geometry, jev, photos, reconstruction
from step_wise.pipeline.http import ExternalFailure
from step_wise.pipeline.registration import bind_metric_link, compose_rooms
from step_wise.validation import validate_hazard


def picture():
    stream = io.BytesIO()
    Image.new("RGB", (96, 64), "#abbd92").save(stream, "PNG")
    return stream.getvalue()


class QueuedLocal3D(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.settings = Settings(data=Path(self.temporary.name), reviewer_password="fixture-testing-password")
        self.client = TestClient(create_app(self.settings))
        self.client.auth = ("reviewer", self.settings.reviewer_password)

    def tearDown(self):
        self.client.close()
        self.temporary.cleanup()

    def create(self):
        aid = self.client.post("/api/assessments", json={
            "label": "Synthetic integration", "profile": {"description": "Fictional walker user"},
            "profile_confirmed": True, "staged_data_confirmed": True, "processing_consent": True,
            "clinical_external_processing_consent": True,
        }).json()["id"]
        result = self.client.post(f"/api/assessments/{aid}/photos", data={
            "scene_label": "Kitchen", "role": "overview", "expected_input_revision": 1,
        }, files={"file": ("synthetic.png", picture(), "image/png")})
        self.assertEqual(result.status_code, 201, result.text)
        return aid

    def assessment(self, aid):
        return self.client.get(f"/api/assessments/{aid}").json()

    def revisions(self, aid):
        row = self.assessment(aid)
        return {"expected_input_revision": row["input_revision"], "expected_review_revision": row["review_revision"]}

    def analyze(self, aid):
        queued = self.client.post(f"/api/assessments/{aid}/analyze", json=self.revisions(aid))
        self.assertEqual(queued.status_code, 202, queued.text)
        context = worker.process_job(self.settings, worker.claim(self.settings))
        with db.connect(self.settings) as conn:
            job = dict(conn.execute("SELECT * FROM jobs WHERE id=?", (queued.json()["job_id"],)).fetchone())
        return context, job, json.loads(job["stages"])

    def test_profile_edit_reuses_visual_stages_and_preserves_immutable_artifacts(self):
        aid = self.create()
        first, job, _ = self.analyze(aid)
        old_manifest = next((first["directory"] / "assets").glob("*-scene.json"))
        original = old_manifest.read_bytes()
        row = self.assessment(aid)
        edited = self.client.patch(f"/api/assessments/{aid}", json={
            **row["inputs"], "profile": {"description": "Corrected fictional profile", "mobility_aid": "walker"},
            "clinical_external_processing_consent": True, "expected_input_revision": row["input_revision"],
        })
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertFalse(self.assessment(aid)["inputs"]["clinical_external_processing_consent"])
        self.assertTrue(self.client.get(f"/api/assessments/{aid}/scenes").json()[0]["ply_url"])
        self.assertEqual(self.client.get(f"/api/assessments/{aid}/findings").json()["hazards"], [])
        with patch.object(photos, "prepare", wraps=photos.prepare) as prepare, \
                patch.object(jev, "inspect", wraps=jev.inspect) as screen, \
                patch.object(reconstruction, "reconstruct", wraps=reconstruction.reconstruct) as recon, \
                patch.object(gemma, "assess", wraps=gemma.assess) as assess, \
                patch.object(avatar, "generate", wraps=avatar.generate) as generate:
            _, latest, stages = self.analyze(aid)
        self.assertEqual(latest["status"], "succeeded")
        for method in (prepare, screen, recon):
            method.assert_not_called()
        self.assertEqual((assess.call_count, generate.call_count), (1, 1))
        for stage in ("prepare", "jev", "reconstruct"):
            self.assertEqual(stages[stage]["origin_job_id"], job["id"])
        self.assertEqual(old_manifest.read_bytes(), original)

    def test_model_failure_exposes_local_scenes_and_retry_reuses_them(self):
        aid = self.create()
        with patch.object(gemma, "assess", side_effect=ExternalFailure("Synthetic provider failure")):
            first, failed, stages = self.analyze(aid)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["stage"], "gemma")
        failure_status = self.client.get(f"/api/jobs/{failed['id']}").json()
        self.assertEqual(failure_status["stage"], "gemma")
        self.assertEqual(failure_status["failed_stages"], ["gemma"])
        self.assertEqual(stages["geometry"]["status"], "blocked")
        self.assertEqual(stages["avatar"]["status"], "succeeded")
        value = self.client.get(f"/api/assessments/{aid}/bundle")
        self.assertEqual(value.status_code, 200, value.text)
        bundle = AssessmentBundle.model_validate(value.json())
        self.assertEqual(bundle.hazards, [])
        self.assertEqual(bundle.house.reconstruction_state, "ready")
        self.assertTrue(bundle.provenance["warnings"])
        room = bundle.rooms[0]
        self.assertEqual(self.client.get(room.ply_url).status_code, 200)
        manifest = self.client.get(room.manifest_url)
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest.json()["source_photos"][0]["sha256"],
                         self.assessment(aid)["photos"][0]["upright_sha256"])
        self.assertEqual(self.client.get(room.manifest_url, auth=("", "")).status_code, 401)
        self.assertEqual(self.client.post(f"/api/assessments/{aid}/reports", json=self.revisions(aid)).status_code, 409)
        with patch.object(reconstruction, "reconstruct", wraps=reconstruction.reconstruct) as recon:
            _, retried, stages = self.analyze(aid)
        recon.assert_not_called()
        self.assertEqual(retried["status"], "succeeded")
        self.assertEqual(stages["reconstruct"]["origin_job_id"], failed["id"])
        self.assertTrue((first["directory"] / "stages/gemma/error.json").is_file())

    def test_avatar_failure_preserves_photo_results_for_explicit_retry(self):
        aid = self.create()
        with patch.object(avatar, "generate", side_effect=ExternalFailure("Synthetic Blender timeout")):
            _, failed, stages = self.analyze(aid)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["stage"], "avatar")
        self.assertEqual(stages["gemma"]["status"], "succeeded")
        self.assertEqual(stages["geometry"]["status"], "succeeded")
        with patch.object(gemma, "assess", wraps=gemma.assess) as assess:
            _, retried, _ = self.analyze(aid)
        assess.assert_not_called()
        self.assertEqual(retried["status"], "succeeded")

    def test_multiple_independent_failures_keep_the_first_real_failure_stage(self):
        aid = self.create()
        with patch.object(gemma, "assess", side_effect=ExternalFailure("Synthetic model failure")), \
                patch.object(avatar, "generate", side_effect=ExternalFailure("Synthetic avatar failure")):
            _, failed, stages = self.analyze(aid)
        self.assertEqual(failed["stage"], "gemma")
        self.assertEqual(stages["geometry"]["status"], "blocked")
        status = self.client.get(f"/api/jobs/{failed['id']}").json()
        self.assertEqual(status["stage"], "gemma")
        self.assertEqual(set(status["failed_stages"]), {"gemma", "avatar"})

    def test_failed_reanalysis_does_not_expose_prior_findings_as_current(self):
        aid = self.create()
        _, initial, _ = self.analyze(aid)
        self.assertTrue(self.client.get(f"/api/assessments/{aid}/findings").json()["hazards"])
        # Change only a model input, so local stages remain reusable.
        original_fingerprint = worker.fingerprint
        def changed(stage, context, manifests):
            checksum, value = original_fingerprint(stage, context, manifests)
            return ("different-model-input", value) if stage == "gemma" else (checksum, value)
        with patch.object(worker, "fingerprint", side_effect=changed), \
                patch.object(gemma, "assess", side_effect=ExternalFailure("Synthetic failure on new attempt")):
            _, failed, _ = self.analyze(aid)
        self.assertEqual(failed["status"], "failed")
        row = self.assessment(aid)
        self.assertIsNone(row["analysis_job_id"])
        self.assertEqual(row["review_revision"], initial["review_revision"] + 1)
        self.assertEqual(self.client.get(f"/api/assessments/{aid}/bundle").json()["hazards"], [])

    def test_corrupted_cache_is_rebuilt_and_not_republished(self):
        aid = self.create()
        context, _, _ = self.analyze(aid)
        prepared = context["outputs"]["prepare"]["photos"][0]["prepared_path"]
        db.asset_path(self.settings, prepared).write_bytes(b"corrupted synthetic artifact")
        with patch.object(photos, "prepare", wraps=photos.prepare) as prepare:
            _, latest, stages = self.analyze(aid)
        self.assertEqual(latest["status"], "succeeded")
        self.assertEqual(prepare.call_count, 1)
        self.assertNotIn("origin_job_id", stages["prepare"])

    def test_cache_never_crosses_assessment_ownership(self):
        self.analyze(self.create())
        second = self.create()
        with patch.object(avatar, "generate", wraps=avatar.generate) as generate:
            self.analyze(second)
        self.assertEqual(generate.call_count, 1)

    def test_unvalidated_optional_anchor_abstains_without_discarding_the_hazard(self):
        aid = self.create()
        original = geometry.geometry
        def propose(context):
            output = original(context)
            hazard = context["outputs"]["gemma"]["hazards"][0]
            evidence = hazard["evidence"][0]
            output["anchors"] = [{"photo_id": evidence["photo_id"], "category": hazard["category"],
                "evidence_box": evidence["box"], "anchor": {
                    "scene_id": output["scenes"][0]["id"], "position_world": [0, 0, 0],
                    "method": "foreground_multiview", "source_photo_ids": [evidence["photo_id"]],
                    "uncertainty": "Deliberately unsupported synthetic proposal"}}]
            return output
        with patch.object(geometry, "geometry", side_effect=propose):
            _, job, _ = self.analyze(aid)
        self.assertEqual(job["status"], "succeeded")
        bundle = self.client.get(f"/api/assessments/{aid}/bundle").json()
        self.assertTrue(bundle["hazards"])
        self.assertTrue(all(not hazard["anchor"] for hazard in bundle["hazards"]))
        self.assertTrue(bundle["anchor_abstentions"])

    def test_shared_document_photo_budget_and_clear_empty_document_error(self):
        self.settings = replace(self.settings, max_assessment_bytes=1200, max_file_bytes=10_000)
        self.client.close()
        self.client = TestClient(create_app(self.settings))
        self.client.auth = ("reviewer", self.settings.reviewer_password)
        created = self.client.post("/api/assessments", json={
            "label": "Synthetic limits", "profile": {"description": "Fictional"},
            "profile_confirmed": True, "staged_data_confirmed": True, "processing_consent": True})
        aid = created.json()["id"]
        path = f"/api/assessments/{aid}/clinical-documents"
        empty = self.client.post(path, data={"expected_input_revision": 1},
                                 files={"file": ("empty.txt", b"", "text/plain")})
        self.assertEqual(empty.status_code, 422)
        self.assertIn("no extractable text", empty.json()["error"])
        uploaded = self.client.post(path, data={"expected_input_revision": 1},
                                    files={"file": ("fictional.txt", b"Synthetic fixture text. " + b"x" * 850, "text/plain")})
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        result = self.client.post(f"/api/assessments/{aid}/photos", data={
            "scene_label": "Room", "expected_input_revision": 2},
            files={"file": ("synthetic.png", picture(), "image/png")})
        self.assertEqual(result.status_code, 413, result.text)
        self.assertEqual(self.assessment(aid)["photos"], [])
        self.assertEqual(len(self.assessment(aid)["clinical_documents"]), 1)

    def test_room_link_api_rejects_an_inconsistent_triangle_without_a_revision_change(self):
        aid = self.create()
        for revision, label in ((2, "Hall"), (3, "Bedroom")):
            result = self.client.post(f"/api/assessments/{aid}/photos", data={
                "scene_label": label, "expected_input_revision": revision},
                files={"file": ("synthetic.png", picture(), "image/png")})
            self.assertEqual(result.status_code, 201, result.text)
        images = self.assessment(aid)["photos"]
        with db.connect(self.settings, write=True) as conn:
            for index, image in enumerate(images, 1):
                payload = {"status": "ready", "provenance": "live", "raw_coordinate_units": "relative",
                           "scale_state": "metric_calibrated", "scale_factor": index,
                           "calibration": {"measurement_id": f"synthetic-{image['scene_id']}", "scale_factor_m_per_unit": index}}
                conn.execute("UPDATE scenes SET payload=? WHERE id=?", (db.dumps(payload), image["scene_id"]))
        def link(a, b, x):
            matrix = np.eye(4)
            matrix[0, 3] = x
            return self.client.put(f"/api/assessments/{aid}/scene-links/{images[a]['scene_id']}/{images[b]['scene_id']}", json={
                **self.revisions(aid), "coordinate_space": "metric_scene_coordinates", "transform_from_to": matrix.tolist(),
                "overlap_photo_pairs": [[images[a]["id"], images[b]["id"]]],
                "residual": 0, "uncertainty": "Synthetic known transform"})
        self.assertEqual(link(0, 1, 2).status_code, 200)
        self.assertEqual(link(1, 2, 3).status_code, 200)
        self.assertEqual(link(0, 2, 0).status_code, 422)
        self.assertEqual(self.assessment(aid)["review_revision"], 2)
        self.assertEqual(link(0, 2, 5).status_code, 200)
        bundle = self.client.get(f"/api/assessments/{aid}/bundle").json()
        self.assertEqual(bundle["house"]["status"], "connected")
        self.assertEqual(bundle["house"]["reconstruction_state"], "ready")

    def test_fixture_smoke_entry_point_runs_api_review_release_and_privacy_checks(self):
        spec = importlib.util.spec_from_file_location("local_smoke", ROOT / "scripts/smoke_e2e.py")
        smoke = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(smoke)
        client, settings = self.client, self.settings
        class Session:
            auth = None
            def request(self, method, url, **kwargs):
                kwargs.pop("allow_redirects", None)
                kwargs.pop("timeout", None)
                if "/api/jobs/" in url:
                    job = worker.claim(settings)
                    if job:
                        worker.process_job(settings, job)
                return client.request(method, url, auth=self.auth, **kwargs)
            def post(self, url, **kwargs):
                return self.request("POST", url, **kwargs)
        with patch.dict(os.environ, {"REVIEWER_PASSWORD": self.settings.reviewer_password, "REVIEWER_USER": "reviewer"}), \
                patch.object(smoke.requests, "Session", return_value=Session()), \
                patch.object(smoke.requests, "get", side_effect=lambda url, **kw: client.get(url, auth=None)), \
                redirect_stdout(io.StringIO()):
            aid = smoke.run("http://testserver", "fixture")
        self.assertEqual(self.assessment(aid)["status"], "released")
        with patch.object(smoke.requests, "Session") as network:
            with self.assertRaisesRegex(RuntimeError, "allow-external"):
                smoke.run("http://testserver", "live")
            network.assert_not_called()

    def test_fictional_document_to_bundle_and_pdf_with_mocked_models(self):
        self.settings = replace(self.settings, gemma_backend="http", gemma_base_url="https://router.huggingface.co/v1",
                                gemma_provider="synthetic-provider", hf_token="synthetic-test-token")
        self.client.close()
        self.client = TestClient(create_app(self.settings))
        self.client.auth = ("reviewer", self.settings.reviewer_password)
        aid = self.create()
        text = "Uses a walker. Previous hip fracture is healed. No current hip fracture."
        uploaded = self.client.post(f"/api/assessments/{aid}/clinical-documents",
                                    data={"expected_input_revision": 2},
                                    files={"file": ("fictional.txt", text.encode(), "text/plain")})
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        document_id = uploaded.json()["id"]

        def consent():
            row = self.assessment(aid)
            result = self.client.patch(f"/api/assessments/{aid}", json={
                **row["inputs"], "external_processing_consent": True, "clinical_external_processing_consent": True,
                "expected_input_revision": row["input_revision"]})
            self.assertEqual(result.status_code, 200, result.text)

        calls = []
        def model(settings, schema, prompt, content, validate=None, audit_directory=None):
            calls.append(schema.__name__)
            if schema.__name__ == "ClinicalDraft":
                claims = [{"field": field, "value": value, "status": status, "source_document_id": document_id,
                           "page": 1, "quote": quote}
                          for field, value, status, quote in (
                              ("mobility_aid", "walker", "current", "Uses a walker."),
                              ("hip_condition", "healed fracture", "historical", "Previous hip fracture is healed."),
                              ("hip_condition", None, "negated", "No current hip fracture."))]
                value = {"claims": claims, "unknown_fields": ["vision"]}
            elif schema.__name__ == "VisualAssessmentProposal":
                photo_id = self.assessment(aid)["photos"][0]["id"]
                value = {"hazards": [{"category": "rug", "observation": "Synthetic visible mat.",
                         "priority": "important", "profile_fields": ["mobility_aid"], "guidance_ids": ["floor-rug"],
                         "uncertainty": "Mocked inference, not a safety assessment.",
                         "evidence": [{"photo_id": photo_id, "box_yxyx_1000": None}]}],
                         "coverage": [], "follow_up_requests": ["Inspect the fictional mat."]}
            else:
                supplied = json.loads(content)
                value = {"title": "Piano d’azione per la casa", "items": [
                    {"hazard_id": h["id"], **{key: h[key] for key in ("observation", "priority", "suggested_action")}}
                    for h in supplied["accepted"]]}
            result = schema.model_validate(value)
            if validate:
                validate(result)
            return result, [{"status": "mocked_contract_test"}]

        with patch.object(gemma, "complete", side_effect=model), patch("step_wise.pipeline.http.requests.post") as network:
            consent()
            draft = self.client.post(f"/api/assessments/{aid}/profile/draft", json=self.revisions(aid))
            self.assertEqual(draft.status_code, 200, draft.text)
            confirmed = self.client.put(f"/api/assessments/{aid}/profile/confirm", json={
                **self.revisions(aid), "profile": {"description": "Fictional walker user", "mobility_aid": "walker"},
                "claims": [{**claim, "reviewer_confirmed": True} for claim in draft.json()["claims"]]})
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            consent()
            _, job, _ = self.analyze(aid)
            self.assertEqual(job["status"], "succeeded")
            bundle = self.client.get(f"/api/assessments/{aid}/bundle").json()
            self.assertEqual({fact["status"] for fact in bundle["functional_profile"]["confirmed_facts"]},
                             {"current", "historical", "negated"})
            hazard = bundle["hazards"][0]
            self.assertEqual(self.client.put(f"/api/assessments/{aid}/reviews/{hazard['id']}", json={
                **self.revisions(aid), "decision": "approve", "note": "Simulated review only."}).status_code, 200)
            response = self.client.post(f"/api/assessments/{aid}/reports", json=self.revisions(aid))
            self.assertEqual(response.status_code, 202, response.text)
            worker.process_job(self.settings, worker.claim(self.settings))
            report = self.assessment(aid)["reports"][0]
            detail = self.client.get(f"/api/reports/{report['id']}").json()
            self.assertTrue(self.client.get(detail["pdf_url"]).content.startswith(b"%PDF"))
            self.assertEqual(report["status"], "draft")
            network.assert_not_called()
        self.assertEqual(calls, ["ClinicalDraft", "VisualAssessmentProposal", "DraftOutput"])


class Local3DContracts(unittest.TestCase):
    def test_room_scales_are_applied_before_rotated_translated_composition(self):
        scenes = {scene_id: {"status": "ready", "provenance": "live", "raw_coordinate_units": "relative",
                            "scale_state": "metric_calibrated", "scale_factor": scale,
                            "calibration": {"measurement_id": scene_id, "scale_factor_m_per_unit": scale}}
                  for scene_id, scale in (("a", 1), ("b", 2), ("c", .5))}
        ab = np.array([[0., -1, 0, 2], [1, 0, 0, -1], [0, 0, 1, .4], [0, 0, 0, 1]])
        bc = np.array([[1., 0, 0, -.7], [0, 0, -1, .5], [0, 1, 0, 1], [0, 0, 0, 1]])
        links = [{"from_scene_id": a, "to_scene_id": b, **bind_metric_link(scenes, a, b, {
            "coordinate_space": "metric_scene_coordinates", "residual_unit": "m", "transform_from_to": matrix.tolist()})}
            for a, b, matrix in (("a", "b", ab), ("b", "c", bc))]
        components, placements = compose_rooms(scenes, links)
        self.assertEqual(components, [["a", "b", "c"]])
        matrices = {p["scene_id"]: np.asarray(p["transform_to_reference"]) for p in placements}
        raw_a = np.array([1., 2, 3, 1])
        raw_b = np.diag([.5, .5, .5, 1]) @ ab @ raw_a
        raw_c = np.diag([2., 2, 2, 1]) @ bc @ ab @ raw_a
        np.testing.assert_allclose(matrices["a"] @ raw_a, matrices["b"] @ raw_b)
        np.testing.assert_allclose(matrices["a"] @ raw_a, matrices["c"] @ raw_c)
        np.testing.assert_allclose(matrices["b"], np.linalg.inv(ab) @ np.diag([2., 2, 2, 1]))
        self.assertEqual({p["coordinate_units"] for p in placements}, {"m"})

    def test_room_composition_preserves_transforms_and_rejects_bad_cycles(self):
        ab, bc = np.eye(4), np.eye(4)
        ab[0, 3], bc[2, 3] = 2, 3
        scenes = {scene_id: {"status": "ready", "provenance": "live", "raw_coordinate_units": "relative",
                            "scale_state": "metric_calibrated", "scale_factor": 1,
                            "calibration": {"measurement_id": scene_id, "scale_factor_m_per_unit": 1}}
                  for scene_id in ("a", "b", "c", "d")}
        def link(a, b, matrix):
            return {"from_scene_id": a, "to_scene_id": b, **bind_metric_link(scenes, a, b, {
                "coordinate_space": "metric_scene_coordinates", "residual_unit": "m", "transform_from_to": matrix.tolist()})}
        links = [link("a", "b", ab), link("b", "c", bc)]
        components, placements = compose_rooms(scenes, links)
        self.assertEqual(components, [["a", "b", "c"], ["d"]])
        c = next(p for p in placements if p["scene_id"] == "c")
        np.testing.assert_allclose(c["transform_to_reference"], np.linalg.inv(bc @ ab))
        compose_rooms(scenes, [*links, link("a", "c", bc @ ab)])
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            compose_rooms(scenes, [*links, link("a", "c", np.eye(4))])

    def test_foreground_anchor_validates_method_boxes_and_camera_baseline(self):
        hazard = Hazard(category="rug", observation="Synthetic feature", profile_relevance="Fictional",
                        profile_fields=[], priority="important", guidance_ids=["g"], uncertainty="Relative estimate",
                        suggested_action="Review", evidence=[{"photo_id": p, "box": [10, 10, 90, 90]} for p in ("a", "b")],
                        anchor={"scene_id": "room", "position_world": [0, 0, 4], "method": "foreground_multiview",
                                "source_photo_ids": ["a", "b"], "uncertainty": "Synthetic multiview support"})
        images = [{"photo_id": p, "world_to_camera": [[1, 0, 0, -offset], [0, 1, 0, 0], [0, 0, 1, 0]],
                   "intrinsics": [[100, 0, 50], [0, 100, 50], [0, 0, 1]],
                   "transform": {"scale_xy": [1, 1], "offset_xy": [0, 0]}}
                  for p, offset in (("a", 0), ("b", .5))]
        scene = {"status": "ready", "provenance": "live", "reconstruction_kind": "multiview",
                 "bounds": [[-1, -1, 1], [1, 1, 5]], "selected_photo_ids": ["a", "b"], "images": images}
        photo_map = {p: {"scene_id": "room", "payload": {"width": 100, "height": 100}} for p in ("a", "b")}
        def check(value=hazard, room=scene):
            return validate_hazard(value, photo_map, {"room": room}, {"g": {"category": "rug"}}, {}, {})
        check()
        for changes in ({"reconstruction_kind": "single_view_estimate"}, {"provenance": "fixture"},
                        {"selected_photo_ids": ["a"]},
                        {"images": [images[0], {**images[1], "world_to_camera": images[0]["world_to_camera"]}]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                check(room={**scene, **changes})
        no_box = hazard.model_copy(deep=True)
        no_box.evidence[1].box = None
        with self.assertRaises(ValueError):
            check(no_box)
        synthetic = hazard.model_copy(deep=True)
        synthetic.anchor.method = "synthetic_fixture"
        with self.assertRaises(ValueError):
            check(synthetic)

    def test_limits_are_loaded_and_nonpositive_values_rejected(self):
        env = {"VGGT_FRAME_CAP": "2", "STEP_WISE_MAX_PHOTOS": "4", "STEP_WISE_MAX_FILE_BYTES": "1234",
               "STEP_WISE_MAX_ASSESSMENT_BYTES": "5678", "STEP_WISE_MAX_PIXELS": "9000"}
        with patch.dict(os.environ, env, clear=True), patch("step_wise.config._load_project_env"), \
                patch("step_wise.config._local_vggt_defaults", return_value=("", "", "")):
            settings = Settings.from_env()
        self.assertEqual((settings.frame_cap, settings.max_photos, settings.max_file_bytes,
                          settings.max_assessment_bytes, settings.max_pixels), (2, 4, 1234, 5678, 9000))
        with self.assertRaises(ValueError):
            Settings(frame_cap=0)

    def test_rebranded_configuration_preserves_legacy_paths_and_limits(self):
        values = {"DATA": str(ROOT / "data/legacy"), "DATABASE": str(ROOT / "data/legacy/custom.sqlite3"),
                  "WORKER_LOCK": str(ROOT / "data/shared.lock"), "MAX_PHOTOS": "40", "MAX_FILE_BYTES": "1234",
                  "MAX_ASSESSMENT_BYTES": "5678", "MAX_PIXELS": "9000"}
        legacy = {f"SAFE_HOME_{key}": value for key, value in values.items()}
        current_values = {**values, "DATA": str(ROOT / "data/current"), "MAX_PHOTOS": "30"}
        current = {f"STEP_WISE_{key}": value for key, value in current_values.items()}
        for env, expected in ((legacy, values), (current, current_values), (legacy | current, current_values)):
            with self.subTest(keys=list(env)), patch.dict(os.environ, env, clear=True), \
                    patch("step_wise.config._load_project_env"), \
                    patch("step_wise.config._local_vggt_defaults", return_value=("", "", "")):
                settings = Settings.from_env()
                self.assertEqual(settings.data, Path(expected["DATA"]))
                self.assertEqual(settings.db_path, Path(expected["DATABASE"]))
                self.assertEqual(settings.worker_lock_path, Path(expected["WORKER_LOCK"]))
                self.assertEqual((settings.max_photos, settings.max_file_bytes, settings.max_assessment_bytes,
                                  settings.max_pixels), (int(expected["MAX_PHOTOS"]), 1234, 5678, 9000))

    def test_failed_migration_rolls_back_version_and_new_tables(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(data=Path(temporary))
            db.init(settings)
            with db.connect(settings, write=True) as conn:
                conn.execute("DROP TABLE schema_version")
                conn.execute("CREATE TABLE schema_version(version INTEGER PRIMARY KEY CHECK(version=1))")
                conn.execute("INSERT INTO schema_version VALUES(1)")
            schema = "CREATE TABLE migration_probe(id TEXT);\nTHIS IS INVALID;\n"
            with patch.object(Path, "read_text", return_value=schema):
                with self.assertRaises(sqlite3.OperationalError):
                    db.init(settings)
            with db.connect(settings) as conn:
                self.assertEqual(conn.execute("SELECT version FROM schema_version").fetchone()[0], 1)
                self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='migration_probe'").fetchone())

    def test_v2_multi_room_case_keeps_disconnected_components(self):
        generate_controls()
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as temporary:
            directory = Path(temporary)
            original = json.loads((ROOT / "demo/manifest.json").read_text())
            manifest = {**original, "schema_version": 2, "rooms": [{"id": "a", "label": "Kitchen"}, {"id": "b", "label": "Hall"}],
                        "photos": [{**{k: v for k, v in photo.items() if k != "scene_label"}, "scene_id": scene_id}
                                   for photo, scene_id in zip(original["photos"], ("a", "b"), strict=True)]}
            manifest_path = directory / "capture.json"
            manifest_path.write_text(db.dumps(manifest))
            case = json.loads((ROOT / "demo/cases/room-001.json").read_text())
            case.update({"schema_version": 2, "manifest": manifest_path.relative_to(ROOT).as_posix(),
                         "expectations": [{"id": "disconnected", "kind": "house_components", "component_sizes": [1, 1]},
                                          {"id": "kitchen", "kind": "scene_reconstruction", "scene_label": "Kitchen", "status": "ready"},
                                          {"id": "floor-unknown", "kind": "floor_state", "scene_label": "Hall", "floor_state": "unknown"}],
                         "manual_checks": []})
            path = directory / "case.json"
            path.write_text(db.dumps(case))
            settings = Settings(data=directory / "runtime")
            record, code = cases.run_case(settings, path, "fixture")
            self.assertEqual((record["status"], code), ("review_ready", 0), record)


if __name__ == "__main__":
    unittest.main()
