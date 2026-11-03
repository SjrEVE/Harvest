# G0-B-CLOSURE — Săn Thầu Data Studio

**Ngày:** 03/11/2026 · **Spec nền:** Master Spec Consolidated **v1.3** (vẫn là source of truth; không tạo bản song song)
**Phạm vi thao tác:** tài liệu data contract, DDL đề xuất, script kiểm tra, contract test — **local workspace**.
**Không làm:** migration production, port engine G1, push/PR/deploy, commit (48 file staged từ G0-A vẫn chưa commit).

## 0. Kết luận trước

| | |
|---|---|
| **G1 gate** | **READY — có điều kiện** (xem §7). Không còn FAIL nào trên contract check; 58 contract test xanh trên SQLite thật |
| Điều kiện | 3 quyết định còn mở (D-1, D-2, D-3 ở §6) — **không chặn** port, nhưng chặn **freeze DDL** |
| A1 | vẫn **BLOCKED** — chờ owner thu hồi token. A8: **VM sạch NOT_TESTED** |

Số đo:

```
ddl_contract_check  baseline §3.10 : PASS 12 / GAP  8 / FAIL 19
ddl_contract_check  G0-B proposal  : PASS 39 / GAP  0 / FAIL  0
pytest (toàn bộ)                    : 122 passed   (58 là contract test, chạy 0.3 s)
ruff check src tools tests          : All checks passed
secret-gate trên 55 file staged     : 0 findings
DDL thực thi trên SQLite 3.53.1     : 39 bảng, 4 view, 1 trigger, 43 index, 51 FK
```

---

## 1. Quyết định owner đã triển khai vào contract

### O-3 — Identity

| Chỉ thị | Triển khai | Bằng chứng |
|---|---|---|
| Phương án A: TBMT `natural_key = notifyNo`, version lưu riêng | `source_records.natural_key` chỉ chứa `notifyNo`; version ở `version_state`/`version_raw` | `docs/DATA_CONTRACT.md` §1.2 — đối chiếu `egp_home.py:235,244,265,283,300,315` |
| Canonical serialization có version, không nối delimiter | `domain/identity.py`: JSON array length-tagged → `sha256` → prefix `id1:`; `IDENTITY_SCHEMA_VERSION = 1` | `test_delimiter_in_source_value_cannot_shift_the_boundary`, `test_key_is_deterministic_and_version_prefixed` |
| `MISSING` có NULL riêng, không thành `"None"`/`"00"` | `identity_components()` **từ chối** `MISSING` kèm `version_raw`; schema có CHECK cùng chiều | `test_missing_version_cannot_carry_raw_value_in_sql`, `test_null_version_is_not_the_string_none_nor_zero_zero` |
| `notifyVersion` 00 và 01 = hai source records, nối bằng `notice_group` | `notice_group_key(family, natural_key)` không chứa version; `notice_groups` + `notice_group_members` | `test_notify_version_00_and_01_are_two_records_one_notice_group` (DEDUP-16) |
| Áp dụng cho family khác **sau khi xác minh natural key trong adapter gốc** | Đã xác minh bằng cách đọc adapter, không đoán — bảng 6 family ở DATA_CONTRACT §1.2 | `egp_home.py:224` khai `id_fields: tuple[str,str]  # (no_field, version_field)` |

**Phát hiện blocking cho G1** (không có trong spec, tìm ra khi đối chiếu adapter):

```
egp_home.py:158  version = _first(doc.get("notifyVersion")) or "00"
egp_home.py:341  version = _first(doc.get(spec.id_fields[1])) or "00"
```

Dòng 341 là **đường dùng chung cho cả sáu family**. Adapter cũ chữa version thiếu thành `"00"` *trước khi*
identity kịp thấy — đúng BUG-08 và đúng điều O-3 cấm. Port rule: adapter mới trả
`(version_state, version_raw)`, không trả chuỗi đã chữa.

### O-4 — Unresolved

