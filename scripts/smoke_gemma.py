#!/usr/bin/env python3
"""One explicit non-clinical Hugging Face image/JSON smoke, with a pinned provider."""
import argparse
import base64
import io
import json
from dataclasses import replace
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from PIL import Image, ImageDraw

from step_wise import db, worker
from step_wise.config import Settings
from step_wise.models import StrictModel
from step_wise.pipeline.gemma import complete
from step_wise.pipeline.http import ExternalFailure


class VisualSmoke(StrictModel):
    shape: Literal["square", "circle", "triangle"]
    shape_color: Literal["red", "blue", "green", "yellow"]
    background_color: Literal["red", "blue", "green", "yellow"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=int, choices=range(10, 121), default=60)
    parser.add_argument("--provider", help="Explicit provider for this standalone attempt; no fallback")
    args = parser.parse_args()
    settings = replace(Settings.from_env(), gemma_timeout_seconds=args.timeout)
    if args.provider:
        settings = replace(settings, gemma_provider=args.provider)
    if urlsplit(settings.gemma_base_url).hostname != "router.huggingface.co":
        parser.error("This smoke requires the configured Hugging Face router")
    output = args.output or settings.data / "live-tests" / "gemma-smoke" / db.uid()
    output.mkdir(parents=True, exist_ok=False)
    picture = Image.new("RGB", (256, 256), "blue")
    ImageDraw.Draw(picture).rectangle((64, 64, 192, 192), fill="red")
    stream = io.BytesIO()
    picture.save(stream, "PNG")
    picture.save(output / "input.png")
    content = [{"type": "text", "text": "Identify the central shape, its color, and the background color in this synthetic image."},
               {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()}}]
    result = {"at": db.now(), "model": settings.gemma_model, "provider": settings.gemma_provider,
              "input_sha256": db.digest(stream.getvalue()), "input_kind": "project_generated_synthetic_nonclinical"}
    try:
        value, attempts = complete(settings, VisualSmoke,
            "Inspect the image and return only the requested JSON object. Do not include reasoning or commentary.",
            content, audit_directory=output / "calls")
        expected = {"shape": "square", "shape_color": "red", "background_color": "blue"}
        result.update({"status": "passed" if value.model_dump() == expected else "failed_visual_check",
                       "output": value.model_dump(), "attempts": attempts})
    except ExternalFailure as exc:
        result.update({"status": "failed", "error": str(exc)})
    worker.atomic_json(output / "result.json", result)
    print(json.dumps({"directory": str(output), **result}, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
