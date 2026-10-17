"""Isolated CUDA process entry point for live VGGT inference."""

import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

from step_wise.config import Settings
from step_wise.pipeline.http import ExternalFailure
from step_wise.pipeline.reconstruction import live_scene_local


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        return 2
    try:
        request = json.loads(Path(args[0]).read_text())
        pin = request["vggt"]
        settings = Settings(data=Path(request["data"]), recon_backend="vggt",
                            vggt_checkpoint=pin["checkpoint"], vggt_checkpoint_sha256=pin["checkpoint_sha256"],
                            vggt_commit=pin["commit"], vggt_timeout_seconds=pin["timeout_seconds"])
        with redirect_stdout(sys.stderr):
            output = live_scene_local({"settings": settings, "directory": Path(request["directory"])},
                                      request["scene"], request["photos"])
        sys.stdout.write(json.dumps(output, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
        return 0
    except Exception as exc:
        try:
            import torch
            oom = isinstance(exc, torch.cuda.OutOfMemoryError)
        except Exception:
            oom = False
        message = ("CUDA ran out of memory; reduce the explicitly selected frame set."
                   if oom else str(exc) if isinstance(exc, ExternalFailure)
                   else "VGGT inference failed; inspect the pinned isolated runtime.")
        sys.stderr.write(json.dumps({"error_category": type(exc).__name__, "message": message}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
