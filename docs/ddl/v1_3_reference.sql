-- BASELINE, KHÔNG PHẢI SPEC HIỆN HÀNH.
-- Trích §3.10 của Master Spec v1.3 nguyên văn tại thời điểm G0-A (03/11/2026, trước điều chỉnh G0-B).
-- Giữ nguyên để tools/ddl_contract_check.py so sánh được 'trước vs sau'; 18/43 check đóng là nhờ phép so
-- sánh này. Master Spec đã được cập nhật tại §3.2/§3.4/§3.6/§3.10 (nhãn [G0-B]), nên file này
-- KHÔNG còn khớp từng ký tự với thân bài spec. Bản DDL đề xuất để freeze: docs/ddl/g0_b_proposed.sql.

-- Tầng 1: request logic (đơn vị retry)
CREATE TABLE fetch_requests (
  id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL,
  source_key TEXT NOT NULL,
  family TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  interval_from_utc TEXT NOT NULL,
  interval_to_utc TEXT NOT NULL,            -- half-open
  page_no INTEGER NOT NULL,
  request_fingerprint TEXT NOT NULL,
  created_at_utc TEXT NOT NULL,
  UNIQUE (run_id, request_fingerprint)
);

-- Tầng 3+4: raw object, HAI LOẠI, cùng bảng
CREATE TABLE raw_objects (
  sha256 TEXT PRIMARY KEY,                  -- hash của object SAU redaction
  object_kind TEXT NOT NULL CHECK (object_kind IN ('RESPONSE','RECORD')),
  byte_len INTEGER NOT NULL,
  codec TEXT NOT NULL,                      -- gzip | identity
  storage_rel_path TEXT NOT NULL,
  integrity_status TEXT NOT NULL,           -- OK | CORRUPT | QUARANTINED
  redaction_policy_version INTEGER NOT NULL,
  wire_sha256 TEXT,                         -- hash bytes TRƯỚC redaction (không lưu bytes)
  created_at_utc TEXT NOT NULL
);
CREATE INDEX ix_raw_kind ON raw_objects(object_kind, created_at_utc);

-- Tầng 2: từng lần thử (retry có thể trả nội dung khác)
CREATE TABLE fetch_attempts (
  id INTEGER PRIMARY KEY,
  request_id INTEGER NOT NULL REFERENCES fetch_requests(id),
  attempt_no INTEGER NOT NULL,
  started_at_utc TEXT NOT NULL,
  finished_at_utc TEXT,
  outcome TEXT NOT NULL,                    -- OK|HTTP_ERROR|TIMEOUT|BLOCKED|MALFORMED
  http_status INTEGER,
  response_object_sha TEXT REFERENCES raw_objects(sha256),  -- cả trang
  byte_len INTEGER,
  error_kind TEXT,
  UNIQUE (request_id, attempt_no)
);

-- Tầng 5: observation — lưu CẢ hai hash + vị trí trích
CREATE TABLE observations (
  id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL,
  attempt_id INTEGER NOT NULL REFERENCES fetch_attempts(id),
  observation_key TEXT NOT NULL,            -- 'SR:<id>' | 'UNRESOLVED:<hash>'
  family TEXT NOT NULL,
  response_object_sha TEXT NOT NULL REFERENCES raw_objects(sha256),
  record_object_sha   TEXT NOT NULL REFERENCES raw_objects(sha256),
  record_locator      TEXT NOT NULL,        -- json_pointer | 'row:<ordinal>'
  content_hash TEXT,
  status TEXT NOT NULL,
  observed_at_utc TEXT NOT NULL,
  UNIQUE (attempt_id, observation_key, record_object_sha)
);

-- Tầng 6: source record
CREATE TABLE source_records (
  id INTEGER PRIMARY KEY,
  source_key TEXT NOT NULL,
  family TEXT NOT NULL,
  natural_key TEXT NOT NULL,
  version_state TEXT NOT NULL
    CHECK (version_state IN ('KNOWN','MISSING','UNPARSABLE')),
  version_raw TEXT,
  identity_key TEXT NOT NULL,               -- family|natural_key|state|version_raw
  first_seen_at_utc TEXT NOT NULL,
  last_seen_at_utc TEXT NOT NULL,
  current_content_version INTEGER NOT NULL,
  current_content_hash TEXT,
  current_record_object_sha TEXT REFERENCES raw_objects(sha256),
  integrity_status TEXT NOT NULL,
  UNIQUE (source_key, family, identity_key)
);

-- Tầng 6b: content version (chỉ đổi khi nội dung chuẩn hóa đổi)
CREATE TABLE source_record_versions (
  id INTEGER PRIMARY KEY,
  source_record_id INTEGER NOT NULL REFERENCES source_records(id),
  content_version_seq INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  introducing_record_sha TEXT NOT NULL REFERENCES raw_objects(sha256),
  introducing_response_sha TEXT REFERENCES raw_objects(sha256),
  fetched_at_utc TEXT NOT NULL,
  change_kind TEXT NOT NULL,                -- NEW | UPDATED
  normalize_schema INTEGER NOT NULL,
  UNIQUE (source_record_id, content_version_seq)
);

