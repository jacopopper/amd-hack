import io
import json
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from scripts.attach_room_presentation import main as attach
from scripts.complete_monolocale import main as complete, validate_manual_source
from step_wise import db
from step_wise.config import Settings
from step_wise.pipeline.photos import ingest


class CompletionProvenanceTests(unittest.TestCase):
    def test_manual_selections_require_identical_analysis_and_ordered_photo_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            analysis = root / "analysis.npz"
            analysis.write_bytes(b"fixed analysis")
            rows = []
            for i in range(5):
                photo = root / f"photo-{i}.jpg"
                photo.write_bytes(f"photo {i}".encode())
                rows.append({"path": photo.name, "sha256": db.digest(photo.read_bytes())})
            scene = {"analysis_path": analysis.name, "analysis_sha256": db.digest(analysis.read_bytes()),
                     "images": [{"photo_id": str(i), "tensor_index": i} for i in range(5)],
                     "selected_photo_ids": [str(i) for i in range(5)]}
            with patch("scripts.complete_monolocale.SOURCE_ANALYSIS_SHA256", scene["analysis_sha256"]), \
                    patch("scripts.complete_monolocale.SOURCE_PHOTO_SHA256", tuple(row["sha256"] for row in rows)):
                self.assertEqual(len(validate_manual_source(root, scene, rows)), 5)
                with self.assertRaisesRegex(ValueError, "Refit"):
                    validate_manual_source(root, scene, rows[::-1])
                analysis.write_bytes(b"new reconstruction")
                with self.assertRaisesRegex(ValueError, "Refit"):
                    validate_manual_source(root, scene, rows)

    def test_missing_analysis_refuses_before_creating_output_or_importing_render_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(data=root)
            db.init(settings)
            aid = db.create_assessment(settings, {})
            with db.connect(settings, write=True) as conn:
                conn.execute("INSERT INTO scenes(id,assessment_id,label) VALUES('scene',?,'Room')", (aid,))
            output = root / "new-output"
            args = ["complete", "--assessment", aid, "--data", str(root), "--output", str(output)]
            with patch("sys.argv", args), self.assertRaisesRegex(ValueError, "Refit"):
                complete()
            self.assertFalse(output.exists())
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_text("previous output")
            with patch("sys.argv", args), self.assertRaisesRegex(ValueError, "already exists"):
                complete()
            self.assertEqual(marker.read_text(), "previous output")

    def test_attachment_checks_model_analysis_and_ordered_photos_but_allows_identical_source_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(data=root)
            db.init(settings)
            aid = db.create_assessment(settings, {})
            image = io.BytesIO()
            Image.new("RGB", (60, 90), "#9a8872").save(image, "PNG")
            for revision in [1, 2]:
                ingest(settings, aid, image.getvalue(), "Room", "overview", revision)
            analysis = root / "analysis.npz"
            analysis.write_bytes(b"unchanged source geometry")
            with db.connect(settings, write=True) as conn:
                scene = db.active_scenes(conn, aid)[0]
                photos = [{"id": row["id"], "sha256": row["sha256"]} for row in conn.execute(
                    "SELECT p.id,a.sha256 FROM photos p JOIN assets a ON a.id=p.asset_id WHERE p.assessment_id=? ORDER BY p.rowid", (aid,))]
                payload = {"analysis_path": analysis.name, "analysis_sha256": db.digest(analysis.read_bytes()),
                           "selected_photo_ids": [photo["id"] for photo in photos],
                           "images": [{"photo_id": photo["id"]} for photo in photos]}
                conn.execute("UPDATE scenes SET payload=? WHERE id=?", (db.dumps(payload), scene["id"]))
                conn.execute("UPDATE assessments SET analysis_job_id='current-job' WHERE id=?", (aid,))
            chunk = b'{"asset":{"version":"2.0"}}'
            chunk += b" " * (-len(chunk) % 4)
            model = root / "room.glb"
            model.write_bytes(struct.pack("<4sIIII", b"glTF", 2, 20 + len(chunk), len(chunk), 0x4E4F534A) + chunk)
            metadata = {"provenance": "photo_completed", "coordinate_space": "raw_scene_relative",
                        "assessment_id": aid, "scene_id": scene["id"], "input_revision": 1, "source_job_id": "original-job",
                        "model_bytes": model.stat().st_size, "model_sha256": db.digest(model.read_bytes()),
                        "source_analysis_sha256": payload["analysis_sha256"], "source_photos": photos,
                        "display_transform": np.eye(4).tolist()}
            metadata_path = root / "completion.json"
            args = ["attach", "--assessment", aid, "--scene", scene["id"], "--model", str(model),
                    "--provenance", "photo_completed", "--expected-input-revision", "3", "--expected-analysis-job", "current-job"]
            with patch("scripts.attach_room_presentation.Settings.from_env", return_value=settings), patch("sys.argv", args):
                for altered in [{"model_sha256": "changed"}, {"source_analysis_sha256": "changed"}, {"source_photos": photos[::-1]}]:
                    metadata_path.write_text(json.dumps(metadata | altered))
                    with self.subTest(altered=altered), self.assertRaises(ValueError):
                        attach()
                with db.connect(settings) as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM assets WHERE scope='derived'").fetchone()[0], 0)
                    self.assertNotIn("presentation", json.loads(db.active_scenes(conn, aid)[0]["payload"]))
                metadata_path.write_text(json.dumps(metadata))
                with redirect_stdout(io.StringIO()):
                    attach()
            with db.connect(settings) as conn:
                presentation = json.loads(db.active_scenes(conn, aid)[0]["payload"])["presentation"]
                self.assertEqual(presentation["source_job_id"], "current-job")
                self.assertEqual(presentation["completion_source_job_id"], "original-job")
                self.assertEqual(presentation["source_photos"], photos)


if __name__ == "__main__":
    unittest.main()
