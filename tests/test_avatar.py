import io
import json
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from step_wise import db, worker
from step_wise.api import create_app
from step_wise.config import Settings
from step_wise.models import AssessmentBundle, AvatarArtifact
from step_wise.pipeline import avatar, avatar_blender

AVATAR_MESHES = ("low-poly", "eyebrow001", "eyelashes01", "teeth_base", "tongue01", "bob02",
                 "female_elegantsuit01", "shoes02")


def png_bytes(width=64, height=96):
    stream = io.BytesIO()
    Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(stream, "PNG")
    return stream.getvalue()


def glb_bytes(doc):
    payload = json.dumps(doc).encode()
    payload += b" " * (-len(payload) % 4)
    return (struct.pack("<4sII", b"glTF", 2, 20 + len(payload)) +
            struct.pack("<I4s", len(payload), b"JSON") + payload)


def avatar_glb(*node_names):
    return glb_bytes({"asset": {"version": "2.0"}, "meshes": [{"name": name} for name in AVATAR_MESHES],
                      "skins": [{}], "nodes": [{"name": name} for name in node_names]})


class AvatarWrapperTests(unittest.TestCase):
    def test_live_wrapper_uses_blender_without_forwarding_service_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "blender"
            binary.write_text("test executable placeholder")
            directory = root / "assessments" / "assessment" / "jobs" / "job"
            directory.mkdir(parents=True)
            settings = Settings(data=root, blender_bin=str(binary), blender_home="/isolated/home",
                                blender_user_config="/isolated/config",
                                blender_xdg_config_home="/isolated/xdg-config", blender_xdg_data_home="/isolated/xdg-data")
            quote = "La persona usa un deambulatore."
            context = {"settings": settings, "directory": directory,
                       "assessment_input": {"profile": {"description": "Profilo fittizio", "mobility_aid": "deambulatore"},
                                            "clinical_facts": [
                                                {"field": "mobility_aid", "value": "deambulatore", "status": "current",
                                                 "quote": quote, "page": 1, "reviewer_confirmed": True},
                                                {"field": "gender_identity", "value": "woman", "status": "current",
                                                 "quote": "La persona si identifica come donna.", "page": 1, "reviewer_confirmed": True},
                                                {"field": "hip_condition", "value": "frattura pregressa", "status": "historical",
                                                 "quote": "Pregressa frattura dell’anca.", "page": 1, "reviewer_confirmed": True},
                                            ],
                                            "profile_confirmed": True}}

            def blender(args, **kwargs):
                env = kwargs["env"]
                for key in ("HF_TOKEN", "JEV_API_KEY", "REVIEWER_PASSWORD", "HUGGINGFACE_HUB_TOKEN", "AWS_SECRET_ACCESS_KEY"):
                    self.assertNotIn(key, env)
                self.assertEqual(env["HOME"], "/isolated/home")
                self.assertEqual(env["BLENDER_USER_CONFIG"], "/isolated/config")
                self.assertEqual(env["XDG_CONFIG_HOME"], "/isolated/xdg-config")
                self.assertEqual(env["XDG_DATA_HOME"], "/isolated/xdg-data")
                if "--version" in args:
                    return SimpleNamespace(returncode=0, stdout="Blender 4.2.1")
                profile_arg, glb_arg, preview_arg = args[-3:]
                self.assertEqual(Path(args[args.index("--python") + 1]).name, "mpfb_export.py")
                child_input = json.loads(Path(profile_arg).read_text())
                self.assertEqual(child_input, {
                    "display_traits": [{"field": "mobility_aid", "value": "deambulatore", "status": "current"}],
                    "callouts": [{"id": "profile-callout-1", "field": "hip_condition", "status": "historical", "fact_index": 2}],
                })
                self.assertNotIn(quote, Path(profile_arg).read_text())
                self.assertEqual((Path(glb_arg).name, Path(preview_arg).name), ("avatar.glb", "avatar-preview.png"))
                Path(glb_arg).write_bytes(avatar_glb("walker", "profile-callout-1"))
                Path(preview_arg).write_bytes(png_bytes(64, 96))
                return SimpleNamespace(returncode=0, stdout="STEP_WISE_MPFB_VERSION=2.0.18")

            with patch.dict("os.environ", {"HF_TOKEN": "secret", "JEV_API_KEY": "secret", "REVIEWER_PASSWORD": "secret", "HUGGINGFACE_HUB_TOKEN": "secret", "AWS_SECRET_ACCESS_KEY": "secret"}), \
                 patch("step_wise.pipeline.avatar_blender.subprocess.run", side_effect=blender) as run:
                result = avatar_blender.generate_glb(context)

            self.assertEqual(run.call_count, 2)
            self.assertFalse((directory / "avatar-work" / "profile.json").exists())
            glb = root / result["path"]
            self.assertTrue(glb.is_file())
            self.assertNotIn(quote.encode(), glb.read_bytes())
            sidecar = root / result["profile_json_path"]
            self.assertIn(quote, sidecar.read_text())
            sidecar_value = json.loads(sidecar.read_text())
            self.assertEqual(sidecar_value["callouts"][0]["id"], "profile-callout-1")
            self.assertFalse(result["clinical_text_embedded"])
            self.assertTrue(result["rigged"])
            self.assertEqual(result["mpfb_version"], "2.0.18")
            self.assertEqual(result["generator"], "MPFB2/Blender")
            self.assertEqual((result["has_walker"], result["callout_ids"], result["style"]),
                             (True, ["profile-callout-1"], {"walker": True}))
            preview = root / result["preview_path"]
            self.assertEqual(preview.read_bytes(), png_bytes(64, 96))
            self.assertEqual((result["preview_width"], result["preview_height"]), (64, 96))
            self.assertEqual(result["preview_sha256"], db.digest(preview.read_bytes()))
            self.assertNotIn(quote, json.dumps({key: value for key, value in result.items() if key != "profile_json_path"}))

    def test_rejects_glb_with_clinical_text(self):
        from step_wise.pipeline.http import ExternalFailure

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "blender"
            binary.write_text("test executable placeholder")
            directory = root / "assessment" / "job"
            directory.mkdir(parents=True)
            settings = Settings(data=root, blender_bin=str(binary))
            quote = "pregressa frattura dell’anca"

            def blender(args, **kwargs):
                if "--version" in args:
                    return SimpleNamespace(returncode=0, stdout="Blender 4.2.1")
                Path(args[-2]).write_bytes(avatar_glb(quote, "profile-callout-1"))
                return SimpleNamespace(returncode=0, stdout="STEP_WISE_MPFB_VERSION=2.0.18")

            context = {"settings": settings, "directory": directory,
                       "assessment_input": {"profile": {"description": "synthetic"}, "clinical_facts": [
                           {"field": "hip_condition", "value": "frattura anca", "status": "historical",
                            "quote": quote, "source_document_id": "synthetic-document", "reviewer_confirmed": True}]}}
            with patch("step_wise.pipeline.avatar_blender.subprocess.run", side_effect=blender):
                with self.assertRaisesRegex(ExternalFailure, "clinical source text"):
                    avatar_blender.generate_glb(context)

    def test_blender_payload_uses_only_confirmed_current_functional_traits(self):
        facts = [
            {"field": "age_group", "value": "older_adult", "status": "current", "reviewer_confirmed": True,
             "quote": "source text", "page": 1},
            {"field": "mobility_aid", "value": "walker", "status": "historical", "reviewer_confirmed": True},
            {"field": "mobility_aid", "value": "rollator", "status": "current", "reviewer_confirmed": False},
            {"field": "gender_identity", "value": "woman", "status": "current", "reviewer_confirmed": True},
        ]
        self.assertEqual(avatar_blender.avatar_payload(facts), {
            "display_traits": [{"field": "age_group", "value": "older_adult", "status": "current"}],
            "callouts": [],
        })

    def test_live_avatar_requires_reviewed_profile(self):
        from step_wise.pipeline.avatar import generate
        from step_wise.pipeline.http import ExternalFailure

        with self.assertRaises(ExternalFailure):
            generate({"settings": Settings(avatar_backend="mpfb"), "assessment_input": {"profile_confirmed": False}})

    def test_avatar_backend_is_independent_from_gemma_fixture_mode(self):
        from step_wise.pipeline.avatar import generate

        context = {"settings": Settings(gemma_backend="fixture", avatar_backend="mpfb"),
                   "assessment_input": {"profile_confirmed": True}}
        with patch("step_wise.pipeline.avatar_blender.generate_glb", return_value={"status": "succeeded"}) as create:
            self.assertEqual(generate(context), {"status": "succeeded"})
        create.assert_called_once_with(context)


