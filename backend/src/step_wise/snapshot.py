"""Public demo: one frozen, already analysed case; every visitor reviews a private copy.

The snapshot is plain JSON rows plus the asset files, so the hosted demo needs no
model keys: visitors only review, draft the report and release it.
"""
import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone

from step_wise import db
from step_wise.config import ROOT

SNAPSHOT = ROOT / "demo/snapshot"
# Insert order follows the foreign keys. Reviews, reports and report jobs stay out:
# every visitor starts the professional review from scratch.
TABLES = ("scenes", "assets", "photos", "jobs", "candidates", "hazards", "measurements", "scene_links",
          "clinical_documents", "clinical_profile_drafts")
ID_TABLES = tuple(t for t in TABLES if t != "clinical_profile_drafts")


def export_case(settings, aid, directory=SNAPSHOT):
    with db.connect(settings) as conn:
        assessment = db.assessment(conn, aid)
        if not assessment["analysis_job_id"] or assessment["status"] not in {"review_ready", "report_drafting", "report_ready", "released"}:
            raise ValueError("Only an assessment with a completed analysis can become the demo case")
        rows = {table: [dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE assessment_id=? ORDER BY rowid", (aid,))] for table in TABLES}
    job = assessment["analysis_job_id"]
    rows["jobs"] = [{**j, "parent_id": None} for j in rows["jobs"] if j["id"] == job]
    rows["candidates"] = [c for c in rows["candidates"] if c["job_id"] == job]
    rows["hazards"] = [h for h in rows["hazards"] if h["job_id"] == job]
    rows["assets"] = [a for a in rows["assets"] if a["scope"] != "report"]
    shutil.rmtree(directory / "files", ignore_errors=True)
    for asset in rows["assets"]:
        target = directory / "files" / asset["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(db.asset_path(settings, asset["path"]), target)
    case = {"schema_version": 3, "exported_at": db.now(), "assessment": assessment, **rows}
    (directory / "case.json").write_text(json.dumps(case, ensure_ascii=False, indent=1))
    return {"assessment_id": aid, "hazards": len(rows["hazards"]), "photos": len(rows["photos"]), "assets": len(rows["assets"])}


def clone_case(settings, directory=SNAPSHOT):
    case = json.loads((directory / "case.json").read_text())
    if case.get("schema_version") != 3:
        raise ValueError("Unsupported demo snapshot")
    mapping = {old: db.uid() for old in [case["assessment"]["id"], *(r["id"] for t in ID_TABLES for r in case[t])]}
    pattern = re.compile("|".join(map(re.escape, mapping)))

    def swap(row):
        return {k: pattern.sub(lambda m: mapping[m.group(0)], v) if isinstance(v, str) else v for k, v in row.items()}

    now = db.now()
    assessment = swap(case["assessment"]) | {"status": "review_ready", "review_revision": 0, "created_at": now, "updated_at": now}
    try:
        with db.connect(settings, write=True) as conn:
            for table, rows in (("assessments", [assessment]), *((t, [swap(r) for r in case[t]]) for t in TABLES)):
                for row in rows:
                    conn.execute(f"INSERT INTO {table}({','.join(row)}) VALUES({','.join('?' * len(row))})", tuple(row.values()))
            for asset in case["assets"]:
                target = db.asset_path(settings, swap(asset)["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                source = directory / "files" / asset["path"]
                try:
                    # Assets are immutable and checksummed, so visitors can share the bytes.
                    os.link(source, target)
                except OSError:
                    shutil.copy2(source, target)
    except BaseException:
        shutil.rmtree(settings.data / "assessments" / assessment["id"], ignore_errors=True)
        raise
    return assessment["id"]


def prune(settings, max_age_hours=24, keep=200):
    """Visitor copies are disposable: drop old ones and cap how many exist at once."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
    with db.connect(settings) as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM assessments ORDER BY created_at DESC")]
        old = {r[0] for r in conn.execute("SELECT id FROM assessments WHERE created_at<?", (cutoff,))}
    for aid in [*ids[keep:], *(i for i in ids[:keep] if i in old)]:
        try:
            db.delete_assessment(settings, aid)
        except (db.Missing, db.Conflict):
            pass