Ba nhánh, ba đường xử lý, **không nhánh nào tạo bản ghi giả** (DATA_CONTRACT §2):

| Tình huống | RECORD object | Observation | Ghi ở |
|---|---|---|---|
| Trích được, đủ identity | có | có `SR:<identity_key>` | `observations` + `source_records` |
| Trích được, thiếu identity | có | có `UNRESOLVED:<sha>` | `observations` + `unresolved_records` |
| Không parse được | **không** | **không** | `quarantined_responses` (mức page) |

`CHECK (response_object_sha <> record_object_sha)` chặn việc dùng hash trang thay hash hồ sơ.
Tests: `test_unparseable_response_produces_no_record_and_no_observation`,
`test_response_hash_can_never_stand_in_for_a_record_hash`,
`test_record_without_identity_is_unresolved_not_dropped`.

### O-5 — Deep capture

`deep_capture_history` (bất biến, INSERT-only, `UNIQUE(deep_capture_id, capture_seq)`) tách khỏi
`deep_captures` (latest + `latest_capture_seq` + `completeness` + `stale` + `refresh_due_at_utc`).
Refresh **không UPDATE xoá lịch sử**; `deep_capture_latest` là view JOIN con trỏ xuống history.
Tests: `test_deep_capture_refresh_appends_and_keeps_history`,
`test_latest_pointer_must_reference_an_existing_capture`, `test_stale_requires_a_reason`.

---

## 2. DDL thay đổi và lý do

`docs/ddl/g0_b_proposed.sql` — 39 bảng / 4 view / 1 trigger. Mỗi nhãn `[G0B-*]` tương ứng một finding.

| Nhãn | Thay đổi | Lý do |
|---|---|---|
| `[G0B-C2]` | CHECK trên `coverage_intervals.status`, `fetch_attempts.outcome`, `observations.status`, `raw_revisions.reason`, `deep_captures.lane_status` | §3.6 liệt kê từ vựng nhưng §3.10 không ràng buộc → BUG-03 tái phát được. Giờ DB từ chối `COMPLETED` trong `interval_status` |
| `[G0B-SHA]` | **Bỏ `object_kind` khỏi `raw_objects`**, thêm view `raw_object_roles` | Xung đột thật giữa `sha256 PRIMARY KEY` và hai vai trò (§3 dưới) |
| `[G0B-C5]` | View `orphan_object_rows`; **không** lưu trạng thái mồ côi | Hai loại mồ côi không đối xứng: row mồ côi SQL thấy được, file mồ côi thì không |
| `[G0B-C6]` | Bảng `filter_boundary_rules` (rule_key, rule_version, boundary_kind, precision, source_timezone, offset_minutes, conversion_params, canary_verified) + `coverage_proofs.boundary_rule_id` | §4.2 đòi khai **công thức quy đổi**; kind không thôi không tái lập được |
| — | Trigger `trg_proof_boundary_canary` | **SQLite cấm subquery trong CHECK** (đo được). Trigger vẫn DB-enforced mà không nhân bản `canary_verified` sang bảng proof |
| `[G0B-O3]` | `identity_key` dạng `id1:<64 hex>` + CHECK GLOB | Không còn chuỗi nối |
| `[G0B-C7]` | `CHECK ((MISSING ∧ version_raw IS NULL) ∨ (KNOWN/UNPARSABLE ∧ version_raw IS NOT NULL))` | NULL có đúng một cách render |
| `[G0B-C9]` | `current_content_hash NOT NULL` | Không tồn tại record có version mà chưa có hash |
| `[G0B-C10]` | `coverage_proofs` khai báo **trước** `coverage_intervals` | §4.1 mục 8 bắt ghi proof trước trong cùng transaction; thứ tự cũ ngược, dễ viết sai |
| `[G0B-O4]` | `quarantined_responses`, `unresolved_records` | Hai mức quarantine §5.5 |
| `[G0B-O5]` | `deep_captures` + `deep_capture_history` + view `deep_capture_latest` | B6/O-5 |
| `[G0B-U2]` | `money_amounts`, `goods_rows`, `contractor_memberships` với `presence_state` | UNKNOWN ≠ 0; **không `DEFAULT 0`** ở mọi cột số |
| `[G0B-U3]` | `page_receipts` (CHECK đơn điệu receipt), `watermarks` (FK tới interval COMPLETE), `reconcile_gaps` (không có đường DELETE record), `unresolved_records` | Bảng bắt buộc nhưng chưa từng có cột |
| `[G0B-B7]` | `source_access_events` với `reason_kind` đóng + `probe_uri CHECK (... NOT GLOB '*[*?]*')` | §6 bắt scrub; code cũ nhét token vào `reason`. Lý do vẫn ghi được mà không mở chỗ cho URL mang query |
| — | `runs.status`, `source_registry.access_state`, `canonical_entities.resolution_state`, `relation_edges.confidence`, `entity_source_links.relation/method` đều bị CHECK | §2.3, §3.8, §6 |
| — | `relation_edges` thêm FK thật tới `canonical_entities` + `CHECK (from_entity <> to_entity)` | §3.10 để `from_entity/to_entity` tự do → edge treo |

