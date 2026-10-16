#!/usr/bin/env python3
"""Explicit hardware preflight and live case smoke. Never provisions GPUs or downloads models."""
import argparse
import json
import platform
import sys
from pathlib import Path

from safe_home import cases, db, worker
from safe_home.config import ROOT, Settings


def hardware():
    result = {"at": db.now(), "python": sys.version, "platform": platform.platform(), "kfd_present": Path("/dev/kfd").exists(), "status": "blocked"}
    try:
        import torch
        result.update({"torch": torch.__version__, "hip": torch.version.hip, "cuda": torch.version.cuda, "device_count": torch.cuda.device_count()})
        if torch.cuda.is_available():
            p = torch.cuda.get_device_properties(0)
            result.update({"device_name": p.name, "total_bytes": p.total_memory, "free_total_bytes": list(torch.cuda.mem_get_info()), "status": "ready" if torch.version.hip else "cuda_development_only"})
    except ImportError:
        result["reason"] = "PyTorch is absent from the CPU environment; use the separate validated ROCm worker"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hardware-only", action="store_true")
    parser.add_argument("--case", type=Path, default=ROOT / "demo/cases/room-001.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = Settings.from_env()
    output = args.output or settings.data / "gpu-smoke" / db.uid()
    output.mkdir(parents=True, exist_ok=False)
    identity = hardware()
    worker.atomic_json(output / "hardware.json", identity)
    result = {"status": "blocked", "hardware": "hardware.json", "modes": settings.modes(), "quality": "manual_pending", "limitations": ["A local AMD device does not prove that a remote Gemma endpoint ran on that device. Preserve the vLLM container/hardware manifest separately."]}
    code = 3
    if identity["status"] != "ready":
        result["reason"] = "Compatible AMD/ROCm execution is unavailable; no fixture or CUDA output counts as AMD smoke success"
    elif args.hardware_only:
        result["status"], code = "hardware_ready", 0
    else:
        run, code = cases.run_case(settings, args.case.resolve(), "live")
        result.update({"case_run": run["id"], "status": run["status"], "error": run.get("error"), "modes": run["modes"]})
        directory = cases.find_run(settings, run["id"])
        reconstruction = directory / "stages/reconstruct/output.json"
        if reconstruction.exists():
            scenes = json.loads(reconstruction.read_text())["scenes"]
            if not scenes or any(s["status"] != "ready" or not s.get("runtime", {}).get("hip") for s in scenes):
                result["status"], code = "failed_reconstruction", 1
        result["remaining_smoke_gates"] = ["Single-image and multi-image known-target checks", "Visual coherence and source pin inspection", "vLLM identity and co-resident memory sampling under the exact container schedule"]
    worker.atomic_json(output / "result.json", result)
    print(json.dumps({"output": str(output), **result}, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
