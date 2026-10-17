import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image

from scripts.reconstruct_capture import capture_masks, prepare, seed_points


class CaptureReconstructionTests(unittest.TestCase):
    def test_masks_exclude_padding_without_dropping_photos(self):
        with tempfile.TemporaryDirectory() as tmp:
            scene = {"images": [{"photo_id": name, "transform": {"content_box": [63, 0, 455, 518]}}
                                for name in ["one", "two"]]}
            masks = capture_masks(Path(tmp), scene)
            self.assertEqual(masks.shape, (2, 518, 518))
            self.assertFalse(masks[:, :, :63].any())
            self.assertFalse(masks[:, :, 455:].any())
            self.assertTrue(masks[:, :, 63:455].all())

    def test_person_mask_only_affects_named_photo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "masks.json").write_text(json.dumps({"one": [[[0, 0], [1, 0], [1, .5], [0, .5]]]}))
            scene = {"images": [{"photo_id": name, "transform": {"content_box": [63, 0, 455, 518]}}
                                for name in ["one", "two"]]}
            masks = capture_masks(root, scene)
            self.assertFalse(masks[0, :259].any())
            self.assertTrue(masks[0, 300:, 63:455].all())
            self.assertTrue(masks[1, :, 63:455].all())

    def test_seeds_use_every_view_and_reject_invalid_depth(self):
        shape = (3, 90, 90)
        points = np.zeros((*shape, 3), dtype=np.float32)
        for i in range(3):
            points[i, ..., 0] = i
        depth = np.ones((*shape, 1), dtype=np.float32)
        depth[:, :9] = 0
        colors = points.copy()
        analysis = {"world_points": points, "confidence": np.ones(shape), "depth": depth}
        masks = np.ones(shape, dtype=bool)
        xyz, rgb = seed_points(analysis, masks, colors)
        self.assertEqual(set(xyz[:, 0]), {0, 1, 2})
        self.assertEqual(len(xyz), 3 * 27 * 30)
        self.assertEqual(xyz.dtype, np.float32)
        np.testing.assert_array_equal(xyz, rgb)
        xyz2, _ = seed_points(analysis, masks, colors, maximum=1200)
        self.assertEqual(len(xyz2), 1200)
        self.assertEqual(set(xyz2[:, 0]), {0, 1, 2})

    def test_insufficient_seeds_fail_instead_of_inventing_geometry(self):
        analysis = {"world_points": np.zeros((1, 9, 9, 3)), "confidence": np.ones((1, 9, 9)),
                    "depth": np.ones((1, 9, 9, 1))}
        with self.assertRaisesRegex(ValueError, "Too few"):
            seed_points(analysis, np.ones((1, 9, 9), bool), np.zeros((1, 9, 9, 3)))

    def test_preparation_is_upright_metadata_free_and_source_preserving(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "original.jpg"
            exif = Image.Exif()
            exif[274] = 6
            exif[270] = "private metadata"
            Image.new("RGB", (40, 20), "red").save(source, exif=exif)
            original = source.read_bytes()
            with patch.dict(sys.modules, {"pillow_heif": SimpleNamespace(register_heif_opener=lambda: None)}):
                prepare(root / "capture", [source])
            self.assertEqual(source.read_bytes(), original)
            capture = json.loads((root / "capture/capture.json").read_text())
            photo = capture["photos"][0]
            self.assertEqual(photo["upright_source_size"], [20, 40])
            self.assertFalse(capture["external_processing"])
            with Image.open(root / "capture" / photo["path"]) as image:
                self.assertEqual(image.size, (20, 40))
                self.assertFalse(image.getexif())
            with patch.dict(sys.modules, {"pillow_heif": SimpleNamespace(register_heif_opener=lambda: None)}):
                with self.assertRaisesRegex(ValueError, "already exists"):
                    prepare(root / "capture", [source])

    def test_duplicate_input_path_is_not_a_second_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "image.jpg"
            Image.new("RGB", (20, 20)).save(source)
            with patch.dict(sys.modules, {"pillow_heif": SimpleNamespace(register_heif_opener=lambda: None)}):
                with self.assertRaisesRegex(ValueError, "Repeated input"):
                    prepare(root / "capture", [source, source])