### Hai lỗi chính tôi mắc và bị công cụ của tôi bắt lại

Ghi lại vì chúng là bài học, không phải để thanh minh:

1. **Off-by-one làm mọi INSERT fail.** DDL ghi `length(identity_key) = 67`, encoder sinh 68 (`id1:` + 64 hex).
   Không đọc ra được; `test_python_generated_key_satisfies_the_database_check` mới là thứ bắt được.
2. **View `orphan_object_files` của tôi trả row mồ côi, không phải file mồ côi.** Tên sai sẽ dẫn tới
   "đã quét GC xong" trong khi chưa quét gì. Đã đổi tên thành `orphan_object_rows` và ghi rõ giới hạn
   của SQL ở DATA_CONTRACT §3.3. Test đầu tiên của tôi đã chỉ ra điều này.

---

## 3. Xung đột raw SHA vs hai vai trò RESPONSE/RECORD (owner yêu cầu kiểm tra)

### Xung đột là có thật, không phải hộp tưởng tượng

§3.10 đặt đồng thời `sha256 TEXT PRIMARY KEY` và `object_kind CHECK (IN ('RESPONSE','RECORD'))`.
Cùng payload đảm hai vai trò thì row thứ hai fail UNIQUE.

Không phải tình huống hiếm: **§13.1 của chính spec ghi `obs.raw = doc`** (`egp_home.py:205,402`) —
code cũ lưu nguyên trang làm raw của từng record. Trang một record ⇒ bytes của "response" và "record"
là một.

Chứng minh bằng chạy, không bằng lập luận — `test_reference_ddl_rejects_the_second_role` dựng DB từ
§3.10 **nguyên bản** và nhận `sqlite3.IntegrityError: UNIQUE constraint failed`.

### Cách giải quyết và vì sao không chọn cách khác

Vai trò là thuộc tính **của cạnh**, không của object. `raw_objects` bỏ `object_kind`; view
`raw_object_roles` UNION 18 cạnh để suy lại.

| Phương án bị loại | Vì sao |
|---|---|
| PK composite `(sha256, object_kind)` | cùng bytes → hai file trên đĩa, phá dedup, phình kho, trái §5.1 |
| Hash có domain-separation (`"RECORD\0"+bytes`) | `sha256` không còn là "hash của object SAU redaction" như §3.10/§5.1 định nghĩa |
| Lưu `object_kind` của vai trò đầu tiên | trạng thái dư, nói dối khi vai trò hai xuất hiện — trái tinh thần C-5 |

**Đây là sửa đổi một điều khoản đã đóng băng của §3.10** → cần owner phê chuẩn tường minh (§7).

---

## 4. Test thực sự đã chạy

