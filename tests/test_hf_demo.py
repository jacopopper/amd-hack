import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.export_hf_demo import export_demo


class StaticDemo(unittest.TestCase):
    def test_export_is_self_contained_and_never_loads_local_settings(self):
        with tempfile.TemporaryDirectory() as directory, patch("step_wise.config.Settings.from_env", side_effect=AssertionError("Do not read credentials")):
            root = Path(directory)
            export_demo(root)
            data = json.loads((root / "responses.json").read_text())
            self.assertEqual(len(data["/assessments"]), 1)
            aid = data["/assessments"][0]["id"]
            self.assertEqual(len(data[f"/assessments/{aid}/findings"]["hazards"]), 2)
            self.assertEqual(len(data[f"/assessments/{aid}"]["photos"]), 8)
            self.assertEqual(data["/health"]["modes"]["reconstruction"], "vggt_gaussian_splats_offline")
            capture = data[f"/assessments/{aid}/scenes"][0]["capture"]
            self.assertEqual(capture["viewer_url"], "/local-room-viewer.html")
            self.assertEqual(capture["views"], 39)
            self.assertEqual(len(capture["pins"]), 2)
            def check(value):
                if isinstance(value, dict):
                    for item in value.values():
                        check(item)
                elif isinstance(value, list):
                    for item in value:
                        check(item)
                elif isinstance(value, str):
                    self.assertFalse(value.startswith("/api/"))
                    if value.startswith("/demo/"):
                        self.assertTrue((root / value.removeprefix("/demo/")).is_file())
            check(data)
            self.assertFalse(list(root.rglob("*.sqlite3")))
