-- Operational checkpoints are separate from immutable successful capture evidence.
CREATE TABLE deep_tasks (
 source_record_id INTEGER NOT NULL REFERENCES source_records(id),
 lane TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('NOT_COLLECTED','FETCHED','SOURCE_EMPTY','PARSE_ERROR','BLOCKED','FAILED','STALE')),
 listing_object_sha TEXT,
 response_object_sha TEXT REFERENCES raw_objects(sha256),
 capture_id INTEGER REFERENCES deep_captures(id),
 updated_at_utc TEXT NOT NULL,
 error TEXT,
 provenance_json TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(source_record_id,lane)
);
CREATE INDEX ix_deep_tasks_state ON deep_tasks(state,source_record_id);