`tests/contract/test_g0_b_invariants.py` — **58 passed, 0.27 s**, SQLite `:memory:` + `PRAGMA foreign_keys=ON`.
Không test nào assert "DDL thiếu cột"; mỗi test assert một hành vi, và phần lớn assert bằng cách
**thử ghi cái sai và đòi DB từ chối**.

### Ánh xạ 7 yêu cầu test của owner

| Yêu cầu | Test | Kết quả |
|---|---|---|
| Không collision identity với NULL, Unicode, delimiter | `test_null_version_is_not_the_string_none_nor_zero_zero`, `test_delimiter_in_source_value_cannot_shift_the_boundary`, `test_delimiter_in_family_and_state_cannot_collide_either`, `test_unicode_nfc_and_nfd_forms_are_one_identity`, `test_notice_group_key_is_immune_to_delimiter_shift`, `test_missing_rejects_a_raw_value_instead_of_silently_ignoring_it` | PASS |
| Không merge sai hai bản công bố | `test_notify_version_00_and_01_are_two_records_one_notice_group` | PASS — 2 record, 1 group, `COUNT(DISTINCT identity_key)=2` |
| Không tạo observation cho response không parse được | `test_unparseable_response_produces_no_record_and_no_observation`, `test_response_hash_can_never_stand_in_for_a_record_hash` | PASS — 0 observation, 0 record, 1 quarantine page |
| Page 10 record, chỉ 1 đổi → chỉ record đó có raw revision mới | `test_page_of_ten_records_only_one_changed` | PASS — 9 record giữ nguyên hash & `content_version=1`; record #10 có revision 2 |
| Deep capture refresh không mất lịch sử | `test_deep_capture_refresh_appends_and_keeps_history`, `test_latest_pointer_must_reference_an_existing_capture` | PASS — history 2 row sau refresh; latest trỏ seq 2 |
| Coverage resolver union đúng, không COMPLETE giả | `test_union_merges_touching_and_overlapping`, `test_subtract_produces_exact_gaps`, `test_three_hours_of_a_day_is_not_a_complete_day`, `test_hourly_split_covering_the_whole_day_is_complete`, `test_newer_partial_does_not_mask_verified_complete`, `test_legacy_complete_without_proof_is_never_skipped`, `test_leap_day_window_unions_correctly`, `test_historical_access_unsupported_is_its_own_status` | PASS |
| Transaction, FK, CHECK, UNIQUE hoạt động trên SQLite thật | `test_transaction_rolls_back_wholly_on_midway_failure`, `test_interval_before_proof_is_refused_by_fk`, `test_complete_interval_requires_a_proof`, `test_completed_never_enters_interval_status`, `test_run_status_and_interval_status_are_different_vocabularies`, `test_attempt_outcome_vocabulary_is_enforced`, `test_unique_constraint_blocks_duplicate_observation`, `test_observation_key_must_be_typed`, `test_blocked_reason_is_recordable_without_becoming_a_token_leak` | PASS |

### Test thêm ngoài yêu cầu

`test_content_unchanged_but_raw_changed_is_revision_only` (DEDUP-09 qua đường ingest thật),
`test_abab_keeps_three_content_versions` (§13.3 quyết định 2),
`test_raw_only_revision_may_not_point_at_a_content_version`,
`test_recovered_retry_is_allowed_and_counted` (COV-16), `test_retry_keeps_both_attempts` (B3),
`test_unknown_amount_is_null_not_zero` / `test_quantity_unknown_is_not_zero` /
`test_money_columns_are_separate_concepts` (U-2),
`test_receipt_steps_are_monotonic` (§2.3), `test_row_orphan_is_derivable_and_file_orphan_is_not_a_sql_question` (C-5),
`test_g0b_role_view_reports_both_roles_for_shared_bytes` (SHA), `test_ok_attempt_without_body_is_refused`,
`test_proof_without_canary_rule_is_refused_by_trigger`, `test_proof_must_match_its_rule_adapter`,
`test_complete_proof_cannot_claim_unrecovered_requests`, `test_revised_after_proof_requires_reconcile`,
`test_empty_interval_is_rejected_as_evidence`, `test_naive_datetime_cannot_pass_as_utc`,
`test_content_hash_invariant_for_trusted_record` (C-9), `test_interval_before_proof_is_refused_by_fk` (C-10).

