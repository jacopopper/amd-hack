import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from step_wise import capture_case, cases, db, snapshot, worker
from step_wise.config import ROOT, Settings
from step_wise.demo import generate_controls, load_case, seed_case


def main(argv=None):
    parser = argparse.ArgumentParser(description="Step Wise staged-data development tools")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("seed-controls")
    seed = commands.add_parser("seed")
    seed.add_argument("--case", type=Path, default=ROOT / "demo/cases/room-001.json")
    reset = commands.add_parser("reset", help="Delete only an explicitly selected synthetic development assessment")
    reset.add_argument("--assessment", required=True)
    export = commands.add_parser("export-demo", help="Freeze an analysed assessment as the public demo case in demo/snapshot")
    export.add_argument("--assessment", required=True)
    commands.add_parser("import-capture", help="Create a reviewable case from the reconstructed room in frontend/public/capture-data")
    work = commands.add_parser("worker")
    work.add_argument("--once", action="store_true")
    cleanup = commands.add_parser("cleanup", help="Dry run unless --execute is supplied")
    cleanup.add_argument("--older-than-days", type=int, default=30)
    cleanup.add_argument("--execute", action="store_true")
    case = commands.add_parser("case").add_subparsers(dest="action", required=True)
    run = case.add_parser("run")
    run.add_argument("case_path", type=Path)
    run.add_argument("--mode", choices=("fixture", "live"), required=True)
    run.add_argument("--stage", choices=worker.STAGES)
    run.add_argument("--reuse-run")
    for command in ("inspect", "check"):
        case.add_parser(command).add_argument("--run", required=True)
    case.add_parser("baseline").add_argument("--run", required=True)
    attest = case.add_parser("attest")
    attest.add_argument("--run", required=True)
    attest.add_argument("--check", required=True)
    attest.add_argument("--note", required=True)
    compare = case.add_parser("compare")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--candidate", required=True)
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    code = 0
    try:
        if args.command == "init":
            db.init(settings)
            result = {"status": "initialized", "modes": settings.modes()}
        elif args.command == "seed-controls":
            generate_controls()
            load_case(ROOT / "demo/cases/room-001.json")
            result = {"status": "synthetic controls generated and checksums verified", "real_capture": "pending"}
        elif args.command == "seed":
            db.init(settings)
            aid, mapping = seed_case(settings, args.case)
            result = {"assessment_id": aid, "photo_mapping": mapping, "provenance": "fixture"}
        elif args.command == "reset":
            _, manifest, photos, _ = load_case(ROOT / "demo/cases/room-001.json")
            if manifest["data_kind"] != "synthetic_fixture":
                raise ValueError("Reset is limited to the synthetic development dataset")
            with db.connect(settings) as conn:
                row = db.assessment(conn, args.assessment)
                hashes = {json.loads(p[0])["raw_sha256"] for p in conn.execute("SELECT payload FROM photos WHERE assessment_id=?", (args.assessment,))}
                if not hashes or not hashes <= {p["sha256"] for p in photos} or not json.loads(row["input_json"])["label"].endswith("— synthetic_fixture"):
                    raise ValueError("Selected assessment is not the seeded synthetic development dataset")
            db.delete_assessment(settings, args.assessment)
            result = {"status": "deleted", "assessment_id": args.assessment, "scope": "synthetic development only"}
        elif args.command == "export-demo":
            result = snapshot.export_case(settings, args.assessment)
        elif args.command == "import-capture":
            result = capture_case.import_capture(settings)
        elif args.command == "worker":
            worker.run(settings, once=args.once)
            result = {"status": "worker stopped"}
        elif args.command == "cleanup":
            if args.older_than_days < 1:
                raise ValueError("Retention period must be at least one day")
            db.init(settings)
            cutoff = (datetime.now(timezone.utc) - timedelta(days=args.older_than_days)).isoformat()
            with db.connect(settings) as conn:
                ids = [r[0] for r in conn.execute("SELECT id FROM assessments WHERE updated_at<? AND NOT EXISTS(SELECT 1 FROM jobs WHERE jobs.assessment_id=assessments.id AND status IN ('queued','running'))", (cutoff,))]
            result = {"dry_run": not args.execute, "assessment_ids": ids}
            if args.execute:
                for aid in ids:
                    db.delete_assessment(settings, aid)
        elif args.action == "run":
            result, code = cases.run_case(settings, args.case_path.resolve(), args.mode, args.stage, args.reuse_run)
            result = {"run_id": result["id"], "status": result["status"], "mode": result["mode"], "error": result.get("error"), "exit_code": code}
        elif args.action == "inspect":
            result = cases.inspect(settings, args.run)
        elif args.action == "check":
            result, code = cases.check_run(cases.find_run(settings, args.run))
        elif args.action == "attest":
            result, code = cases.attest(settings, args.run, args.check, args.note)
        elif args.action == "baseline":
            result = cases.freeze(settings, args.run)
        else:
            result = cases.compare(settings, args.baseline, args.candidate)
    except (ValueError, db.Missing, db.Conflict) as exc:
        result, code = {"error": str(exc)}, 3
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
