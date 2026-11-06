-- Additive scheduler ledger. Existing frozen status enums and history are unchanged.
CREATE TABLE maintenance_tasks (
  task_key TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('BACKFILL','RECONCILE')),
  source_key TEXT NOT NULL, family TEXT NOT NULL,
  interval_from_utc TEXT NOT NULL, interval_to_utc TEXT NOT NULL,
  context_json TEXT NOT NULL, contract_signature TEXT NOT NULL,
  parent_job_id TEXT,
  state TEXT NOT NULL CHECK(state IN ('WAITING','RUNNING','PAUSED','CANCELLED','COMPLETE','MANUAL_REVIEW','EXHAUSTED')),
  attempts INTEGER NOT NULL DEFAULT 0,
  requests_used INTEGER NOT NULL DEFAULT 0, seconds_used REAL NOT NULL DEFAULT 0,
  next_attempt_at TEXT NOT NULL, last_reason TEXT, approved INTEGER NOT NULL DEFAULT 0,
  created_at_utc TEXT NOT NULL, updated_at_utc TEXT NOT NULL
);
CREATE INDEX ix_maintenance_due ON maintenance_tasks(state,approved,next_attempt_at);
CREATE TABLE maintenance_holds (
  parent_job_id TEXT NOT NULL, source_key TEXT NOT NULL, family TEXT NOT NULL,
  interval_from_utc TEXT NOT NULL, interval_to_utc TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('PAUSED','CANCELLED')),
  PRIMARY KEY(parent_job_id,source_key,family,interval_from_utc,interval_to_utc)
);
CREATE TABLE maintenance_attempts (
  task_key TEXT NOT NULL REFERENCES maintenance_tasks(task_key),
  attempt_no INTEGER NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id),
  started_at_utc TEXT NOT NULL, finished_at_utc TEXT,
  reserved_requests INTEGER NOT NULL, reserved_seconds INTEGER NOT NULL,
  requests_used INTEGER, seconds_used REAL, result_json TEXT,
  PRIMARY KEY(task_key,attempt_no)
);