### Test đã làm thay đổi code — không chỉ xanh để trang trí

Bốn test fail trước và buộc sửa thật:

| Test | Lỗi nó bắt | Sửa |
|---|---|---|
| `test_python_generated_key_satisfies_the_database_check` | DDL nói 67, encoder ra 68 | CHECK `length = 68` |
| `test_g0b_role_view_reports_both_roles_for_shared_bytes` | view `raw_object_roles` select cột `sha256` từ `observations` — cột không tồn tại | view trỏ đúng tên cột từng bảng nguồn, alias về `sha256` |
| `test_row_orphan_is_derivable_and_file_orphan_is_not_a_sql_question` | view tên `orphan_object_files` nhưng trả row mồ côi | đổi tên `orphan_object_rows` + ghi rõ giới hạn SQL |
| `test_content_unchanged_but_raw_changed_is_revision_only` | helper tự tính `normalized` bỏ qua giá trị caller → content hash sai | `add_record` nhận `normalized` tường minh |

Và hai lỗi **phương pháp** tôi phải sửa ở checker:

1. 25 check gốc mã hoá **vị trí** spec đặt cột, không phải **hợp đồng** phải thỏa → khi DDL G0-B chuyển
   nghĩa vụ sang chỗ khác (boundary ra bảng rule, orphan sang view, unresolved sang bảng quarantine),
   checker báo đó là **regression**. Đã viết lại placement-agnostic. Đây là lỗi nghiêm trọng hơn các lỗi
   trên vì nó đánh dấu đúng vào thứ tiến bộ.
2. `B4.2` quét từ `notifyVersion` trên **toàn văn bản**, nên một comment mô tả cũng bị tính là lộ tên
   trường. Sửa thành so trên **tên cột**.

### Trạng thái các finding

| Finding | Trước | Sau | Đóng bằng |
|---|---|---|---|
| C-2 từ vựng không CHECK | FAIL | **CLOSED** | CHECK + `test_completed_never_enters_interval_status` |
| C-5 mồ côi | GAP | **CLOSED** | view `orphan_object_rows` + ghi rõ file mồ côi thuộc filesystem scan |
| C-6 boundary rule | GAP | **CLOSED** | `filter_boundary_rules` + trigger canary |
| C-7 NULL encoding | GAP | **CLOSED** | CHECK 2 chiều + encoder từ chối |
| C-8 delimiter | GAP | **CLOSED** | canonical digest length-tagged |
| C-9 content hash | GAP | **CLOSED** | `current_content_hash NOT NULL` |
| C-10 thứ tự proof | GAP | **CLOSED** | proof khai báo trước + FK test |
| U-1 resolver shape | thiếu | **CLOSED** | `CoverageResolution` + 11 test coverage |
| U-2 money | thiếu | **CLOSED** | `money_amounts` + presence_state, không DEFAULT 0 |
| U-3 4 bảng | thiếu | **CLOSED** | `page_receipts`, `watermarks`, `reconcile_gaps`, `unresolved_records` |
| SHA role | chưa xét | **CLOSED** (chờ phê chuẩn) | bỏ `object_kind`, view role |
| B7.2 lý do BLOCKED | GAP | **CLOSED** | `source_access_events` reason_kind đóng |
| KEY.1 notifyVersion 2 lần | FAIL | **CLOSED** | O-3 phương án A |
| UNRES.1 | FAIL | **CLOSED** | O-4 ba nhánh |
| B6.1 deep capture | GAP | **CLOSED** | O-5 hai bảng |

---

## 5. Phạm vi tôi đã vượt — khai báo tường minh