class AvatarApiTests(unittest.TestCase):
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
            "label": "Synthetic avatar", "profile": {"description": "Fictional walker user"},
            "profile_confirmed": True, "staged_data_confirmed": True, "processing_consent": True,
            "clinical_external_processing_consent": True,
        }).json()["id"]
        stream = io.BytesIO()
        Image.new("RGB", (96, 64), "#abbd92").save(stream, "PNG")
        result = self.client.post(f"/api/assessments/{aid}/photos", data={
            "scene_label": "Kitchen", "role": "overview", "expected_input_revision": 1,
        }, files={"file": ("synthetic.png", stream.getvalue(), "image/png")})
        self.assertEqual(result.status_code, 201, result.text)
        return aid

    def analyze(self, aid):
        row = self.client.get(f"/api/assessments/{aid}").json()
        queued = self.client.post(f"/api/assessments/{aid}/analyze", json={
            "expected_input_revision": row["input_revision"], "expected_review_revision": row["review_revision"]})
        self.assertEqual(queued.status_code, 202, queued.text)
        worker.process_job(self.settings, worker.claim(self.settings))

    @staticmethod
    def fake_avatar(context):
        assets = context["directory"] / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        (assets / "avatar.glb").write_bytes(avatar_glb("walker"))
        (assets / "avatar-preview.png").write_bytes(png_bytes())
        (assets / "avatar-profile.json").write_text("{}")

        def relative(name):
            return (assets / name).relative_to(context["settings"].data).as_posix()
        return {"status": "succeeded", "provenance": "live", "format": "glb", "illustrative_only": True,
                "clinical_text_embedded": False, "rigged": True, "has_walker": True, "callout_ids": [],
                "style": {"walker": True}, "preview_width": 64, "preview_height": 96,
                "path": relative("avatar.glb"), "preview_path": relative("avatar-preview.png"),
                "profile_json_path": relative("avatar-profile.json")}

    def test_published_avatar_is_served_as_preview_image_and_glb(self):
        aid = self.create()
        with patch.object(avatar, "generate", side_effect=self.fake_avatar):
            self.analyze(aid)
        response = self.client.get(f"/api/assessments/{aid}/avatar")
        self.assertEqual(response.status_code, 200, response.text)
        value = AvatarArtifact.model_validate(response.json())
        preview = self.client.get(value.preview_url)
        self.assertEqual((preview.status_code, preview.headers["content-type"], preview.content),
                         (200, "image/png", png_bytes()))
        glb = self.client.get(value.glb_url)
        self.assertEqual((glb.status_code, glb.headers["content-type"], glb.content[:4]), (200, "model/gltf-binary", b"glTF"))
        self.assertEqual(self.client.get(value.preview_url, auth=("", "")).status_code, 401)
        bundle = AssessmentBundle.model_validate(self.client.get(f"/api/assessments/{aid}/bundle").json())
        self.assertEqual(bundle.avatar, value)


if __name__ == "__main__":
    unittest.main()
