-- 009 m1_lane_enums: the M1 lane needs 'M1' in two frozen v1 CHECK enums.
-- deep_captures.lane (g0_b_proposed.sql) and runs.job_kind (g0_b_proposed.sql)
-- list only DETAIL/HSMT/REOFFER/KQLCNT_FULL/BOQ/OTHER and the list-flow kinds.
-- SQLite cannot ALTER a CHECK, so both tables are rebuilt, exactly as in 007/008.
-- Existing rows are copied verbatim; all other columns, CHECKs, UNIQUEs and
-- FOREIGN KEYs are unchanged. v1's file is NOT edited: its checksum is
-- recorded in production stores.
-- NOTE: deep_captures is the parent of deep_capture_history and runs is the
-- parent of run_tasks / maintenance_attempts, so this rebuild needs foreign
-- key enforcement OFF for its own transaction (see the -- FK_OFF marker and
-- storage/migrations.py). The harness re-enables enforcement afterwards and
-- fails the migration if PRAGMA foreign_key_check finds any violation.
-- Three views read deep_captures (raw_object_roles, deep_capture_latest,
-- orphan_object_rows via raw_object_roles). SQLite's ALTER TABLE RENAME
-- rewrites dependent views and fails while the old table is dropped, so the
-- views are dropped first and re-created verbatim (v1 definitions) last.
-- FK_OFF
DROP VIEW IF EXISTS orphan_object_rows;
DROP VIEW IF EXISTS deep_capture_latest;
DROP VIEW IF EXISTS raw_object_roles;
CREATE TABLE deep_captures_new (
  id                   INTEGER PRIMARY KEY,
  source_record_id     INTEGER NOT NULL REFERENCES source_records(id),
  lane                 TEXT NOT NULL CHECK (lane IN ('DETAIL','HSMT','REOFFER','M1','KQLCNT_FULL','BOQ','OTHER')),
  endpoint_kind        TEXT NOT NULL,
  latest_capture_seq   INTEGER NOT NULL DEFAULT 1 CHECK (latest_capture_seq >= 1),
  completeness         TEXT NOT NULL CHECK (completeness IN ('FULL','PARTIAL','NOT_COLLECTED')),
  captured_at_utc      TEXT NOT NULL CHECK (captured_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  refresh_due_at_utc   TEXT CHECK (refresh_due_at_utc IS NULL OR refresh_due_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  stale                INTEGER NOT NULL DEFAULT 0 CHECK (stale IN (0,1)),
  stale_reason         TEXT CHECK (stale_reason IS NULL OR stale_reason IN ('SOURCE_VERSION_CHANGED','REFRESH_INTERVAL_ELAPSED','RECONCILE_MISMATCH','QUARANTINED_MEMBER')),
  source_version_json  TEXT NOT NULL CHECK (source_version_json GLOB '{*'),
  response_object_sha  TEXT NOT NULL REFERENCES raw_objects(sha256),
  record_object_sha    TEXT NOT NULL REFERENCES raw_objects(sha256),
  content_hash         TEXT NOT NULL CHECK (content_hash GLOB 'ch1:*'),
  lane_status          TEXT NOT NULL CHECK (lane_status IN ('NOT_ATTEMPTED','IN_PROGRESS','FULL','PARTIAL','FAILED','BLOCKED','STALE','LEGACY_PARTIAL')),
  UNIQUE (source_record_id, lane, endpoint_kind),
  CHECK (stale = 0 OR stale_reason IS NOT NULL),
  CHECK (latest_capture_seq >= 1)
);
INSERT INTO deep_captures_new (id, source_record_id, lane, endpoint_kind, latest_capture_seq, completeness, captured_at_utc, refresh_due_at_utc, stale, stale_reason, source_version_json, response_object_sha, record_object_sha, content_hash, lane_status)
  SELECT id, source_record_id, lane, endpoint_kind, latest_capture_seq, completeness, captured_at_utc, refresh_due_at_utc, stale, stale_reason, source_version_json, response_object_sha, record_object_sha, content_hash, lane_status FROM deep_captures;
DROP TABLE deep_captures;
ALTER TABLE deep_captures_new RENAME TO deep_captures;
CREATE TABLE runs_new (
  run_id        TEXT PRIMARY KEY,
  job_id        TEXT NOT NULL,
  job_kind      TEXT NOT NULL CHECK (job_kind IN ('INDEX','BACKFILL','INCREMENTAL','RECONCILE','DETAIL','HSMT','REOFFER','M1','VERIFY','IMPORT','EXPORT','BACKUP')),
  lifecycle     TEXT NOT NULL CHECK (lifecycle IN ('PENDING','RUNNING','PAUSING','PAUSED','RESUMING','STOPPING','FINISHED','TERMINATED')),
  status        TEXT CHECK (status IN ('COMPLETED','PARTIAL','FAILED','BLOCKED','INTERRUPTED')),
  started_at_utc TEXT NOT NULL CHECK (started_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  finished_at_utc TEXT CHECK (finished_at_utc IS NULL OR finished_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  policy_version INTEGER NOT NULL CHECK (policy_version >= 1),
  requested_by  TEXT NOT NULL,
  CHECK (
    (lifecycle IN ('FINISHED','TERMINATED')) = (status IS NOT NULL)
  ),
  CHECK (status IS NULL OR finished_at_utc IS NOT NULL),
  CHECK (status <> 'COMPLETED' OR lifecycle = 'FINISHED')
);
INSERT INTO runs_new (run_id, job_id, job_kind, lifecycle, status, started_at_utc, finished_at_utc, policy_version, requested_by)
  SELECT run_id, job_id, job_kind, lifecycle, status, started_at_utc, finished_at_utc, policy_version, requested_by FROM runs;
DROP TABLE runs;
ALTER TABLE runs_new RENAME TO runs;
-- Re-create the dropped views verbatim (v1 definitions from g0_b_proposed.sql).
CREATE VIEW raw_object_roles AS
  SELECT response_object_sha AS sha256, 'RESPONSE' AS role FROM fetch_attempts WHERE response_object_sha IS NOT NULL
  UNION SELECT response_object_sha, 'RESPONSE' FROM observations
  UNION SELECT record_object_sha,   'RECORD'   FROM observations
  UNION SELECT record_object_sha,   'RECORD'   FROM raw_revisions
  UNION SELECT introducing_record_sha,   'RECORD'   FROM source_record_versions
  UNION SELECT introducing_response_sha, 'RESPONSE' FROM source_record_versions WHERE introducing_response_sha IS NOT NULL
  UNION SELECT current_record_object_sha, 'RECORD'  FROM source_records
  UNION SELECT record_object_sha,   'RECORD'   FROM unresolved_records
  UNION SELECT response_object_sha, 'RESPONSE' FROM unresolved_records WHERE response_object_sha IS NOT NULL
  UNION SELECT response_object_sha, 'RESPONSE' FROM quarantined_responses
  UNION SELECT response_object_sha, 'RESPONSE' FROM page_receipts
  UNION SELECT record_object_sha,   'RECORD'   FROM deep_captures
  UNION SELECT response_object_sha, 'RESPONSE' FROM deep_captures
  UNION SELECT record_object_sha,   'RECORD'   FROM deep_capture_history
  UNION SELECT response_object_sha, 'RESPONSE' FROM deep_capture_history
  UNION SELECT party_object_sha,    'RECORD'   FROM contractor_memberships
  UNION SELECT object_sha,          'RECORD'   FROM documents WHERE object_sha IS NOT NULL
  UNION SELECT sample_sha,          'RESPONSE' FROM source_registry WHERE sample_sha IS NOT NULL;
CREATE VIEW deep_capture_latest AS
  SELECT dc.id AS deep_capture_id, dc.source_record_id, dc.lane, dc.endpoint_kind,
         dc.completeness, dc.stale, dc.refresh_due_at_utc, h.captured_at_utc,
         h.response_object_sha, h.record_object_sha, h.content_hash, h.source_version_json
  FROM deep_captures dc
  JOIN deep_capture_history h
    ON h.deep_capture_id = dc.id AND h.capture_seq = dc.latest_capture_seq;
CREATE VIEW orphan_object_rows AS
  SELECT r.sha256, r.storage_rel_path, r.created_at_utc
  FROM raw_objects r
  WHERE NOT EXISTS (SELECT 1 FROM raw_object_roles o WHERE o.sha256 = r.sha256);
