"""Generate explicitly synthetic control images; never replace a staged capture."""
import json

from PIL import Image, ImageDraw

from safe_home import db
from safe_home.config import ROOT
from safe_home.models import AssessmentInput
from safe_home.pipeline.photos import ingest


def generate_controls():
    directory = ROOT / "data/demo"
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(2):
        image = Image.new("RGB", (960, 640), "#e7e5da")
        draw = ImageDraw.Draw(image)
        offset = index * 30
        draw.polygon([(0, 360), (960, 360), (960, 640), (0, 640)], fill="#b8ab8e")
        draw.line([(0, 360), (960, 360)], fill="#958c78", width=3)
        draw.rectangle([690 + offset, 110, 860 + offset, 355], fill="#a5c3c8", outline="#6d827b", width=7)
        draw.rectangle([100 + offset, 190, 390 + offset, 370], fill="#527765")
        draw.rectangle([80 + offset, 280, 420 + offset, 400], fill="#527765")
        draw.polygon([(290, 410), (660, 410), (690, 550), (240, 550)], fill="#b4684c", outline="#81442f", width=5)
        draw.rectangle([80, 385, 220, 530], fill="#c5a663", outline="#806b3c", width=4)
        draw.text((20, 20), "SYNTHETIC CONTRACT CONTROL / NOT A HOME CAPTURE", fill="#243b31")
        draw.text((20, 45), f"VIEW {index + 1} / NO METRIC SCALE / NO MODEL INFERENCE", fill="#243b31")
        path = directory / f"synthetic-{index + 1}.png"
        if path.exists():
            continue
        image.save(path)
    return directory


def load_case(path):
    case = json.loads(path.read_text())
    manifest_path = (ROOT / case["manifest"]).resolve()
    if not manifest_path.is_relative_to(ROOT):
        raise ValueError("Case manifest must be in the project")
    manifest = json.loads(manifest_path.read_text())
    if case["schema_version"] != 1 or manifest["schema_version"] != 1:
        raise ValueError("Unsupported case schema")
    if len(case["photo_ids"]) != len(set(case["photo_ids"])):
        raise ValueError("Duplicate case photo ID")
    selected = [next(p for p in manifest["photos"] if p["id"] == pid) for pid in case["photo_ids"]]
    for p in selected:
        source = (ROOT / p["path"]).resolve()
        if not source.is_relative_to(ROOT) or not source.is_file() or db.digest(source.read_bytes()) != p["sha256"]:
            raise ValueError(f"Missing or changed dataset photo {p['id']}; run safe-home seed-controls")
    profiles = json.loads((ROOT / "demo/profiles.json").read_text())["profiles"]
    profile = next(p["profile"] for p in profiles if p["id"] == case["profile_id"])
    return case, manifest, selected, profile


def seed_case(settings, path):
    case, manifest, selected, profile = load_case(path)
    inputs = AssessmentInput(label=f"{case['id']} — {manifest['data_kind']}", profile=profile, profile_confirmed=True,
                             staged_data_confirmed=True, processing_consent=True, external_processing_consent=False)
    aid = db.create_assessment(settings, inputs.model_dump(mode="json"))
    mapping = {}
    for index, p in enumerate(selected):
        photo_id = ingest(settings, aid, (ROOT / p["path"]).read_bytes(), p["scene_label"], p["role"], index + 1)
        mapping[p["id"]] = photo_id
    return aid, mapping
