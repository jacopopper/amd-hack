"""Export the remote judge snapshot and its reconstruction references for the static demo."""
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from step_wise import snapshot, db
from step_wise.api import create_app
from step_wise.config import ROOT, Settings


def export_demo(destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="stepwise-public-demo-") as work:
        local = Settings(data=Path(work))  # Never load .env or the working patient database.
        db.init(local)
        aid = snapshot.clone_case(local)
        routes = ["/health", "/assessments", f"/assessments/{aid}",
                  f"/assessments/{aid}/findings", f"/assessments/{aid}/scenes", f"/assessments/{aid}/avatar"]
        with TestClient(create_app(local)) as client:
            responses = {}
            for route in routes:
                response = client.get(f"/api{route}")
                response.raise_for_status()
                responses[route] = response.json()
            responses["/health"]["demo"] = True
            responses["/health"]["modes"] = {"reconstruction": "vggt_gaussian_splats_offline", "jev": "recorded_live", "gemma": "not_run", "avatar": "fixture"}
            assets = {}

            def rewrite(value):
                if isinstance(value, dict):
                    return {key: rewrite(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [rewrite(item) for item in value]
                if isinstance(value, str) and value.startswith("/api/assets/"):
                    if value not in assets:
                        asset = client.get(value)
                        asset.raise_for_status()
                        media = asset.headers["content-type"].split(";")[0]
                        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "model/gltf-binary": ".glb", "application/json": ".json"}.get(media, ".bin")
                        relative = f"assets/{value.rsplit('/', 1)[1]}{suffix}"
                        target = destination / relative
                        target.parent.mkdir(exist_ok=True)
                        target.write_bytes(asset.content)
                        assets[value] = f"/demo/{relative}"
                    return assets[value]
                return value

            data = rewrite(responses)
            serialized = json.dumps(data, ensure_ascii=False, indent=2)
            assert "/api/assets/" not in serialized
            assert str(ROOT) not in serialized and work not in serialized
            (destination / "responses.json").write_text(serialized + "\n")
            (destination / "provenance.json").write_text(json.dumps({
                "source": "demo/snapshot/case.json", "mode": "offline_capture_recorded_jev_findings",
                "snapshot_sha256": db.digest((snapshot.SNAPSHOT / "case.json").read_bytes()),
                "reconstruction": {"path": "/capture-data/room.ksplat", "sha256": db.digest((ROOT / "frontend/public/capture-data/room.ksplat").read_bytes()), "views": 39},
                "live_inference": False, "clinical_validation": False,
                "checks": "Real offline reconstruction; fictional patient; findings recorded from live TypeSafe Jev runs (pitch/jev-results/2026-10-17/room-capture). Not clinically validated.",
                "sha256": {str(p.relative_to(destination)): db.digest(p.read_bytes()) for p in destination.rglob("*") if p.is_file()},
            }, indent=2) + "\n")
            print(f"Exported {len(routes)} read responses and {len(assets)} assets to {destination}")


if __name__ == "__main__":
    # Generate in an empty directory, so reruns never publish stale assets.
    destination = ROOT / "frontend/public/demo"
    with TemporaryDirectory(prefix="stepwise-demo-export-") as temporary:
        export_demo(Path(temporary))
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(temporary, destination)
    print("Ready: frontend/public/demo")
