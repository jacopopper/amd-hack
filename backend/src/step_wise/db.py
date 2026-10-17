import hashlib
import json
import sqlite3
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return str(uuid.uuid4())


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def digest(value: bytes):
    return hashlib.sha256(value).hexdigest()


class Conflict(ValueError):
    pass


class Missing(ValueError):
    pass


@contextmanager
def connect(settings, *, write=False):
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    try:
        if write:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init(settings):
    settings.data.mkdir(parents=True, exist_ok=True)
    with connect(settings) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    with connect(settings, write=True) as conn:
        has_version = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone()
        if has_version:
            versions = [r[0] for r in conn.execute("SELECT version FROM schema_version")]
            if versions == [1]:
                # Rebuild only the version marker; all v1 tables and released reports remain intact.
                conn.execute("ALTER TABLE schema_version RENAME TO schema_version_v1")
                conn.execute("CREATE TABLE schema_version(version INTEGER PRIMARY KEY CHECK(version = 2))")
                conn.execute("INSERT INTO schema_version VALUES(2)")
                conn.execute("DROP TABLE schema_version_v1")
                versions = [2]
            if versions == [2]:
                conn.execute("ALTER TABLE schema_version RENAME TO schema_version_v2")
                conn.execute("CREATE TABLE schema_version(version INTEGER PRIMARY KEY CHECK(version = 3))")
                conn.execute("INSERT INTO schema_version VALUES(3)")
                conn.execute("DROP TABLE schema_version_v2")
            elif versions != [3]:
                raise RuntimeError("Unsupported database schema; back up database and assets before migrating")
        # executescript commits any open transaction, which would leave a partially
        # upgraded version marker after a later schema failure.
        statement = ""
        for line in Path(__file__).with_name("schema.sql").read_text().splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                conn.execute(statement)
                statement = ""
        if statement.strip():
            raise RuntimeError("Incomplete database schema statement")
        # Retire password-login sessions from existing v3 databases.
        conn.execute("DROP TABLE IF EXISTS sessions")
        if [r[0] for r in conn.execute("SELECT version FROM schema_version")] != [3]:
            raise RuntimeError("Unsupported database schema; back up database and assets before migrating")


def assessment(conn, assessment_id):
    row = conn.execute("SELECT * FROM assessments WHERE id=?", (assessment_id,)).fetchone()
    if row is None:
        raise Missing("Assessment not found")
    return dict(row)


def editable(conn, assessment_id, input_revision=None, review_revision=None):
    row = assessment(conn, assessment_id)
    if row["status"] == "released":
        raise Conflict("Released assessment is immutable; create a new assessment")
    if conn.execute("SELECT 1 FROM jobs WHERE assessment_id=? AND status IN ('queued','running')", (assessment_id,)).fetchone():
        raise Conflict("Assessment has active work")
    revisions(row, input_revision, review_revision)
    return row


def revisions(row, input_revision=None, review_revision=None):
    if input_revision is not None and row["input_revision"] != input_revision:
        raise Conflict("Inputs changed; reload the assessment")
    if review_revision is not None and row["review_revision"] != review_revision:
        raise Conflict("Review changed; reload the assessment")


def active_scenes(conn, assessment_id):
    # Keep historical rows and their foreign keys, but exclude empty rooms from current work.
    return [dict(row) for row in conn.execute(
        "SELECT s.* FROM scenes s WHERE s.assessment_id=? AND EXISTS "
        "(SELECT 1 FROM photos p WHERE p.scene_id=s.id AND p.assessment_id=s.assessment_id AND p.active=1) ORDER BY s.rowid",
        (assessment_id,))]


def current_scene_links(conn, assessment_id, scenes):
    from step_wise.pipeline.registration import link_state
    result = []
    for row in conn.execute("SELECT * FROM scene_links WHERE assessment_id=? ORDER BY created_at,id", (assessment_id,)):
        if row["from_scene_id"] in scenes and row["to_scene_id"] in scenes:
            value = {"id": row["id"], "from_scene_id": row["from_scene_id"], "to_scene_id": row["to_scene_id"],
                     **json.loads(row["payload"]), "created_at": row["created_at"]}
            result.append({**value, "state": link_state(value, scenes)})
    return result


def invalidate(conn, assessment_id, *, preserve_scenes=False):
    conn.execute("UPDATE assessments SET input_revision=input_revision+1, review_revision=0, analysis_job_id=NULL, status='draft', updated_at=? WHERE id=?", (now(), assessment_id))
    if not preserve_scenes:
        conn.execute("DELETE FROM scene_links WHERE assessment_id=?", (assessment_id,))
        conn.execute("DELETE FROM measurements WHERE assessment_id=?", (assessment_id,))
        conn.execute("UPDATE scenes SET payload='{}' WHERE assessment_id=?", (assessment_id,))
    conn.execute("DELETE FROM clinical_profile_drafts WHERE assessment_id=?", (assessment_id,))


