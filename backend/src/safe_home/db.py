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
        conn.executescript(Path(__file__).with_name("schema.sql").read_text())
        if [r[0] for r in conn.execute("SELECT version FROM schema_version")] != [1]:
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


def invalidate(conn, assessment_id):
    conn.execute("UPDATE assessments SET input_revision=input_revision+1, review_revision=0, analysis_job_id=NULL, status='draft', updated_at=? WHERE id=?", (now(), assessment_id))
    conn.execute("UPDATE scenes SET payload='{}' WHERE assessment_id=?", (assessment_id,))


def create_assessment(settings, payload):
    aid = uid()
    with connect(settings, write=True) as conn:
        conn.execute("INSERT INTO assessments(id,input_json,created_at,updated_at) VALUES(?,?,?,?)", (aid, dumps(payload), now(), now()))
    return aid


def enqueue(settings, aid, kind, expected_input, expected_review, parent_id=None):
    jid = uid()
    with connect(settings, write=True) as conn:
        row = editable(conn, aid, expected_input, expected_review)
        data = json.loads(row["input_json"])
        if not data["profile_confirmed"]:
            raise Conflict("Confirm the fictional profile first")
        if kind == "analyze":
            if not conn.execute("SELECT 1 FROM photos WHERE assessment_id=? AND role='overview' AND active=1", (aid,)).fetchone():
                raise Conflict("Upload at least one overview photo")
            if settings.jev_enabled and not data["external_processing_consent"]:
                raise Conflict("Acknowledge external photo processing before Jev runs")
            status = "queued"
        else:
            if row["status"] not in {"review_ready", "report_ready"}:
                raise Conflict("Analysis must be ready before drafting")
            accepted_snapshot(conn, row)  # required decisions and current analysis
            status = "report_drafting"
        conn.execute("INSERT INTO jobs(id,assessment_id,kind,status,input_revision,review_revision,parent_id,created_at,output) VALUES(?,?,?,'queued',?,?,?,?,?)", (jid, aid, kind, row["input_revision"], row["review_revision"], parent_id, now(), dumps({"requested_modes": settings.modes()})))
        conn.execute("UPDATE assessments SET status=?,updated_at=? WHERE id=?", (status, now(), aid))
    return jid


def accepted_snapshot(conn, row):
    if not row["analysis_job_id"]:
        raise Conflict("No current analysis")
    accepted, deferred = [], []
    for h in conn.execute("SELECT * FROM hazards WHERE job_id=? ORDER BY id", (row["analysis_job_id"],)):
        review = conn.execute("SELECT * FROM reviews WHERE hazard_id=? AND input_revision=? ORDER BY review_revision DESC LIMIT 1", (h["id"], row["input_revision"])).fetchone()
        if review is None:
            raise Conflict("Review every finding before drafting")
        if review["decision"] in {"approve", "edit"}:
            accepted.append({"id": h["id"], **json.loads(review["effective"]), "reviewer": review["reviewer"], "reviewed_at": review["created_at"]})
        elif review["decision"] == "defer":
            deferred.append({"id": h["id"], "note": review["note"]})
    accepted.sort(key=lambda h: ({"urgent": 0, "important": 1, "recommended": 2}[h["priority"]], h["id"]))
    result = json.loads(conn.execute("SELECT output FROM jobs WHERE id=?", (row["analysis_job_id"],)).fetchone()[0])
    return {"accepted": accepted, "deferred": deferred, "coverage": result.get("coverage", []),
            "follow_up_requests": result.get("follow_up_requests", []), "modes": result.get("modes", {}),
            "profile": json.loads(row["input_json"])["profile"], "label": json.loads(row["input_json"])["label"],
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