Đề bài nói "chưa viết code sản phẩm" ở G0-B, nhưng cũng đòi "test bất biến thực tế" và "resolver tính
đúng union". Không thể có cả hai nếu không có code để gọi. Tôi viết **hai primitive hợp đồng**, không
phải engine:

| File | Là gì | Không là gì |
|---|---|---|
| `src/santhau_studio/domain/identity.py` | canonical serialization + notice group + 3 hàm hash | không có I/O, không có DB, không phải ingest path |
| `src/santhau_studio/domain/coverage.py` | interval algebra + resolver thuần theo §4.3/§4.5 | không đọc `coverage_intervals`, không ghi gì cả |

`tests/contract/helpers.py` chứa `ingest_record()` — bản chép trung thực bảng quyết định §3.5, **chỉ để
test**. Nó không phải đường ingest sản phẩm; G1 thay bằng writer thật và phải chạy lại đúng 58 test này.

Nếu bạn muốn hai module đó nằm ngoài `src/` cho tới G1, nói một câu là tôi dời sang `contracts/`.

---

## 6. Còn mở

### Cần owner

| ID | Mở | Chặn gì |
|---|---|---|
| **D-0** | **Phê chuẩn bỏ `object_kind` khỏi `raw_objects`** — sửa một điều khoản đã đóng băng của §3.10 | freeze DDL |
| D-1 | `identity_key` là digest ⇒ **không đọc ngược được** natural key khi điều tra sự cố. Chọn: digest thuần, hay digest + cột `identity_components` (JSON) để debug | freeze DDL (thêm cột sau = migration) |
| D-2 | `relation_edges.confidence` ba mức nhưng chưa có ngưỡng để `candidate_match` tự lên `LINKED` | UI-06/§3.8, không chặn port |
| D-3 | Cột của `documents`, `lots`, `goods_rows`, `contractor_memberships` do tôi thiết kế; §3.10 chỉ liệt kê tên | freeze DDL cho các bảng đó |
| D-4 | Natural key của detail/HSMT/Reoffer (`inputResultId`, `notifyId+document_kind+processApply`) chưa đối chiếu được — các script đó đang QUARANTINE | G1 |
| **S-1/S-2/S-3** | A1: hạ tunnel + rotate token + ký quyết định GitHub | A1 (không chặn G1) |
| **O-8** | A8: VM Windows sạch | A8 (không chặn G1) |
| **O-6** | Legal: LGPL vs commercial Qt; Qt per-module license files không có trong wheel | G4 |

### Chưa verify (không tô xanh)

- **`ingest_record()` chưa được kiểm chứng chéo** bởi writer thật — nó là transcription của §3.5, và
  chính vì nó do tôi viết nên "test xanh" chưa đồng nghĩa "engine sẽ xanh". G1 phải chạy lại 58 test
  này trên writer thật.
- **Chưa có live canary nào.** Toàn bộ chạy offline trên dữ liệu tự sinh. `filter_boundary_rules`
  hiện mang giá trị **giả định** (`CLOSED_LOCAL`, `Asia/Ho_Chi_Minh`, offset 420) — chưa được nguồn
  chứng minh. §13.2 vẫn để ngỏ mục này.
- **`conversion_params` chưa được test round-trip** — schema giữ được nó, nhưng chưa có test chứng
  minh một adapter áp dụng params rồi sinh đúng tham số nguồn. Thuộc COV-17 ở G1.
- **Hiệu năng §7.4 (1M records) chưa đo.** 51 FK + 198 CHECK + 4 view có thể có giá; chưa biết bao nhiêu.
- **`orphan_object_rows` chưa đo ở scale.** View UNION 18 cạnh trên 1M raw object có thể chậm; cần đo
  trước khi để UI-09 gọi đồng bộ.

---

## 7. Xác nhận G1

### READY — với 4 điều kiện