def invalidate_scene(conn, assessment_id, scene_id):
    invalidate(conn, assessment_id, preserve_scenes=True)
    conn.execute("UPDATE scenes SET payload='{}' WHERE id=? AND assessment_id=?", (scene_id, assessment_id))
    conn.execute("DELETE FROM measurements WHERE assessment_id=? AND scene_id=?", (assessment_id, scene_id))
    conn.execute("DELETE FROM scene_links WHERE assessment_id=? AND (from_scene_id=? OR to_scene_id=?)",
                 (assessment_id, scene_id, scene_id))


def assessment_measurements(conn, assessment_id):
    return {r["id"]: {**json.loads(r["payload"]), "scene_id": r["scene_id"]}
            for r in conn.execute("SELECT * FROM measurements WHERE assessment_id=?", (assessment_id,))}


def create_assessment(settings, payload):
    if any(claim.get("origin", "document") == "document" for claim in payload.get("clinical_facts", [])):
        raise ValueError("Upload and source-check documents before confirming document-derived claims")
    aid = uid()
    with connect(settings, write=True) as conn:
        conn.execute("INSERT INTO assessments(id,input_json,created_at,updated_at) VALUES(?,?,?,?)", (aid, dumps(payload), now(), now()))
    return aid


def enqueue(settings, aid, kind, expected_input, expected_review, parent_id=None, stage_scope=None):
    from step_wise.worker import job_stages

    if kind not in {"analyze", "report"}:
        raise ValueError("Unknown job kind")
    scope = set(job_stages(kind)) if stage_scope is None else set(stage_scope)
    if not scope or not scope.issubset(job_stages(kind)):
        raise ValueError("Invalid job stage scope")
    jid = uid()
    with connect(settings, write=True) as conn:
        row = editable(conn, aid, expected_input, expected_review)
        data = json.loads(row["input_json"])
        if not data.get("profile_confirmed"):
            raise Conflict("Confirm the fictional profile first")
        if kind == "analyze":
            if not conn.execute("SELECT 1 FROM photos WHERE assessment_id=? AND role='overview' AND active=1", (aid,)).fetchone():
                raise Conflict("Upload at least one overview photo")
            if settings.recon_backend == "vggt" and "reconstruct" in scope:
                oversized = conn.execute(
                    "SELECT s.label,COUNT(*) AS photo_count FROM photos p JOIN scenes s ON s.id=p.scene_id "
                    "WHERE p.assessment_id=? AND p.active=1 AND p.role='overview' "
                    "GROUP BY p.scene_id HAVING COUNT(*)>? ORDER BY s.rowid LIMIT 1",
                    (aid, settings.frame_cap)).fetchone()
                if oversized:
                    raise Conflict(f"Room {oversized['label']} has {oversized['photo_count']} overview photos; "
                                   f"VGGT allows at most {settings.frame_cap} per room. Remove excess overview photos "
                                   "or explicitly adjust VGGT_FRAME_CAP before analyzing; no photos are silently dropped.")
            if ((settings.jev_enabled and "jev" in scope) or (settings.gemma_backend == "http" and "gemma" in scope)) and not data.get("external_processing_consent"):
                raise Conflict("Acknowledge external photo processing before live visual models run")
            if settings.gemma_backend == "http" and "gemma" in scope and not data.get("clinical_external_processing_consent"):
                raise Conflict("Acknowledge external processing of clinical profile facts before Gemma runs")
            if row["analysis_job_id"]:
                row["review_revision"] += 1
            conn.execute("UPDATE assessments SET analysis_job_id=NULL,review_revision=? WHERE id=?",
                         (row["review_revision"], aid))
            status = "queued"
        else:
            if settings.gemma_backend == "http" and not data.get("clinical_external_processing_consent"):
                raise Conflict("Acknowledge external processing of the clinical profile before Gemma drafts a report")
            if row["status"] not in {"review_ready", "report_ready"}:
                raise Conflict("Analysis must be ready before drafting")
            accepted_snapshot(conn, row)  # required decisions and current analysis
            status = "report_drafting"
        request = {"requested_modes": settings.modes(), "requested_stages": sorted(scope)}
        if settings.gemma_backend == "http" and scope & {"gemma", "report"}:
            request["requested_gemma"] = settings.gemma_identity()
        conn.execute("INSERT INTO jobs(id,assessment_id,kind,status,input_revision,review_revision,parent_id,created_at,output) VALUES(?,?,?,'queued',?,?,?,?,?)", (jid, aid, kind, row["input_revision"], row["review_revision"], parent_id, now(), dumps(request)))
        conn.execute("UPDATE assessments SET status=?,updated_at=? WHERE id=?", (status, now(), aid))
    return jid