-- Tầng 6c: raw revision (mọi trạng thái record raw đã thấy)
CREATE TABLE raw_revisions (
  id INTEGER PRIMARY KEY,
  source_record_id INTEGER NOT NULL REFERENCES source_records(id),
  revision_no INTEGER NOT NULL,
  record_object_sha TEXT NOT NULL REFERENCES raw_objects(sha256),
  content_version_id INTEGER REFERENCES source_record_versions(id),
  reason TEXT NOT NULL,                     -- NEW_RECORD|SEMANTIC_CHANGE|RAW_ONLY_CHANGE|REPLAY
  first_observed_at_utc TEXT NOT NULL,
  run_id TEXT NOT NULL,
  UNIQUE (source_record_id, revision_no)
);

-- Coverage theo interval (KHÔNG lưu tập ngày)
CREATE TABLE coverage_intervals (
  id INTEGER PRIMARY KEY,
  source_key TEXT NOT NULL,
  family TEXT NOT NULL,
  interval_from_utc TEXT NOT NULL,
  interval_to_utc TEXT NOT NULL,            -- [from, to)
  granularity TEXT NOT NULL,                -- DAY|HOUR|MINUTE|SECOND|MILLISECOND
  status TEXT NOT NULL,
  proof_id INTEGER REFERENCES coverage_proofs(id),
  run_id TEXT NOT NULL,
  created_at_utc TEXT NOT NULL
);
CREATE INDEX ix_cov ON coverage_intervals
  (source_key, family, interval_from_utc, interval_to_utc);

CREATE TABLE coverage_proofs (
  id INTEGER PRIMARY KEY,
  proof_version INTEGER NOT NULL,
  source_key TEXT NOT NULL,
  family TEXT NOT NULL,
  interval_from_utc TEXT NOT NULL,
  interval_to_utc TEXT NOT NULL,
  granularity TEXT NOT NULL,
  filter_boundary_kind TEXT NOT NULL,       -- HALF_OPEN_UTC | CLOSED_LOCAL | ...
  filter_boundary_verified_at_utc TEXT NOT NULL,
  source_query_fingerprint TEXT NOT NULL,
  adapter_sha TEXT NOT NULL,
  request_count INTEGER NOT NULL,
  attempt_count INTEGER NOT NULL,
  attempt_error_count INTEGER NOT NULL,     -- vận hành, KHÔNG chặn COMPLETE
  unrecovered_request_count INTEGER NOT NULL, -- PHẢI = 0
  malformed_count INTEGER NOT NULL,
  distinct_ids INTEGER NOT NULL,
  ids_union_hash TEXT NOT NULL,
  total_first INTEGER,
  total_last INTEGER,
  total_trusted INTEGER NOT NULL,
  terminal_evidence_json TEXT NOT NULL,
  page_size INTEGER NOT NULL,
  max_pages_hit INTEGER NOT NULL,
  max_records_hit INTEGER NOT NULL,
  verified_at_utc TEXT NOT NULL,
  verified_by_run_id TEXT NOT NULL
);

-- Canonical + notice group
CREATE TABLE canonical_entities (
  id TEXT PRIMARY KEY, entity_kind TEXT NOT NULL, label TEXT,
  resolution_state TEXT NOT NULL, created_at_utc TEXT NOT NULL
);
CREATE TABLE entity_source_links (
  entity_id TEXT NOT NULL REFERENCES canonical_entities(id),
  source_record_id INTEGER NOT NULL REFERENCES source_records(id),
  relation TEXT NOT NULL, method TEXT NOT NULL,
  evidence_json TEXT NOT NULL, verified_at_utc TEXT,
  UNIQUE (entity_id, source_record_id, relation)
);
CREATE TABLE notice_groups (
  id TEXT PRIMARY KEY, family TEXT NOT NULL, group_kind TEXT NOT NULL,
  evidence_json TEXT NOT NULL, created_at_utc TEXT NOT NULL
);
CREATE TABLE notice_group_members (
  group_id TEXT NOT NULL REFERENCES notice_groups(id),
  source_record_id INTEGER NOT NULL REFERENCES source_records(id),
  publication_label TEXT,                   -- notifyVersion gốc, để hiển thị
  UNIQUE (group_id, source_record_id)
);
CREATE TABLE source_aliases (
  source_key TEXT NOT NULL, family TEXT NOT NULL,
  alias_kind TEXT NOT NULL, alias_value TEXT NOT NULL,
  source_record_id INTEGER NOT NULL REFERENCES source_records(id),
  evidence_json TEXT NOT NULL,
  UNIQUE (source_key, family, alias_kind, alias_value, source_record_id)
);
CREATE TABLE relation_edges (
  id INTEGER PRIMARY KEY, from_entity TEXT NOT NULL, to_entity TEXT NOT NULL,
  relation TEXT NOT NULL, evidence_json TEXT NOT NULL, confidence TEXT NOT NULL
);
