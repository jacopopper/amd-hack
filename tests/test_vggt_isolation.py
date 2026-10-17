import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from step_wise import db
from step_wise.config import Settings
from step_wise.pipeline.http import ExternalFailure
from step_wise.pipeline.reconstruction import live_scene


class IsolatedVGGTTests(unittest.TestCase):
    def context(self, directory):
        root = Path(directory)
        checkpoint = root / "model.pt"
        checkpoint.write_bytes(b"pinned test checkpoint")
        python = root / "vggt-python"
        python.write_text("placeholder executable")
        data = root / "data"
        work = data / "assessments" / "assessment" / "jobs" / "job"
        work.mkdir(parents=True)
        photo = data / "assessments" / "assessment" / "photo.jpg"
        photo.parent.mkdir(parents=True, exist_ok=True)
        photo.write_bytes(b"photo bytes")
        settings = Settings(data=data, recon_backend="vggt", vggt_python=str(python),
                            vggt_checkpoint=str(checkpoint), vggt_checkpoint_sha256=db.digest(checkpoint.read_bytes()),
                            vggt_commit="pinned-commit")
        selected = [{"id": "photo-1", "path": "assessments/assessment/photo.jpg", "scene_id": "room-1", "role": "overview",
                     "payload": {"private": "must not cross the process boundary"}}]
        return {"settings": settings, "directory": work}, selected, data

    def test_calls_isolated_python_with_minimal_request_and_checks_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            context, selected, data = self.context(temporary)
            result_value = {"id": "room-1", "status": "ready", "checkpoint_sha256": context["settings"].vggt_checkpoint_sha256,
                            "vggt_commit": "pinned-commit", "reconstruction_kind": "single_view_estimate",
                            "anchor_eligibility": "abstain_single_view", "scale_state": "unknown", "scale_factor": None,
                            "selected_photo_ids": ["photo-1"], "images": [{}],
                            "runtime": {"gpu": "NVIDIA GeForce RTX 5090", "cuda": "12.8", "hip": None, "elapsed_seconds": 7.5}}

            def run(args, **kwargs):
                self.assertEqual(args[0], context["settings"].vggt_python)
                self.assertEqual(args[1:3], ["-m", "step_wise.pipeline.vggt_worker"])
                for key in ("HF_TOKEN", "JEV_API_KEY", "REVIEWER_PASSWORD", "AWS_SECRET_ACCESS_KEY"):
                    self.assertNotIn(key, kwargs["env"])
                request = json.loads(Path(args[-1]).read_text())
                self.assertEqual(request["photos"], [{key: selected[0][key] for key in ("id", "path", "scene_id", "role")}])
                self.assertNotIn("payload", json.dumps(request))
                assets = context["directory"] / "assets"
                assets.mkdir()
                analysis = assets / "analysis.npz"
                ply = assets / "scene.ply"
                analysis.write_bytes(b"npz")
                ply.write_bytes(b"ply")
                result_value["analysis_path"] = analysis.relative_to(data).as_posix()
                result_value["ply_path"] = ply.relative_to(data).as_posix()
                result_value["analysis_sha256"] = db.digest(analysis.read_bytes())
                return SimpleNamespace(returncode=0, stdout=json.dumps(result_value), stderr="")

            with patch.dict("os.environ", {"HF_TOKEN": "secret", "JEV_API_KEY": "secret", "REVIEWER_PASSWORD": "secret", "AWS_SECRET_ACCESS_KEY": "secret"}), \
                 patch("step_wise.pipeline.reconstruction.subprocess.run", side_effect=run) as process:
                result = live_scene(context, {"id": "room-1"}, selected)

            self.assertEqual(result["status"], "ready")
            self.assertEqual(process.call_args.kwargs["timeout"], 900)
            self.assertEqual(Path(process.call_args.kwargs["cwd"]), Path(__file__).resolve().parents[1])

    def test_timeout_and_gpu_oom_are_explicit_and_do_not_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            context, selected, _ = self.context(temporary)
            with patch("step_wise.pipeline.reconstruction.subprocess.run", side_effect=subprocess.TimeoutExpired("python", 900)) as process:
                with self.assertRaisesRegex(ExternalFailure, "timeout"):
                    live_scene(context, {"id": "room-1"}, selected)
                self.assertEqual(process.call_count, 1)
            with patch("step_wise.pipeline.reconstruction.subprocess.run", return_value=SimpleNamespace(
                    returncode=1, stdout="", stderr=json.dumps({"error_category": "OutOfMemoryError", "message": "redacted"}))) as process:
                with self.assertRaisesRegex(ExternalFailure, "CUDA ran out of memory"):
                    live_scene(context, {"id": "room-1"}, selected)
                self.assertEqual(process.call_count, 1)


if __name__ == "__main__":
    unittest.main()