def accepted_snapshot(conn, row):
    if not row["analysis_job_id"]:
        raise Conflict("No current analysis")
    result = json.loads(conn.execute("SELECT output FROM jobs WHERE id=?", (row["analysis_job_id"],)).fetchone()[0])
    inputs = json.loads(row["input_json"])
    accepted, deferred = [], []
    for h in conn.execute("SELECT * FROM hazards WHERE job_id=? ORDER BY id", (row["analysis_job_id"],)):
        review = conn.execute("SELECT * FROM reviews WHERE hazard_id=? AND input_revision=? ORDER BY review_revision DESC LIMIT 1", (h["id"], row["input_revision"])).fetchone()
        if review is None:
            raise Conflict("Review every finding before drafting")
        if review["decision"] in {"approve", "edit"}:
            accepted.append({"id": h["id"], **json.loads(review["effective"]), "reviewer": review["reviewer"],
                             "reviewed_at": review["created_at"], "producer": result.get("producer"),
                             "provenance": result.get("provenance")})
        elif review["decision"] == "defer":
            deferred.append({"id": h["id"], "note": review["note"]})
    accepted.sort(key=lambda h: ({"urgent": 0, "important": 1, "recommended": 2}[h["priority"]], h["id"]))
    from step_wise.config import ROOT
    from step_wise.models import Hazard
    from step_wise.validation import validate_hazard
    photo_rows = {p["id"]: {**dict(p), "payload": json.loads(p["payload"])}
                  for p in conn.execute("SELECT * FROM photos WHERE assessment_id=? AND active=1", (row["id"],))}
    scene_rows = {s["id"]: json.loads(s["payload"]) for s in conn.execute("SELECT * FROM scenes WHERE assessment_id=?", (row["id"],))}
    guidance = {g["id"]: g for g in json.loads((ROOT / "guidance/checklist.json").read_text())["entries"]}
    measurements = assessment_measurements(conn, row["id"])
    for hazard in accepted:
        value = Hazard.model_validate({key: hazard[key] for key in Hazard.model_fields})
        validate_hazard(value, photo_rows, scene_rows, guidance, inputs["profile"], measurements)
    current_scenes = {scene["id"]: json.loads(scene["payload"]) for scene in active_scenes(conn, row["id"])}
    house_links = current_scene_links(conn, row["id"], current_scenes)
    calibrations = [{"scene_id": scene["id"], **json.loads(scene["payload"]).get("calibration", {})}
                    for scene in conn.execute("SELECT id,payload FROM scenes WHERE assessment_id=? ORDER BY rowid", (row["id"],))
                    if json.loads(scene["payload"]).get("scale_state") == "metric_calibrated"]
    return {"accepted": accepted, "deferred": deferred, "coverage": result.get("coverage", []),
            "follow_up_requests": result.get("follow_up_requests", []), "modes": result.get("modes", {}),
            "house_links": house_links, "calibrations": calibrations,
            "profile": inputs["profile"], "label": inputs["label"],
            "profile_confirmed": bool(inputs.get("profile_confirmed")),
            "clinical_facts": inputs.get("clinical_facts", []),
            "analysis_job_id": row["analysis_job_id"],
            "input_revision": row["input_revision"], "review_revision": row["review_revision"],
            "staged_data": True, "created_at": now()}


def asset_path(settings, relative):
    path = (settings.data / relative).resolve()
    if not path.is_relative_to(settings.data.resolve()):
        raise ValueError("Invalid asset path")
    return path


def store_asset(settings, conn, aid, relative, media_type, provenance, scope="source"):
    path = asset_path(settings, relative)
    data = path.read_bytes()
    existing = conn.execute("SELECT * FROM assets WHERE path=?", (relative,)).fetchone()
    if existing:
        if existing["assessment_id"] != aid or existing["sha256"] != digest(data):
            raise Conflict("Existing immutable asset changed")
        return existing["id"]
    asset_id = uid()
    conn.execute("INSERT INTO assets VALUES(?,?,?,?,?,?,?,?)", (asset_id, aid, relative, media_type, len(data), digest(data), provenance, scope))
    return asset_id


def delete_assessment(settings, aid):
    with connect(settings, write=True) as conn:
        assessment(conn, aid)
        if conn.execute("SELECT 1 FROM jobs WHERE assessment_id=? AND status IN ('queued','running')", (aid,)).fetchone():
            raise Conflict("Cannot delete active work")
        paths = [asset_path(settings, r[0]) for r in conn.execute("SELECT path FROM assets WHERE assessment_id=?", (aid,))]
        conn.execute("DELETE FROM assessments WHERE id=?", (aid,))
    # Access is revoked transactionally before removing the local files.
    for path in paths:
        path.unlink(missing_ok=True)
    shutil.rmtree(settings.data / "assessments" / aid, ignore_errors=True)
