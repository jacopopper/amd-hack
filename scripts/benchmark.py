#!/usr/bin/env python3
"""Collect actual live room-001 attempts. Fixture quality and costs are never substituted."""
import argparse
import json
from pathlib import Path

from safe_home import cases, db, worker
from safe_home.config import ROOT, Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--case", type=Path, default=ROOT / "demo/cases/room-001.json")
    parser.add_argument("--repeats", type=int, choices=range(1, 6), default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = Settings.from_env()
    case = json.loads(args.case.read_text())
    if (ROOT / case["manifest"]).resolve() != args.manifest.resolve():
        parser.error("Case must reference the selected manifest")
    output = args.output or settings.data / "benchmarks" / db.uid()
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for _ in range(args.repeats):
        run, exit_code = cases.run_case(settings, args.case.resolve(), "live")
        directory = cases.find_run(settings, run["id"])
        stages = run.get("stages", {})
        rows.append({"run_id": run["id"], "status": run["status"], "exit_code": exit_code, "input_hash": run["input_hash"], "runtime": run["runtime"], "modes": run["modes"],
                     "stage_seconds": {s: m.get("elapsed_seconds") for s, m in stages.items()}, "checks": cases.read(directory / "checks.json"),
                     "quality": "manual_pending", "cost_usd": None, "cost_status": "unknown — dated provider rates, measured token usage and provisioned/idle time must be reconciled"})
    result = {"schema_version": 1, "at": db.now(), "manifest_sha256": db.digest(args.manifest.read_bytes()), "case_version": case["version"],
              "attempts": len(rows), "review_ready": sum(r["status"] == "review_ready" for r in rows), "blocked": sum(r["status"] == "blocked" for r in rows), "failures": sum(r["status"] == "failed" for r in rows), "runs": rows,
              "pending": ["Held-out captures and reviewed detection/localization labels", "Jev-only / Gemma-only / combined ablation", "Independent tape-measure evaluation", "Dated cost sources and provisioned GPU accounting", "Warm/cold and resident-vLLM peak memory observations"],
              "scope": "Single-case execution accounting, not general/clinical accuracy. Failure and blocked denominators are retained."}
    worker.atomic_json(output / "results.json", result)
    print(json.dumps({"output": str(output / "results.json"), "attempts": result["attempts"], "blocked": result["blocked"], "failures": result["failures"]}, indent=2))
    return 3 if result["blocked"] else 1 if result["failures"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
