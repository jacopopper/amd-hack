"""Publish the reviewed static artifact; credentials stay in the deploy process."""
import os
import shutil
import subprocess
from pathlib import Path

import requests

from step_wise.config import ROOT, Settings

stage = ROOT / "dist/hf-space"
# Deployment explicitly uses .env; an inherited token may be an older read-only one.
os.environ.pop("HF_TOKEN", None)
settings = Settings.from_env()
if not settings.hf_token:
    raise SystemExit("HF_TOKEN is missing")
assert (stage / "README.md").is_file() and (stage / "demo/responses.json").is_file(), "Stage the demo first"
for path in stage.rglob("*"):
    if path.is_file():
        payload = path.read_bytes()
        for secret in (settings.hf_token, settings.gemma_api_key, settings.jev_api_key):
            if secret and secret.encode() in payload:
                raise SystemExit(f"Credential found in upload artifact: {path.relative_to(stage)}")
headers = {"Authorization": f"Bearer {settings.hf_token}"}
identity = requests.get("https://huggingface.co/api/whoami-v2", headers=headers, timeout=30)
identity.raise_for_status()
owner = identity.json()["name"]
assert owner == "jacopopper", "Deployment token owner changed; review destination"
repo = f"{owner}/step-wise-demo"
env = {**os.environ, "HF_TOKEN": settings.hf_token}
info = requests.get(f"https://huggingface.co/api/spaces/{repo}", headers=headers, timeout=30)
if info.status_code == 404:
    subprocess.run(["hf", "repos", "create", repo, "--type", "space", "--space-sdk", "static", "--public"], env=env, check=True)
else:
    info.raise_for_status()
    data = info.json()
    assert data.get("sdk") == "static" and not data.get("private"), "Existing Space is not the expected public static demo"
    assert data.get("cardData", {}).get("title") == "Step Wise Demo", "Space name is already in use by a different app"
# hf 1.8 upload calls create_repo(space_sdk="gradio") even for an existing Static
# Space. Use its installed SDK directly to avoid that erroneous paid-plan check.
hub_python = Path(shutil.which("hf")).resolve().parent / "python"
subprocess.run([str(hub_python), "-c",
                "import sys; from huggingface_hub import HfApi; "
                "print(HfApi().upload_folder(repo_id=sys.argv[1], folder_path=sys.argv[2], "
                "repo_type='space', delete_patterns=['demo/*', '_next/*', 'pazienti/*', 'capture/*'], "
                "commit_message='Deploy remote 8be9805 room reconstruction and review UI'))",
                repo, str(stage)], env=env, check=True)
print(f"Published https://huggingface.co/spaces/{repo}")
