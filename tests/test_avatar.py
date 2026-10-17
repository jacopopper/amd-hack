import json
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from step_wise.config import Settings
from step_wise.pipeline import avatar_blender


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
                child_input = json.loads(Path(args[-2]).read_text())
                self.assertEqual(child_input, {
                    "display_traits": [{"field": "mobility_aid", "value": "deambulatore", "status": "current"}],
                    "callouts": [{"id": "profile-callout-1", "field": "hip_condition", "status": "historical", "fact_index": 2}],
                })
                self.assertNotIn(quote, Path(args[-2]).read_text())
                doc = {"asset": {"version": "2.0"}, "meshes": [{"name": name} for name in (
                    "low-poly", "eyebrow001", "eyelashes01", "teeth_base", "tongue01", "short01",
                    "male_casualsuit01", "shoes01")], "skins": [{}], "nodes": [
                    {"name": "walker_frame"}, {"name": "profile-callout-1"}]}
                payload = json.dumps(doc).encode()
                payload += b" " * (-len(payload) % 4)
                Path(args[-1]).write_bytes(struct.pack("<4sII", b"glTF", 2, 20 + len(payload)) +
                                          struct.pack("<I4s", len(payload), b"JSON") + payload)
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
                Path(args[-1]).write_bytes(self.make_glb({
                    "asset": {"version": "2.0"}, "meshes": [{"name": name} for name in (
                        "low-poly", "eyebrow001", "eyelashes01", "teeth_base", "tongue01", "short01",
                        "male_casualsuit01", "shoes01")], "skins": [{}],
                    "nodes": [{"name": quote}, {"name": "profile-callout-1"}]
                }))
                return SimpleNamespace(returncode=0, stdout="STEP_WISE_MPFB_VERSION=2.0.18")

            context = {"settings": settings, "directory": directory,
                       "assessment_input": {"profile": {"description": "synthetic"}, "clinical_facts": [
                           {"field": "hip_condition", "value": "frattura anca", "status": "historical",
                            "quote": quote, "source_document_id": "synthetic-document", "reviewer_confirmed": True}]}}
            with patch("step_wise.pipeline.avatar_blender.subprocess.run", side_effect=blender):
                with self.assertRaisesRegex(ExternalFailure, "clinical source text"):
                    avatar_blender.generate_glb(context)

    @staticmethod
    def make_glb(doc):
        payload = json.dumps(doc).encode()
        payload += b" " * (-len(payload) % 4)
        return (struct.pack("<4sII", b"glTF", 2, 20 + len(payload)) +
                struct.pack("<I4s", len(payload), b"JSON") + payload)

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


if __name__ == "__main__":
    unittest.main()
