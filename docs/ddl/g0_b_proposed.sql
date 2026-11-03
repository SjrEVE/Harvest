-- SĂN THẦU DATA STUDIO — DDL đề xuất G0-B
-- Baseline: Master Spec Consolidated v1.3 §3.10 (docs/ddl/v1_3_reference.sql).
-- Mọi thay đổi so với §3.10 có nhãn [G0B-<id>] và được giải thích ở GATE-G0-B/G0-B-CLOSURE.md.
-- ĐÂY LÀ HỢP ĐỒNG ĐỂ NGHIỆM THU, KHÔNG PHẢI MIGRATION PRODUCTION.
--
-- Pragmas (§3.10): journal_mode=WAL, busy_timeout, synchronous=FULL, foreign_keys=ON.
-- foreign_keys=ON chỉ có tác dụng sau khi FK được khai báo thật — ở đây tất cả đều khai báo.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- 1. Raw object store
-- [G0B-SHA] object_kind BỊ BỎ khỏi bảng. §3.10 vừa đặt sha256 làm PRIMARY KEY vừa đặt
-- object_kind ∈ {RESPONSE,RECORD}; hai ràng buộc đó xung đột: cùng một payload byte-identical
-- (page một record được lưu nguyên văn như record — đúng hình dạng code cũ `obs.raw = doc`)
-- chỉ được phép tồn tại một row, nên row thứ hai hoặc fail UNIQUE hoặc phải nói dối về vai trò.
-- Vai trò nay là thuộc tính của CẠNH tham chiếu, không phải của object; xem view bên dưới.
-- Hệ quả tốt: store đúng nghĩa content-addressed theo §5.1 ("hai source cùng nội dung dùng
-- chung object, identity vẫn riêng"), và không có trạng thái dư thừa theo tinh thần C-5.
CREATE TABLE raw_objects (
  sha256                  TEXT PRIMARY KEY NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
  byte_len                INTEGER NOT NULL CHECK (byte_len >= 0),
  codec                   TEXT NOT NULL CHECK (codec IN ('gzip','identity')),
  storage_rel_path        TEXT NOT NULL,
  integrity_status        TEXT NOT NULL CHECK (integrity_status IN ('OK','CORRUPT','QUARANTINED')),
  redaction_policy_version INTEGER NOT NULL CHECK (redaction_policy_version >= 1),
  wire_sha256             TEXT CHECK (wire_sha256 IS NULL OR (length(wire_sha256) = 64 AND wire_sha256 NOT GLOB '*[^0-9a-f]*')),
  created_at_utc          TEXT NOT NULL CHECK (created_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z')
);
CREATE INDEX ix_raw_created ON raw_objects(created_at_utc);
CREATE INDEX ix_raw_integrity ON raw_objects(integrity_status);

-- Vai trò suy ra từ các cạnh, không lưu -> không thể tự mâu thuẫn.
-- View này phục vụ benchmark §5.1 và GC, thứ mà object_kind từng định làm.
-- Mỗi SELECT lấy đúng tên cột của bảng nguồn rồi alias về sha256.
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

-- ---------------------------------------------------------------- 2. Fetch tiers
CREATE TABLE fetch_requests (
  id                  INTEGER PRIMARY KEY,
  run_id              TEXT NOT NULL,
  source_key          TEXT NOT NULL,
  family              TEXT NOT NULL,
  endpoint            TEXT NOT NULL,
  interval_from_utc   TEXT NOT NULL CHECK (interval_from_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  interval_to_utc     TEXT NOT NULL CHECK (interval_to_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  page_no             INTEGER NOT NULL CHECK (page_no >= 0),
  request_fingerprint TEXT NOT NULL,
  created_at_utc      TEXT NOT NULL CHECK (created_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  UNIQUE (run_id, request_fingerprint),
  CHECK (interval_from_utc < interval_to_utc)          -- half-open [from,to): rỗng là lỗi, không phải dữ kiện
);

-- [G0B-C2] attempt_outcome nay bị CHECK. §3.6 liệt kê từ vựng nhưng §3.10 không ràng buộc,
-- nên một đường code ghi sai là DB nhận mà không kêu.
CREATE TABLE fetch_attempts (
  id                   INTEGER PRIMARY KEY,
  request_id           INTEGER NOT NULL REFERENCES fetch_requests(id),
  attempt_no           INTEGER NOT NULL CHECK (attempt_no >= 1),
  started_at_utc       TEXT NOT NULL CHECK (started_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  finished_at_utc      TEXT CHECK (finished_at_utc IS NULL OR finished_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  outcome              TEXT NOT NULL CHECK (outcome IN ('OK','HTTP_ERROR','TIMEOUT','BLOCKED','MALFORMED')),
  http_status          INTEGER,
  response_object_sha  TEXT REFERENCES raw_objects(sha256),
  byte_len             INTEGER CHECK (byte_len IS NULL OR byte_len >= 0),
  error_kind           TEXT,
  UNIQUE (request_id, attempt_no),
  -- Bằng chứng vật lý phải đi kèm OK: không có "OK" mà không có body.
  CHECK (outcome <> 'OK' OR response_object_sha IS NOT NULL)
);
CREATE INDEX ix_attempts_request ON fetch_attempts(request_id);

-- ---------------------------------------------------------------- 3. Observations
-- [G0B-O4] record_object_sha vẫn NOT NULL, và đó là *chủ đích*. O-4 chốt:
--   * trích được record nhưng thiếu identity -> có RECORD object + observation UNRESOLVED;
--   * không parse được response/member -> quarantine ở mức response/page,
--     KHÔNG tạo record và KHÔNG tạo observation giả.
-- Vì vậy mọi observation luôn có record object thật; không đường nào phải gán hash của
-- response vào cột của record (điều §3.2 cấm).
CREATE TABLE observations (
  id                  INTEGER PRIMARY KEY,
  run_id              TEXT NOT NULL,
  attempt_id          INTEGER NOT NULL REFERENCES fetch_attempts(id),
  observation_key     TEXT NOT NULL CHECK (observation_key GLOB 'SR:*' OR observation_key GLOB 'UNRESOLVED:*'),
  family              TEXT NOT NULL,
  response_object_sha TEXT NOT NULL REFERENCES raw_objects(sha256),
  record_object_sha   TEXT NOT NULL REFERENCES raw_objects(sha256),
  record_locator      TEXT NOT NULL CHECK (record_locator GLOB '/*' OR record_locator GLOB 'row:*'),
  content_hash        TEXT,
  status              TEXT NOT NULL CHECK (status IN ('NEW','UPDATED','UPDATED_RAW','UNCHANGED','DUPLICATE_IN_RUN','UNRESOLVED')),
  observed_at_utc     TEXT NOT NULL CHECK (observed_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  UNIQUE (attempt_id, observation_key, record_object_sha)
  -- [G0B-ROLE] KHÔNG còn CHECK (response_object_sha <> record_object_sha).
  -- Lý do: D-0 chấp thuận store content-addressed thuần. Nếu bytes của một trang thực sự
  -- bằng bytes của một hồ sơ (đúng hình dạng code cũ `obs.raw = doc`), hai hash bằng nhau
  -- là HỢP LỆ, còn CHECK đó thì từ chối một row đúng.
  -- Điều §3.2 thật sự cần không phải "hai hash khác nhau" mà là "record này được trích ra
  -- từ response kia, tại locator kia" — mạnh hơn và không phủ nhận trùng hợp bytes.
  -- Bảo đảm đó nằm ở object_derivation + trigger bên dưới.
);

-- [G0B-ROLE] Chứng cứ dẫn xuất: hồ sơ này sinh ra từ trang kia tại locator kia, dưới
-- chính sách redaction này. Không có bảng này thì record_locator chỉ là lời khai.
CREATE TABLE object_derivation (
  id                       INTEGER PRIMARY KEY,
  response_object_sha      TEXT NOT NULL REFERENCES raw_objects(sha256),
  record_object_sha        TEXT NOT NULL REFERENCES raw_objects(sha256),
  record_locator           TEXT NOT NULL CHECK (record_locator GLOB '/*' OR record_locator GLOB 'row:*'),
  redaction_policy_version INTEGER NOT NULL CHECK (redaction_policy_version >= 1),
  derived_at_utc           TEXT NOT NULL CHECK (derived_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  UNIQUE (response_object_sha, record_object_sha, record_locator, redaction_policy_version)
);
CREATE INDEX ix_derivation_record ON object_derivation(record_object_sha);

-- Mọi observation phải có chứng cứ dẫn xuất khớp cả ba: response, record, locator.
-- Đây là thứ thay thế CHECK so-hash, và nó phát hiện được lỗi mà CHECK đó không phát hiện:
-- gán đúng loại hash nhưng sai cặp trang/hồ sơ.
CREATE TRIGGER trg_observation_derivation
BEFORE INSERT ON observations
BEGIN
  SELECT CASE WHEN NOT EXISTS (
      SELECT 1 FROM object_derivation d
      WHERE d.response_object_sha = NEW.response_object_sha
        AND d.record_object_sha   = NEW.record_object_sha
        AND d.record_locator      = NEW.record_locator
  ) THEN RAISE(ABORT, 'observation requires a matching object_derivation row (response, record, locator)')
  END;
END;
CREATE INDEX ix_obs_record ON observations(record_object_sha);
CREATE INDEX ix_obs_key ON observations(observation_key);

-- ---------------------------------------------------------------- 4. Source records
-- [G0B-O3] identity_key là canonical serialization có version (domain/identity.py),
-- KHÔNG phải nối chuỗi. Dạng 'id1:<64 hex>'.
-- [G0B-C7] MISSING ⟺ version_raw IS NULL, buộc cả ở CHECK lẫn ở Python.
--   'MISSING' + NULL và 'KNOWN' + '00' là hai identity khác nhau (DEDUP-06);
--   version_raw không bao giờ mang giá trị sentinel 'None' hay '00'.
CREATE TABLE source_records (
  id                        INTEGER PRIMARY KEY,
  source_key                TEXT NOT NULL,
  family                    TEXT NOT NULL,
  natural_key               TEXT NOT NULL,
  version_state             TEXT NOT NULL CHECK (version_state IN ('KNOWN','MISSING','UNPARSABLE')),
  version_raw               TEXT,
  identity_key              TEXT NOT NULL CHECK (identity_key GLOB 'id1:[0-9a-f][0-9a-f]*' AND length(identity_key) = 68),
  first_seen_at_utc         TEXT NOT NULL CHECK (first_seen_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  last_seen_at_utc          TEXT NOT NULL CHECK (last_seen_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  current_content_version   INTEGER NOT NULL CHECK (current_content_version >= 1),
  -- [G0B-C9] NOT NULL: không tồn tại trạng thái hợp lệ nào có content version mà chưa có hash.
  current_content_hash      TEXT NOT NULL CHECK (current_content_hash GLOB 'ch1:*'),
  current_record_object_sha TEXT NOT NULL REFERENCES raw_objects(sha256),
  integrity_status          TEXT NOT NULL CHECK (integrity_status IN ('OK','SUSPECT','CORRUPT')),
  UNIQUE (source_key, family, identity_key),
  CHECK (
    (version_state = 'MISSING' AND version_raw IS NULL)
    OR (version_state IN ('KNOWN','UNPARSABLE') AND version_raw IS NOT NULL)
  ),
  CHECK (first_seen_at_utc <= last_seen_at_utc)
);
CREATE INDEX ix_records_natural ON source_records(source_key, family, natural_key);

CREATE TABLE source_record_versions (
  id                     INTEGER PRIMARY KEY,
  source_record_id       INTEGER NOT NULL REFERENCES source_records(id),
  content_version_seq    INTEGER NOT NULL CHECK (content_version_seq >= 1),
  content_hash           TEXT NOT NULL CHECK (content_hash GLOB 'ch1:*'),
  introducing_record_sha TEXT NOT NULL REFERENCES raw_objects(sha256),
  introducing_response_sha TEXT REFERENCES raw_objects(sha256),
  fetched_at_utc         TEXT NOT NULL CHECK (fetched_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  change_kind            TEXT NOT NULL CHECK (change_kind IN ('NEW','UPDATED')),
  normalize_schema       INTEGER NOT NULL CHECK (normalize_schema >= 1),
  UNIQUE (source_record_id, content_version_seq),
  -- seq 1 là bản khai sinh; không có "UPDATE" của record chưa từng tồn tại.
  CHECK ((content_version_seq = 1 AND change_kind = 'NEW') OR content_version_seq > 1)
);
CREATE INDEX ix_versions_hash ON source_record_versions(content_hash);

-- [G0B-C9] raw revision độc lập với content version: RAW_ONLY_CHANGE có content_version_id NULL.
-- [G0B-REDACT] redaction_policy_version nằm TRÊN revision, không chỉ trên object.
-- Lý do: đổi chính sách redaction làm đổi bytes dẫn xuất -> đổi record_object_sha, mà
-- nguồn không đổi gì cả. Nếu không phân biệt, một lần đổi chính sách tạo hàng loạt
-- "revision" giả, phình raw_revisions_count và khiến UI-07 hiện "raw đã đổi" vô nghĩa.
-- Quy tắc: RAW_ONLY_CHANGE = nguồn đổi, cùng policy. REPLAY = cùng nội dung nguồn, khác policy.
CREATE TABLE raw_revisions (
  id                       INTEGER PRIMARY KEY,
  source_record_id         INTEGER NOT NULL REFERENCES source_records(id),
  revision_no              INTEGER NOT NULL CHECK (revision_no >= 1),
  record_object_sha        TEXT NOT NULL REFERENCES raw_objects(sha256),
  content_version_id       INTEGER REFERENCES source_record_versions(id),
  reason                   TEXT NOT NULL CHECK (reason IN ('NEW_RECORD','SEMANTIC_CHANGE','RAW_ONLY_CHANGE','REPLAY')),
  redaction_policy_version INTEGER NOT NULL CHECK (redaction_policy_version >= 1),
  first_observed_at_utc    TEXT NOT NULL CHECK (first_observed_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  run_id                   TEXT NOT NULL,
  UNIQUE (source_record_id, revision_no),
  -- DEDUP-09: raw-only change KHÔNG được gắn content version.
  CHECK ((reason = 'RAW_ONLY_CHANGE' AND content_version_id IS NULL) OR reason <> 'RAW_ONLY_CHANGE'),
  -- REPLAY không bao giờ được nhân bản content version: nó là tái dẫn xuất, không phải thay đổi.
  CHECK (reason <> 'REPLAY' OR content_version_id IS NULL)
);

-- Revision mang tính nghiệp vụ = bằng chứng nguồn thật sự đổi.
CREATE VIEW business_raw_revisions AS
  SELECT * FROM raw_revisions WHERE reason IN ('NEW_RECORD','SEMANTIC_CHANGE','RAW_ONLY_CHANGE');

-- Revision chỉ do chính sách redaction — hiển thị riêng, không cộng vào counter nghiệp vụ.
CREATE VIEW policy_driven_revisions AS
  SELECT * FROM raw_revisions WHERE reason = 'REPLAY';

-- ---------------------------------------------------------------- 5. Unresolved (U-3, O-4)
-- Payload trích được record object nhưng không đủ identity. Không phải source_record,
-- không được đếm vào records_count, không âm thầm biến mất khỏi UI-09.
CREATE TABLE unresolved_records (
  id                    INTEGER PRIMARY KEY,
  run_id                TEXT NOT NULL,
  attempt_id            INTEGER NOT NULL REFERENCES fetch_attempts(id),
  observation_id        INTEGER REFERENCES observations(id),
  family                TEXT NOT NULL,
  record_object_sha     TEXT NOT NULL REFERENCES raw_objects(sha256),
  response_object_sha   TEXT REFERENCES raw_objects(sha256),
  record_locator        TEXT NOT NULL,
  reason                TEXT NOT NULL CHECK (reason IN ('MISSING_NATURAL_KEY','AMBIGUOUS_NATURAL_KEY','UNPARSEABLE_FIELD','VERSION_UNPARSABLE')),
  natural_key_candidate TEXT,
  raised_at_utc         TEXT NOT NULL CHECK (raised_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  resolved_at_utc       TEXT,
  resolved_record_id    INTEGER REFERENCES source_records(id),
  UNIQUE (record_object_sha, family, reason)
);

-- Response/page không parse được: quarantine MỨC PAGE (§5.5). Không record, không observation.
CREATE TABLE quarantined_responses (
  id                   INTEGER PRIMARY KEY,
  run_id               TEXT NOT NULL,
  attempt_id           INTEGER NOT NULL REFERENCES fetch_attempts(id),
  response_object_sha  TEXT NOT NULL REFERENCES raw_objects(sha256),
  failure              TEXT NOT NULL CHECK (failure IN ('GZIP_MEMBER_CORRUPT','NOT_JSON','SCHEMA_MISMATCH','TRUNCATED','EMPTY_BODY')),
  detail               TEXT NOT NULL,           -- đã scrub URL, không chứa credential
  quarantined_at_utc   TEXT NOT NULL CHECK (quarantined_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  UNIQUE (attempt_id, response_object_sha, failure)
);

-- ---------------------------------------------------------------- 6. Coverage
-- [G0B-C6] boundary rule là bảng riêng có ID + version, để proof trỏ vào một quy tắc
-- tái lập được, thay vì chỉ ghi cái tên kind.
CREATE TABLE filter_boundary_rules (
  id                INTEGER PRIMARY KEY,
  rule_key          TEXT NOT NULL,
  rule_version      INTEGER NOT NULL CHECK (rule_version >= 1),
  source_key        TEXT NOT NULL,
  family            TEXT NOT NULL,
  endpoint          TEXT NOT NULL,
  adapter_sha       TEXT NOT NULL,
  boundary_kind     TEXT NOT NULL CHECK (boundary_kind IN ('HALF_OPEN_UTC','CLOSED_LOCAL','CLOSED_UTC','OPEN_UTC','UNKNOWN')),
  precision         TEXT NOT NULL CHECK (precision IN ('DAY','HOUR','MINUTE','SECOND','MILLISECOND')),
  source_timezone   TEXT NOT NULL,              -- ví dụ 'Asia/Ho_Chi_Minh'
  offset_minutes    INTEGER NOT NULL DEFAULT 0, -- timezone nguồn, không phụ thuộc máy chủ
  conversion_params TEXT NOT NULL CHECK (conversion_params GLOB '{*'),  -- canonical JSON
  canary_verified   INTEGER NOT NULL DEFAULT 0 CHECK (canary_verified IN (0,1)),
  verified_at_utc   TEXT CHECK (verified_at_utc IS NULL OR verified_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  UNIQUE (rule_key, rule_version)
);

CREATE TABLE coverage_proofs (
  id                            INTEGER PRIMARY KEY,
  proof_version                 INTEGER NOT NULL CHECK (proof_version >= 1),
  source_key                    TEXT NOT NULL,
  family                        TEXT NOT NULL,
  interval_from_utc             TEXT NOT NULL CHECK (interval_from_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  interval_to_utc               TEXT NOT NULL CHECK (interval_to_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  granularity                   TEXT NOT NULL CHECK (granularity IN ('DAY','HOUR','MINUTE','SECOND','MILLISECOND')),
  boundary_rule_id              INTEGER NOT NULL REFERENCES filter_boundary_rules(id),
  source_query_fingerprint      TEXT NOT NULL,
  adapter_sha                   TEXT NOT NULL,
  request_count                 INTEGER NOT NULL CHECK (request_count >= 0),
  attempt_count                 INTEGER NOT NULL CHECK (attempt_count >= 0),
  attempt_error_count           INTEGER NOT NULL CHECK (attempt_error_count >= 0),  -- vận hành, KHÔNG chặn
  unrecovered_request_count     INTEGER NOT NULL CHECK (unrecovered_request_count >= 0),
  malformed_count               INTEGER NOT NULL CHECK (malformed_count >= 0),
  distinct_ids                  INTEGER NOT NULL CHECK (distinct_ids >= 0),
  ids_union_hash                TEXT NOT NULL,
  total_first                   INTEGER,
  total_last                    INTEGER,
  total_trusted                 INTEGER NOT NULL CHECK (total_trusted >= 0),
  terminal_evidence_json        TEXT NOT NULL CHECK (terminal_evidence_json GLOB '{*'),
  page_size                     INTEGER NOT NULL CHECK (page_size > 0),
  max_pages_hit                 INTEGER NOT NULL CHECK (max_pages_hit >= 0),
  max_records_hit               INTEGER NOT NULL CHECK (max_records_hit >= 0),
  verified_at_utc               TEXT NOT NULL CHECK (verified_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  verified_by_run_id            TEXT NOT NULL,
  CHECK (interval_from_utc < interval_to_utc),
  -- §4.1: COMPLETE đòi unrecovered=0, malformed=0, max_*_hit=0, total_trusted=1.
  -- Đưa vào CHECK để một row 'COMPLETE' sai điều kiện bị DB chặn, không chờ UI hiển thị nhầm.
  CHECK (
    proof_version <> 1
    OR (
      unrecovered_request_count = 0 AND malformed_count = 0
      AND max_pages_hit = 0 AND max_records_hit = 0 AND total_trusted = 1
    )
  )
);

-- Bằng chứng biên phải thật sự được canary. SQLite cấm subquery trong CHECK
-- ("subqueries prohibited in CHECK constraints" — đo trên SQLite 3.4x), nên quy tắc
-- cross-table này nằm ở trigger: vẫn là DB-enforced, và không phải nhân bản
-- canary_verified sang coverage_proofs (thứ sẽ tự mâu thuẫn với bảng rule).
CREATE TRIGGER trg_proof_boundary_canary
BEFORE INSERT ON coverage_proofs
BEGIN
  SELECT CASE
    WHEN NOT EXISTS (SELECT 1 FROM filter_boundary_rules r
                      WHERE r.id = NEW.boundary_rule_id AND r.canary_verified = 1)
    THEN RAISE(ABORT, 'coverage proof requires a canary-verified filter boundary rule')
  END;
  SELECT CASE
    WHEN NEW.adapter_sha <> (SELECT adapter_sha FROM filter_boundary_rules WHERE id = NEW.boundary_rule_id)
    THEN RAISE(ABORT, 'proof adapter_sha must equal the boundary rule it cites')
  END;
END;

-- [G0B-C10] coverage_intervals khai báo SAU coverage_proofs để proof được insert trước,
-- đúng thứ tự §4.1 mục 8 yêu cầu trong cùng một transaction.
CREATE TABLE coverage_intervals (
  id                INTEGER PRIMARY KEY,
  source_key        TEXT NOT NULL,
  family            TEXT NOT NULL,
  interval_from_utc TEXT NOT NULL CHECK (interval_from_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  interval_to_utc   TEXT NOT NULL CHECK (interval_to_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  granularity       TEXT NOT NULL CHECK (granularity IN ('DAY','HOUR','MINUTE','SECOND','MILLISECOND')),
  -- [G0B-C2] từ vựng theo tầng, có CHECK. BUG-03 không tái phát được nữa.
  status            TEXT NOT NULL CHECK (status IN ('COMPLETE','PARTIAL','FAILED','BLOCKED','INTERRUPTED')),
  proof_id          INTEGER REFERENCES coverage_proofs(id),
  run_id            TEXT NOT NULL,
  created_at_utc    TEXT NOT NULL CHECK (created_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  CHECK (interval_from_utc < interval_to_utc),
  -- COMPLETE mà không có proof là đúng cái lỗ BUG-15: không gì ghi proof_version != 1.
  CHECK (status <> 'COMPLETE' OR proof_id IS NOT NULL)
);
CREATE INDEX ix_cov ON coverage_intervals(source_key, family, interval_from_utc, interval_to_utc);

-- [G0B-IMMUT] Một rule đã bị proof lịch sử tham chiếu thì không sửa tại chỗ được.
-- Proof là bằng chứng; nếu rule nó viện dẫn có thể đổi âm thầm, thì proof cũ nói về một
-- quy tắc không còn tồn tại và COV-17 mất khả năng tái hiện. Muốn đổi: INSERT rule_version
-- mới, proof mới trỏ vào đó. Rule chưa bị references vẫn sửa được (draft).
CREATE TRIGGER trg_boundary_rule_immutable_after_use
BEFORE UPDATE ON filter_boundary_rules
BEGIN
  SELECT CASE WHEN EXISTS (SELECT 1 FROM coverage_proofs p WHERE p.boundary_rule_id = OLD.id)
    THEN RAISE(ABORT, 'filter boundary rule is cited by a proof; insert a new rule_version instead of updating')
  END;
END;

CREATE TRIGGER trg_boundary_rule_no_delete_after_use
BEFORE DELETE ON filter_boundary_rules
BEGIN
  SELECT CASE WHEN EXISTS (SELECT 1 FROM coverage_proofs p WHERE p.boundary_rule_id = OLD.id)
    THEN RAISE(ABORT, 'cannot delete a filter boundary rule cited by a proof')
  END;
END;

-- Cùng lý do cho chính proof: bằng chứng đã ghi là bất biến.
CREATE TRIGGER trg_proof_immutable
BEFORE UPDATE ON coverage_proofs
BEGIN
  SELECT CASE WHEN EXISTS (SELECT 1 FROM coverage_intervals i WHERE i.proof_id = OLD.id)
    THEN RAISE(ABORT, 'a coverage proof referenced by an interval is immutable')
  END;
END;

CREATE TRIGGER trg_proof_no_delete
BEFORE DELETE ON coverage_proofs
BEGIN
  SELECT CASE WHEN EXISTS (SELECT 1 FROM coverage_intervals i WHERE i.proof_id = OLD.id)
    THEN RAISE(ABORT, 'cannot delete a coverage proof referenced by an interval')
  END;
END;

-- ---------------------------------------------------------------- 7. Deep capture (O-5)
-- Lịch sử bất biến tách khỏi latest state. Refresh INSERT một capture mới và cập
-- con trỏ latest; không UPDATE xoá lịch sử.
CREATE TABLE deep_captures (
  id                   INTEGER PRIMARY KEY,
  source_record_id     INTEGER NOT NULL REFERENCES source_records(id),
  lane                 TEXT NOT NULL CHECK (lane IN ('DETAIL','HSMT','REOFFER','KQLCNT_FULL','BOQ','OTHER')),
  endpoint_kind        TEXT NOT NULL,
  latest_capture_seq   INTEGER NOT NULL DEFAULT 1 CHECK (latest_capture_seq >= 1),
  completeness         TEXT NOT NULL CHECK (completeness IN ('FULL','PARTIAL','NOT_COLLECTED')),
  captured_at_utc      TEXT NOT NULL CHECK (captured_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  refresh_due_at_utc   TEXT CHECK (refresh_due_at_utc IS NULL OR refresh_due_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  stale                INTEGER NOT NULL DEFAULT 0 CHECK (stale IN (0,1)),
  stale_reason         TEXT CHECK (stale_reason IS NULL OR stale_reason IN ('SOURCE_VERSION_CHANGED','REFRESH_INTERVAL_ELAPSED','RECONCILE_MISMATCH','QUARANTINED_MEMBER')),
  source_version_json  TEXT NOT NULL CHECK (source_version_json GLOB '{*'),  -- decisionVersion/resultVersion/notifyVersion
  response_object_sha  TEXT NOT NULL REFERENCES raw_objects(sha256),
  record_object_sha    TEXT NOT NULL REFERENCES raw_objects(sha256),
  content_hash         TEXT NOT NULL CHECK (content_hash GLOB 'ch1:*'),
  lane_status          TEXT NOT NULL CHECK (lane_status IN ('NOT_ATTEMPTED','IN_PROGRESS','FULL','PARTIAL','FAILED','BLOCKED','STALE','LEGACY_PARTIAL')),
  UNIQUE (source_record_id, lane, endpoint_kind),
  CHECK (stale = 0 OR stale_reason IS NOT NULL),
  CHECK (latest_capture_seq >= 1)
);

CREATE TABLE deep_capture_history (
  id                  INTEGER PRIMARY KEY,
  deep_capture_id     INTEGER NOT NULL REFERENCES deep_captures(id),
  capture_seq         INTEGER NOT NULL CHECK (capture_seq >= 1),
  captured_at_utc     TEXT NOT NULL CHECK (captured_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  response_object_sha TEXT NOT NULL REFERENCES raw_objects(sha256),
  record_object_sha   TEXT NOT NULL REFERENCES raw_objects(sha256),
  content_hash        TEXT NOT NULL CHECK (content_hash GLOB 'ch1:*'),
  completeness        TEXT NOT NULL CHECK (completeness IN ('FULL','PARTIAL','NOT_COLLECTED')),
  source_version_json TEXT NOT NULL CHECK (source_version_json GLOB '{*'),
  captured_by_run_id  TEXT NOT NULL,
  UNIQUE (deep_capture_id, capture_seq)
);

-- latest trỏ vào một capture_seq tồn tại trong history -> refresh không thể xoá chứng cứ.
CREATE VIEW deep_capture_latest AS
  SELECT dc.id AS deep_capture_id, dc.source_record_id, dc.lane, dc.endpoint_kind,
         dc.completeness, dc.stale, dc.refresh_due_at_utc, h.captured_at_utc,
         h.response_object_sha, h.record_object_sha, h.content_hash, h.source_version_json
  FROM deep_captures dc
  JOIN deep_capture_history h
    ON h.deep_capture_id = dc.id AND h.capture_seq = dc.latest_capture_seq;

-- ---------------------------------------------------------------- 8. Receipts / watermarks (U-3)
CREATE TABLE page_receipts (
  id                  INTEGER PRIMARY KEY,
  run_id              TEXT NOT NULL,
  request_id          INTEGER NOT NULL REFERENCES fetch_requests(id),
  attempt_id          INTEGER NOT NULL REFERENCES fetch_attempts(id),
  response_object_sha TEXT NOT NULL REFERENCES raw_objects(sha256),
  page_no             INTEGER NOT NULL CHECK (page_no >= 0),
  records_seen        INTEGER NOT NULL CHECK (records_seen >= 0),
  records_new         INTEGER NOT NULL CHECK (records_new >= 0),
  records_updated     INTEGER NOT NULL CHECK (records_updated >= 0),
  records_raw_only    INTEGER NOT NULL CHECK (records_raw_only >= 0),
  records_unresolved  INTEGER NOT NULL CHECK (records_unresolved >= 0),
  response_received   INTEGER NOT NULL DEFAULT 0 CHECK (response_received IN (0,1)),
  page_committed      INTEGER NOT NULL DEFAULT 0 CHECK (page_committed IN (0,1)),
  proof_verified      INTEGER NOT NULL DEFAULT 0 CHECK (proof_verified IN (0,1)),
  snapshot_published  INTEGER NOT NULL DEFAULT 0 CHECK (snapshot_published IN (0,1)),
  committed_at_utc    TEXT CHECK (committed_at_utc IS NULL OR committed_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  UNIQUE (run_id, request_id),
  -- §2.3: receipt tách rời từng bước, và các bước phải đơn điệu.
  CHECK (NOT (page_committed = 1 AND response_received = 0)),
  CHECK (NOT (proof_verified = 1 AND page_committed = 0)),
  CHECK (NOT (snapshot_published = 1 AND proof_verified = 0))
);

CREATE TABLE watermarks (
  id                INTEGER PRIMARY KEY,
  source_key        TEXT NOT NULL,
  family            TEXT NOT NULL,
  watermark_from_utc TEXT CHECK (watermark_from_utc IS NULL OR watermark_from_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  watermark_to_utc  TEXT NOT NULL CHECK (watermark_to_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  -- §4.4: watermark chỉ tiến qua interval COMPLETE.
  last_complete_interval_id INTEGER REFERENCES coverage_intervals(id),
  updated_at_utc    TEXT NOT NULL CHECK (updated_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  UNIQUE (source_key, family),
  CHECK (watermark_from_utc IS NULL OR watermark_from_utc <= watermark_to_utc)
);

CREATE TABLE reconcile_gaps (
  id                 INTEGER PRIMARY KEY,
  run_id             TEXT NOT NULL,
  source_key         TEXT NOT NULL,
  family             TEXT NOT NULL,
  interval_from_utc  TEXT NOT NULL,
  interval_to_utc    TEXT NOT NULL,
  -- §4.4: RECONCILE chỉ khẳng định MISSING_FROM_SOURCE_IN_VERIFIED_WINDOW, không xoá record.
  kind               TEXT NOT NULL CHECK (kind IN ('MISSING_FROM_SOURCE_IN_VERIFIED_WINDOW','HASH_DRIFT','COUNT_DRIFT','GRANULARITY_DRIFT')),
  source_record_id   INTEGER REFERENCES source_records(id),
  proof_id           INTEGER REFERENCES coverage_proofs(id),
  evidence_json      TEXT NOT NULL CHECK (evidence_json GLOB '{*'),
  detected_at_utc    TEXT NOT NULL CHECK (detected_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  acknowledged_at_utc TEXT,
  CHECK (interval_from_utc < interval_to_utc)
);
CREATE INDEX ix_gaps_open ON reconcile_gaps(acknowledged_at_utc) WHERE acknowledged_at_utc IS NULL;

-- ---------------------------------------------------------------- 9. Money / lots / goods (U-2)
-- [G0B-U2] Nguyên tắc: UNKNOWN ≠ 0 (§0.2 mục 5). Mọi trường số đều có presence_state
-- và CHECK buộc value IS NULL khi MISSING/UNPARSABLE. Không DEFAULT 0 ở bất kỳ cột nào.
CREATE TABLE money_amounts (
  id                 INTEGER PRIMARY KEY,
  owner_kind         TEXT NOT NULL CHECK (owner_kind IN ('PACKAGE','LOT','GOODS_ROW','CONTRACT','OFFER')),
  owner_id           INTEGER NOT NULL,
  role               TEXT NOT NULL CHECK (role IN ('PACKAGE_VALUE','BID_PRICE','WIN_PRICE','UNIT_PRICE','CONTRACT_VALUE','BUDGET','ADJUSTMENT')),
  presence_state     TEXT NOT NULL CHECK (presence_state IN ('KNOWN','MISSING','UNPARSABLE')),
  value              NUMERIC,                       -- không DEFAULT; NULL nghĩa là chưa biết
  value_raw          TEXT,                          -- chuỗi gốc từ nguồn, giữ định dạng
  currency           TEXT NOT NULL CHECK (currency IN ('KNOWN','MISSING','UNPARSABLE')),
  currency_code      TEXT CHECK (currency_code IS NULL OR length(currency_code) = 3),
  tax_basis          TEXT NOT NULL CHECK (tax_basis IN ('INCLUDING_TAX','EXCLUDING_TAX','UNKNOWN','NOT_APPLICABLE')),
  scale_exp          INTEGER CHECK (scale_exp IS NULL OR scale_exp BETWEEN -6 AND 12), -- 1.000.000.000 vs 1e9
  precision_digits   INTEGER CHECK (precision_digits IS NULL OR precision_digits >= 0),
  provenance_json    TEXT NOT NULL CHECK (provenance_json GLOB '{*'),  -- endpoint + locator + adapter_sha
  observed_at_utc    TEXT NOT NULL CHECK (observed_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  UNIQUE (owner_kind, owner_id, role, currency_code, tax_basis),
  CHECK (
    (presence_state = 'KNOWN' AND value IS NOT NULL AND currency = 'KNOWN' AND currency_code IS NOT NULL)
    OR (presence_state IN ('MISSING','UNPARSABLE') AND value IS NULL)
  ),
  CHECK (presence_state <> 'MISSING' OR value_raw IS NULL)
);
CREATE INDEX ix_money_owner ON money_amounts(owner_kind, owner_id);

CREATE TABLE lots (
  id                 INTEGER PRIMARY KEY,
  source_record_id   INTEGER NOT NULL REFERENCES source_records(id),
  lot_no_raw         TEXT NOT NULL,
  lot_label          TEXT,
  lot_version        TEXT,
  goods_count        INTEGER CHECK (goods_count IS NULL OR goods_count >= 0),
  provenance_json    TEXT NOT NULL CHECK (provenance_json GLOB '{*'),
  UNIQUE (source_record_id, lot_no_raw)
);

CREATE TABLE goods_rows (
  id                 INTEGER PRIMARY KEY,
  lot_id             INTEGER REFERENCES lots(id),
  source_record_id   INTEGER NOT NULL REFERENCES source_records(id),
  row_ordinal        INTEGER NOT NULL CHECK (row_ordinal >= 0),
  name               TEXT,
  specification      TEXT,
  unit               TEXT,
  quantity_presence  TEXT NOT NULL CHECK (quantity_presence IN ('KNOWN','MISSING','UNPARSABLE')),
  quantity           NUMERIC,
  quantity_raw       TEXT,
  provenance_json    TEXT NOT NULL CHECK (provenance_json GLOB '{*'),
  UNIQUE (source_record_id, row_ordinal),
  CHECK ((quantity_presence = 'KNOWN' AND quantity IS NOT NULL) OR (quantity_presence <> 'KNOWN' AND quantity IS NULL))
);

CREATE TABLE contractor_memberships (
  id                   INTEGER PRIMARY KEY,
  source_record_id     INTEGER NOT NULL REFERENCES source_records(id),
  party_object_sha     TEXT NOT NULL REFERENCES raw_objects(sha256),
  party_name           TEXT NOT NULL,
  party_tax_id_presence TEXT NOT NULL CHECK (party_tax_id_presence IN ('KNOWN','MISSING','UNPARSABLE')),
  party_tax_id         TEXT,
  role                 TEXT NOT NULL CHECK (role IN ('WINNER','MEMBER','SUBCONTRACTOR','OFFEROR','UNKNOWN')),
  membership_share_presence TEXT NOT NULL CHECK (membership_share_presence IN ('KNOWN','MISSING','UNPARSABLE')),
  membership_share     NUMERIC,
  ranking              INTEGER CHECK (ranking IS NULL OR ranking >= 1),
  provenance_json      TEXT NOT NULL CHECK (provenance_json GLOB '{*'),
  UNIQUE (source_record_id, party_object_sha, role),
  CHECK ((party_tax_id_presence = 'KNOWN') = (party_tax_id IS NOT NULL)),
  CHECK ((membership_share_presence = 'KNOWN') = (membership_share IS NOT NULL))
);

CREATE TABLE documents (
  id                  INTEGER PRIMARY KEY,
  source_record_id    INTEGER NOT NULL REFERENCES source_records(id),
  document_kind       TEXT NOT NULL,
  process_apply       TEXT,
  name                TEXT NOT NULL,
  media_type          TEXT,
  byte_len            INTEGER CHECK (byte_len IS NULL OR byte_len >= 0),
  object_sha          TEXT REFERENCES raw_objects(sha256),
  storage_rel_path    TEXT,
  provenance_json     TEXT NOT NULL CHECK (provenance_json GLOB '{*'),
  UNIQUE (source_record_id, document_kind, name)
);

-- ---------------------------------------------------------------- 10. Identity graph
CREATE TABLE canonical_entities (
  id                 TEXT PRIMARY KEY,
  entity_kind        TEXT NOT NULL CHECK (entity_kind IN ('TENDER_PACKAGE','PLAN','PROJECT','CONTRACT','RESULT','PARTY','GOODS')),
  label              TEXT,
  resolution_state   TEXT NOT NULL CHECK (resolution_state IN ('EXACT','LINKED','AMBIGUOUS','CONFLICT')),
  created_at_utc     TEXT NOT NULL CHECK (created_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z')
);

CREATE TABLE entity_source_links (
  entity_id          TEXT NOT NULL REFERENCES canonical_entities(id),
  source_record_id   INTEGER NOT NULL REFERENCES source_records(id),
  relation           TEXT NOT NULL CHECK (relation IN ('SAME_AS','PLAN_HAS_NOTICE','NOTICE_HAS_RESULT','RESULT_HAS_CONTRACT','NOTICE_HAS_HSMT','EXECUTED_BY','OFFERED_BY')),
  method             TEXT NOT NULL CHECK (method IN ('EXACT_NATURAL_KEY','ALIAS','MANUAL','GRAPH')),
  evidence_json      TEXT NOT NULL CHECK (evidence_json GLOB '{*'),
  verified_at_utc    TEXT CHECK (verified_at_utc IS NULL OR verified_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  PRIMARY KEY (entity_id, source_record_id, relation)
);

CREATE TABLE notice_groups (
  id              TEXT PRIMARY KEY,
  family          TEXT NOT NULL,
  group_kind      TEXT NOT NULL CHECK (group_kind IN ('PUBLICATION_VERSIONS','SAME_NOTICE_OTHER_CHANNEL')),
  evidence_json   TEXT NOT NULL CHECK (evidence_json GLOB '{*'),
  created_at_utc  TEXT NOT NULL CHECK (created_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z')
);

CREATE TABLE notice_group_members (
  group_id             TEXT NOT NULL REFERENCES notice_groups(id),
  source_record_id     INTEGER NOT NULL REFERENCES source_records(id),
  publication_label    TEXT,      -- notifyVersion gốc, chỉ để hiển thị
  UNIQUE (group_id, source_record_id),
  -- Một record chỉ thuộc một nhóm theo một natural key; nhóm không bao giờ merge record.
  CHECK (publication_label IS NULL OR length(publication_label) <= 16)
);

CREATE TABLE source_aliases (
  source_key           TEXT NOT NULL,
  family               TEXT NOT NULL,
  alias_kind           TEXT NOT NULL,
  alias_value          TEXT NOT NULL,
  source_record_id     INTEGER NOT NULL REFERENCES source_records(id),
  evidence_json        TEXT NOT NULL CHECK (evidence_json GLOB '{*'),
  UNIQUE (source_key, family, alias_kind, alias_value, source_record_id)
);

CREATE TABLE relation_edges (
  id             INTEGER PRIMARY KEY,
  from_entity    TEXT NOT NULL REFERENCES canonical_entities(id),
  to_entity      TEXT NOT NULL REFERENCES canonical_entities(id),
  relation       TEXT NOT NULL,
  evidence_json  TEXT NOT NULL CHECK (evidence_json GLOB '{*'),
  confidence     TEXT NOT NULL CHECK (confidence IN ('EXACT','LINKED','CANDIDATE')),
  CHECK (from_entity <> to_entity)
);

-- ---------------------------------------------------------------- 11. Operational
CREATE TABLE source_registry (
  source_key       TEXT PRIMARY KEY,
  display_name     TEXT NOT NULL,
  endpoint_family  TEXT NOT NULL,
  access_state     TEXT NOT NULL CHECK (access_state IN ('ALLOWED_AND_VERIFIED','NEEDS_USER_SESSION','UNVERIFIED','DISABLED','BLOCKED')),
  verified_at_utc  TEXT CHECK (verified_at_utc IS NULL OR verified_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  probe_uri        TEXT,                 -- không chứa secret; §6 buộc scrub
  sample_sha       TEXT REFERENCES raw_objects(sha256),
  notes            TEXT
);

-- [G0B-B7] §6 yêu cầu scrub URL khỏi MỌI reason/report/manifest, và code cũ từng ghi token
-- vào coverage_windows.reason. Lý do BLOCKED vẫn phải ghi được (UI-03 cần hiện, owner cần
-- biết vì sao lane dừng), nên nó nằm ở một bảng riêng với reason_kind đóng — không có cột
-- tự do nào để ai đó nhét cả cái URL có token vào.
CREATE TABLE source_access_events (
  id             INTEGER PRIMARY KEY,
  source_key     TEXT NOT NULL REFERENCES source_registry(source_key),
  family         TEXT,
  from_state     TEXT CHECK (from_state IS NULL OR from_state IN ('ALLOWED_AND_VERIFIED','NEEDS_USER_SESSION','UNVERIFIED','DISABLED','BLOCKED')),
  to_state       TEXT NOT NULL CHECK (to_state IN ('ALLOWED_AND_VERIFIED','NEEDS_USER_SESSION','UNVERIFIED','DISABLED','BLOCKED')),
  reason_kind    TEXT NOT NULL CHECK (reason_kind IN ('HTTP_401','HTTP_403','CAPTCHA','WAF','RATE_LIMIT_429','TIMEOUT','TLS_FAILURE','REDIRECT_OFF_ALLOWLIST','USER_DISABLED','CANARY_FAILED','POLICY')),
  http_status    INTEGER,
  retry_after_s  INTEGER CHECK (retry_after_s IS NULL OR retry_after_s >= 0),
  detail_scrubbed TEXT NOT NULL DEFAULT 'none',
  probe_uri      TEXT CHECK (probe_uri IS NULL OR probe_uri NOT GLOB '*[*?]*'),  -- cấm query string: nơi token hay đi kèm
  adapter_sha    TEXT,
  observed_at_utc TEXT NOT NULL CHECK (observed_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  run_id         TEXT
);
CREATE INDEX ix_access_events_recent ON source_access_events(source_key, observed_at_utc);

CREATE TABLE adapter_health (  source_key       TEXT NOT NULL REFERENCES source_registry(source_key),
  family           TEXT NOT NULL,
  adapter_sha      TEXT NOT NULL,
  last_canary_ok_at_utc TEXT CHECK (last_canary_ok_at_utc IS NULL OR last_canary_ok_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
  last_error_kind  TEXT,
  updated_at_utc   TEXT NOT NULL CHECK (updated_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  PRIMARY KEY (source_key, family, adapter_sha)
);

-- [G0B-LIFECYCLE] §3.6 định nghĩa run_status là KẾT QUẢ (COMPLETED/PARTIAL/...), còn §2.3
-- định nghĩa PAUSE/RESUME/STOP là TRẠNG THÁI ĐIỀU KHIỂN. Hai trục này khác nhau và trước đây
-- chỉ có một cột, nên worker ghi 'PAUSED' sẽ vỡ CHECK của 'status'.
-- Tách thành: lifecycle (điều khiển, luôn có giá trị) và status (kết quả, chỉ có khi đã dừng).
CREATE TABLE runs (
  run_id        TEXT PRIMARY KEY,
  job_id        TEXT NOT NULL,
  job_kind      TEXT NOT NULL CHECK (job_kind IN ('INDEX','BACKFILL','INCREMENTAL','RECONCILE','DETAIL','HSMT','REOFFER','VERIFY','IMPORT','EXPORT','BACKUP')),
  lifecycle     TEXT NOT NULL CHECK (lifecycle IN ('PENDING','RUNNING','PAUSING','PAUSED','RESUMING','STOPPING','FINISHED','TERMINATED')),
  status        TEXT CHECK (status IN ('COMPLETED','PARTIAL','FAILED','BLOCKED','INTERRUPTED')),
  started_at_utc TEXT NOT NULL CHECK (started_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  finished_at_utc TEXT CHECK (finished_at_utc IS NULL OR finished_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  policy_version INTEGER NOT NULL CHECK (policy_version >= 1),
  requested_by  TEXT NOT NULL,
  -- Kết quả chỉ tồn tại khi run đã dừng hẳn; và ngược lại run đã dừng phải có kết quả.
  CHECK (
    (lifecycle IN ('FINISHED','TERMINATED')) = (status IS NOT NULL)
  ),
  CHECK (status IS NULL OR finished_at_utc IS NOT NULL),
  -- §2.3: process chết -> INTERRUPTED/RECOVERABLE, không tự đổi thành COMPLETED.
  CHECK (status <> 'COMPLETED' OR lifecycle = 'FINISHED')
);

CREATE TABLE run_tasks (
  id          INTEGER PRIMARY KEY,
  run_id      TEXT NOT NULL REFERENCES runs(run_id),
  task_kind   TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('COMPLETED','PARTIAL','FAILED','BLOCKED','INTERRUPTED','NOT_ATTEMPTED')),
  detail_json TEXT CHECK (detail_json IS NULL OR detail_json GLOB '{*'),
  UNIQUE (run_id, task_kind)
);

CREATE TABLE job_commands (
  job_id         TEXT NOT NULL,
  seq            INTEGER NOT NULL CHECK (seq >= 1),
  command        TEXT NOT NULL CHECK (command IN ('START','PAUSE','RESUME','STOP_GRACEFUL','FORCE_TERMINATE','GET_STATUS')),
  issued_at_utc  TEXT NOT NULL CHECK (issued_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  issued_by_sid  TEXT NOT NULL,
  acknowledged_at_utc TEXT CHECK (acknowledged_at_utc IS NULL OR acknowledged_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  PRIMARY KEY (job_id, seq)
);

-- [G0B-D2] D-2: AMBIGUOUS không tự leo lên LINKED/EXACT bằng ngưỡng confidence.
-- Chỉ hai đường hợp lệ: bằng chứng định danh chính thức, hoặc xác nhận thủ công có audit trail.
-- Bảng này là chỗ ghi audit trail mà §3.8 đòi ("người thao tác, lý do, chứng cứ, undo").
CREATE TABLE identity_audit_actions (
  id              INTEGER PRIMARY KEY,
  action          TEXT NOT NULL CHECK (action IN ('CONFIRM_LINK','REJECT_LINK','MERGE','SPLIT','UNDO')),
  target_kind     TEXT NOT NULL CHECK (target_kind IN ('relation_edge','entity_source_link','canonical_entity','notice_group_member')),
  target_ref      TEXT NOT NULL,
  actor_sid       TEXT NOT NULL,          -- SID người thao tác, không phải tên tự khai
  reason          TEXT NOT NULL,
  evidence_json   TEXT NOT NULL CHECK (evidence_json GLOB '{*'),
  undo_action_id  INTEGER REFERENCES identity_audit_actions(id),
  acted_at_utc    TEXT NOT NULL CHECK (acted_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z')
);

-- CANDIDATE chỉ được leo thang khi có audit row cùng lúc. Trigger nhìn trong cùng
-- transaction nên một writer "quên" ghi audit sẽ bị chặn thay vì âm thầm gộp nhầm.
CREATE TRIGGER trg_edge_promotion_requires_audit
BEFORE UPDATE OF confidence ON relation_edges
BEGIN
  SELECT CASE
    WHEN OLD.confidence = 'CANDIDATE' AND NEW.confidence IN ('EXACT','LINKED')
         AND NOT EXISTS (
           SELECT 1 FROM identity_audit_actions a
           WHERE a.target_kind = 'relation_edge'
             AND a.target_ref = CAST(NEW.id AS TEXT)
             AND a.action IN ('CONFIRM_LINK','MERGE')
         )
    THEN RAISE(ABORT, 'CANDIDATE edge cannot be promoted without an identity_audit_actions row')
  END;
END;

-- Cùng nguyên tắc cho entity_source_link: method EXACT/LINKED phải có chứng cứ,
-- và không được tự nâng từ CANDIDATE.
CREATE TRIGGER trg_link_promotion_requires_audit
BEFORE UPDATE OF method ON entity_source_links
BEGIN
  SELECT CASE
    WHEN NEW.method IN ('EXACT_NATURAL_KEY','ALIAS','GRAPH')
         AND NOT EXISTS (
           SELECT 1 FROM identity_audit_actions a
           WHERE a.target_kind = 'entity_source_link'
             AND a.target_ref = NEW.entity_id || '|' || CAST(NEW.source_record_id AS TEXT)
         )
    THEN RAISE(ABORT, 'entity_source_link method change requires an identity_audit_actions row')
  END;
END;

CREATE TABLE import_batches (
  id             INTEGER PRIMARY KEY,
  batch_key      TEXT NOT NULL UNIQUE,
  source_path    TEXT NOT NULL,
  manifest_sha   TEXT NOT NULL,
  rows_seen      INTEGER NOT NULL CHECK (rows_seen >= 0),
  rows_imported  INTEGER NOT NULL CHECK (rows_imported >= 0),
  rows_rejected  INTEGER NOT NULL CHECK (rows_rejected >= 0),
  state          TEXT NOT NULL CHECK (state IN ('PENDING','VALIDATED','IMPORTED','REJECTED','ROLLED_BACK')),
  imported_at_utc TEXT CHECK (imported_at_utc IS NULL OR imported_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z')
);

CREATE TABLE integrity_repairs (
  id             INTEGER PRIMARY KEY,
  target_kind    TEXT NOT NULL CHECK (target_kind IN ('OBJECT_FILE_ORPHAN','OBJECT_ROW_ORPHAN','REF','HASH')),
  target_ref     TEXT NOT NULL,
  finding        TEXT NOT NULL,
  action         TEXT NOT NULL CHECK (action IN ('KEEP','GC','QUARANTINE','MARK_CORRUPT','REPARSE')),
  acted_at_utc   TEXT NOT NULL CHECK (acted_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  run_id         TEXT NOT NULL,
  detail_json    TEXT CHECK (detail_json IS NULL OR detail_json GLOB '{*')
);

CREATE TABLE software_migrations (
  version    INTEGER PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  applied_at_utc TEXT NOT NULL CHECK (applied_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  checksum   TEXT NOT NULL
);

CREATE TABLE serving_meta (
  id                 INTEGER PRIMARY KEY,
  snapshot_built_at_utc TEXT NOT NULL CHECK (snapshot_built_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  source_warehouse_sha  TEXT NOT NULL,
  row_counts_json    TEXT NOT NULL CHECK (row_counts_json GLOB '{*'),
  stale              INTEGER NOT NULL DEFAULT 0 CHECK (stale IN (0,1)),
  stale_reason       TEXT
);

CREATE TABLE settings (
  key        TEXT PRIMARY KEY,
  value_json TEXT NOT NULL CHECK (value_json GLOB '{*'),
  updated_at_utc TEXT NOT NULL CHECK (updated_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z')
);

-- ---------------------------------------------------------------- 12. Metrics (§7.3)
-- Tách ba counter: records / content versions / raw revisions / observations.
CREATE VIEW metric_counts AS
  SELECT
    (SELECT COUNT(*) FROM source_records)        AS records_count,
    (SELECT COUNT(*) FROM source_record_versions) AS content_versions_count,
    -- Counter nghiệp vụ chỉ đếm revision do nguồn đổi (§7.3). Revision do đổi chính sách
    -- redaction hiển thị ở cột riêng, không được lẫn vào "raw revisions".
    (SELECT COUNT(*) FROM business_raw_revisions) AS raw_revisions_count,
    (SELECT COUNT(*) FROM policy_driven_revisions) AS policy_driven_revisions_count,
    (SELECT COUNT(*) FROM observations)           AS observations_count,
    (SELECT COUNT(*) FROM unresolved_records WHERE resolved_at_utc IS NULL) AS unresolved_count;

-- [G0B-C5] Orphan suy ra từ tham chiếu, không lưu trạng thái dư.
-- Hai loại mồ côi ở §5.2 KHÔNG đối xứng về khả năng phát hiện:
--   * ROW mồ côi  -> SQL thấy được, view này phục vụ nó. Chính sách: GIỮ, chỉ GC theo tuổi.
--   * FILE mồ côi -> file tồn tại mà không có row. SQL không thể biết, vì nó chỉ thấy row.
--     Loại này thuộc bước recovery quét filesystem (§5.2 bước 5), không phải truy vấn DB.
-- Đặt tên là rows chứ không phải files để không ai gọi view này rồi tưởng đã quét xong GC.
CREATE VIEW orphan_object_rows AS
  SELECT r.sha256, r.storage_rel_path, r.created_at_utc
  FROM raw_objects r
  WHERE NOT EXISTS (SELECT 1 FROM raw_object_roles o WHERE o.sha256 = r.sha256);