| # | Điều kiện | Loại |
|---|---|---|
| 1 | Owner phê chuẩn **D-0** (bỏ `object_kind`) và **D-1** (digest có/không kèm components) | Chặn **freeze DDL**, không chặn bắt đầu port |
| 2 | Port rule: adapter **không** được `or "00"` (`egp_home.py:158,341`) — phải có regression test fail trên code cũ | Chặn G1 correctness |
| 3 | G1 writer thật phải chạy xanh **58 contract test** này, không viết lại test | Chặn G1 exit |
| 4 | B5 boundary: chưa có canary. `filter_boundary_rules` hiện là giá trị giả định → **không được cấp `COMPLETE`** cho tới khi canary chứng minh (`canary_verified=1` đã là điều kiện trigger) | Chặn claim coverage |

### Không còn gì chặn G1 về mặt contract

- 7 hợp đồng B1–B7: **đóng cả 7** về schema.
- 10 finding C/U: **đóng 10**.
- Contract check: **39 PASS / 0 GAP / 0 FAIL** trên DDL đề xuất.
- DDL thực thi được trên SQLite thật, có FK/CHECK/trigger/unique đang hoạt động — không phải DDL trên giấy.
- Ba bất biến nguy hiểm nhất đã có hàng rào ở DB, không chỉ ở code: `COMPLETED` vào `interval_status`,
  `UNKNOWN` thành `0`, và `response_object_sha` thế cho `record_object_sha`.

### NO-GO cho việc gì

- **NO-GO cho freeze DDL** tới khi owner trả lời D-0/D-1/D-3.
- **NO-GO cho live canary / claim coverage** — boundary rule chưa được nguồn chứng minh.
- A1 vẫn **BLOCKED**, A8 **VM sạch NOT_TESTED**.

---

## 8. Cách tái lập

```powershell
cd D:\Harvest\santhau-data-studio
uv sync --all-groups

# 1. DDL thực thi được không?
python -c "import sqlite3,pathlib;sqlite3.connect(':memory:').executescript(pathlib.Path('docs/ddl/g0_b_proposed.sql').read_text(encoding='utf-8'))"

# 2. Contract check — baseline vs proposal
python tools/ddl_contract_check.py --sql docs/ddl/v1_3_reference.sql
python tools/ddl_contract_check.py --sql docs/ddl/g0_b_proposed.sql

# 3. Test bất biến trên SQLite thật
python -m pytest tests/contract -q      # 58 passed
python -m pytest                        # 122 passed
python -m ruff check src tools tests    # All checks passed
```

## 9. File thay đổi / mới

```
MỚI   src/santhau_studio/domain/identity.py            canonical serialization (O-3/C-7/C-8)
MỚI   src/santhau_studio/domain/coverage.py            resolver + interval algebra (U-1)
MỚI   docs/ddl/g0_b_proposed.sql                       DDL nghiệm thu, 39 bảng
MỚI   docs/DATA_CONTRACT.md                            hợp đồng dữ liệu, natural key 6 family
MỚI   tests/contract/helpers.py                        scaffolding ghi row hợp lệ (§3.5 transcription)
MỚI   tests/contract/test_g0_b_invariants.py           58 contract test
SỬA   tools/ddl_contract_check.py                      closure checks + placement-agnostic rewrite
SỬA   pyproject.toml                                   + tzdata==2026.6 (win32), pythonpath tests/contract
SỬA   uv.lock                                          15 packages
```

**DỪNG SAU G0-B.** Không tự chuyển G1. Không commit — file vẫn staged, chờ bạn duyệt.

> **Đợt 2 đã đóng 5 sửa bắt buộc + D-1/D-2/D-3.** Xem `G0-B-FINAL-FIXES.md` trong cùng thư mục:
> `ddl_contract_check` G0-B final đạt **43 PASS / 0 GAP / 0 FAIL** (baseline §3.10: 12/8/23),
> **135 tests** xanh trong đó 71 là contract test, và danh sách 10 điểm **chưa kiểm chứng** (V-1…V-10).
