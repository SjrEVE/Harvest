-- Sidecars preserve the frozen capture/task states and old binary INSERT arity.
CREATE TABLE deep_capture_parse_metadata (
 deep_capture_id INTEGER NOT NULL,
 capture_seq INTEGER NOT NULL,
 parser_version INTEGER NOT NULL,
 capability TEXT NOT NULL,
 warnings_json TEXT NOT NULL DEFAULT '[]',
 parsed_at_utc TEXT NOT NULL,
 PRIMARY KEY(deep_capture_id,capture_seq),
 FOREIGN KEY(deep_capture_id,capture_seq) REFERENCES deep_capture_history(deep_capture_id,capture_seq)
);
CREATE TABLE deep_task_metadata (
 source_record_id INTEGER NOT NULL,
 lane TEXT NOT NULL,
 parser_version INTEGER NOT NULL,
 adapter_version INTEGER NOT NULL,
 capability TEXT NOT NULL,
 warnings_json TEXT NOT NULL DEFAULT '[]',
 task_updated_at_utc TEXT NOT NULL,
 PRIMARY KEY(source_record_id,lane),
 FOREIGN KEY(source_record_id,lane) REFERENCES deep_tasks(source_record_id,lane)
);
