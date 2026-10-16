PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY CHECK(version = 1));
INSERT OR IGNORE INTO schema_version VALUES(1);
CREATE TABLE IF NOT EXISTS assessments(
 id TEXT PRIMARY KEY, input_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
 input_revision INTEGER NOT NULL DEFAULT 1, review_revision INTEGER NOT NULL DEFAULT 0,
 analysis_job_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scenes(
 id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
 label TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS assets(
 id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
 path TEXT NOT NULL UNIQUE, media_type TEXT NOT NULL, byte_size INTEGER NOT NULL,
 sha256 TEXT NOT NULL, provenance TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'source'
);
CREATE TABLE IF NOT EXISTS photos(
 id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
 scene_id TEXT NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
 asset_id TEXT NOT NULL REFERENCES assets(id), role TEXT NOT NULL CHECK(role IN ('overview','detail')),
 payload TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK(kind IN ('analyze','report')), status TEXT NOT NULL,
 input_revision INTEGER NOT NULL, review_revision INTEGER NOT NULL, parent_id TEXT REFERENCES jobs(id),
 stage TEXT, stages TEXT NOT NULL DEFAULT '{}', output TEXT NOT NULL DEFAULT '{}',
 heartbeat TEXT, error TEXT, created_at TEXT NOT NULL, finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_job ON jobs(assessment_id) WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS candidates(
 id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
 job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 photo_id TEXT NOT NULL REFERENCES photos(id), payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hazards(
 id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
 job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS measurements(
 id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
 scene_id TEXT NOT NULL REFERENCES scenes(id) ON DELETE CASCADE, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews(
 id TEXT PRIMARY KEY, hazard_id TEXT NOT NULL REFERENCES hazards(id) ON DELETE CASCADE,
 assessment_id TEXT NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
 input_revision INTEGER NOT NULL, review_revision INTEGER NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('approve','edit','reject','defer')),
 effective TEXT NOT NULL, reviewer TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS latest_review ON reviews(hazard_id, review_revision DESC);
CREATE TABLE IF NOT EXISTS reports(
 id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
 job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id), input_revision INTEGER NOT NULL, review_revision INTEGER NOT NULL,
 snapshot TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL, html_asset_id TEXT REFERENCES assets(id),
 pdf_asset_id TEXT REFERENCES assets(id), status TEXT NOT NULL DEFAULT 'draft', approver TEXT, released_at TEXT,
 family_hash TEXT UNIQUE, family_expires_at TEXT, revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, expires_at TEXT NOT NULL);
