# G0-B-READINESS — xác minh B1–B7 và DDL §3.10

**Không đề xuất lại từ số không.** B1–B7 và DDL §3.10 là quyết định đã chốt trong v1.3. Phần dưới đây
kiểm tra xem DDL có *thực sự biểu đạt* các quyết định đó không, và chỉ ra chỗ **hai điều khoản đã chốt
không cùng lúc khả thi**.

Máy kiểm: `tools/ddl_contract_check.py` trên `docs/ddl/v1_3_reference.sql` (trích nguyên văn §3.10,
15 `CREATE TABLE`). Test khoá lại: `tests/unit/test_ddl_contract.py` (25 test).

```
tally: {'PASS': 12, 'GAP': 8, 'FAIL': 5}
```

## 1. Bảy hợp đồng: trạng thái

| # | Hợp đồng | DDL biểu đạt chưa | Ghi chú |
|---|---|---|---|
| B1 | Hai định danh raw | **ĐẠT** | `observations` có cả `response_object_sha` + `record_object_sha` (2 cột rời), `record_locator NOT NULL`, `raw_objects.object_kind` có CHECK `RESPONSE/RECORD`. **Nhưng** xem C-4 (UNRESOLVED) |
| B2 | Transaction vs fault test | **ĐẠT một phần** | FK khai báo thật (`REFERENCES raw_objects`) nên transaction §5.2 có nghĩa. **Thiếu** chỗ ghi loại mồ côi → C-5 |
| B3 | Retry đã phục hồi | **ĐẠT** | `attempt_error_count` (vận hành) và `unrecovered_request_count` (chặn) là 2 cột rời; `fetch_attempts UNIQUE(request_id, attempt_no)` giữ đủ lịch sử attempt |
| B4 | notifyVersion ≠ content_version | **ĐẠT về tên** | Bộ đếm của ta là `content_version_seq`; `notifyVersion` chỉ còn là `notice_group_members.publication_label`. **Nhưng** xem C-3 (mã hoá trùng) |
| B5 | Hợp đồng biên filter | **ĐẠT một phần** | `filter_boundary_kind` + `filter_boundary_verified_at_utc` nằm trong proof; `granularity` có ở cả interval lẫn proof. **Thiếu** quy tắc quy đổi → C-6 |
| B6 | Refresh deep capture | **CHƯA ĐẠT** | Không có bảng/cột nào cho `captured_at` / `refresh_due_at` → C-1 |
| B7 | Token qua header | **ĐẠT phần schema** | Không cột nào lưu token/cookie/authorization. Phần "nguồn có nhận header không" là canary, không phải schema → để G1 |

## 2. Bảng mâu thuẫn và lỗ hổng

Cột đúng yêu cầu: `spec_clause / conflict / evidence / proposed_resolution / impact`.

### C-1 — B6 không có chỗ trong schema

| | |
|---|---|
| spec_clause | §9 B6 + §10.4: "`FULL_RESPONSE` chỉ đúng tại thời điểm capture; invalidate khi `decisionVersion`/`resultVersion`/`notifyVersion` đổi hoặc quá `refresh_after_days`"; "`done_ids` trả về kèm `captured_at` + `refresh_due_at`" |
| conflict | §3.10 DDL không định nghĩa bảng nào cho trạng thái deep-capture. "Bảng bắt buộc còn lại" chỉ **liệt kê tên** (`documents`, `lots`, `goods_rows`…) mà không có cột |
| evidence | `ddl-contract-check.txt` → `B6.1 GAP`; grep: `refresh_due_at` và `captured_at_utc` **không xuất hiện** trong DDL. Test `test_deep_capture_refresh_has_no_schema_surface_yet` |
| proposed_resolution | Thêm bảng `deep_captures` (hoặc `capture_states`): `id`, `source_record_id FK`, `lane`, `endpoint_kind`, `captured_at_utc`, `response_object_sha FK raw_objects`, `full_response_verified BOOL`, `refresh_due_at_utc`, `refresh_reason`, `decision_version_raw`, `result_version_raw`, `notify_version_raw`, `captured_by_run_id`, `stale BOOL NOT NULL DEFAULT 0`, `UNIQUE(source_record_id, lane, endpoint_kind)`. `done_ids` = `lane='FULL' AND stale=0 AND refresh_due_at_utc > now` |
| impact | **CAO.** Nếu đóng băng DDL mà thiếu bảng này, FAULT-17 và §10.4 không có chỗ ghi. Sửa sau khi khách có kho = migration dữ liệu khách |

### C-2 — Từ vựng trạng thái không bị CHECK, dù §3.6 cấm chéo

| | |
|---|---|
| spec_clause | §3.6 liệt kê 5 bộ từ vựng theo tầng và "Không đường code nào ghi `COMPLETED` vào `interval_status` hay ngược lại (BUG-03)" |
| conflict | DDL chỉ có CHECK cho `source_records.version_state` và `raw_objects.object_kind`. Ba cột trạng thái khác để trống |
| evidence | `VOCAB.coverage_intervals FAIL`, `VOCAB.fetch_attempts FAIL`, `VOCAB.observations FAIL`, `VOCAB.source_records PASS`. Test `test_vocabulary_columns_lacking_check_constraints_are_the_known_gap` |
| proposed_resolution | Thêm CHECK đúng §3.6: `coverage_intervals.status IN ('COMPLETE','PARTIAL','FAILED','BLOCKED','INTERRUPTED')`; `fetch_attempts.outcome IN ('OK','HTTP_ERROR','TIMEOUT','BLOCKED','MALFORMED')`; `observations.status IN ('NEW','UPDATED','UPDATED_RAW','UNCHANGED','DUPLICATE_IN_RUN','UNRESOLVED')`. Thêm `runs.status` và `lane_status` khi viết migration cho 2 bảng còn lại |
| impact | **CAO và rẻ.** Đây chính xác là cơ chế BUG-03 tái phát. CHECK biến một bug từng tồn tại 1 revision thành `sqlite3.IntegrityError` ngay lần ghi đầu tiên. Nên đóng **trước** G0-B freeze |

### C-3 — `notifyVersion` bị mã hoá hai lần trong identity

| | |
|---|---|
| spec_clause | §3.7: natural key TBMT = `(notifyNo, notifyVersion)`. §3.4: `identity_key = family\|natural_key\|state\|version_raw` và `identity_key` nằm trong UNIQUE |
| conflict | `notifyVersion` đã nằm **trong** `natural_key`, rồi xuất hiện **lần nữa** qua `version_raw`. Hai chỗ phải luôn được suy ra giống nhau; spec không nêu quy tắc nào bảo đảm điều đó |
| evidence | `KEY.1 FAIL`; `source_records` có cả `natural_key`, `identity_key`, `version_raw`. Test `test_notify_version_double_encoding_is_recorded_as_conflict` |
| proposed_resolution | Chọn 1 trong 2, rồi viết test bất biến: **(A)** `natural_key` = `notifyNo` **đơn thuần**, mọi thứ version dồn hết vào `version_state`+`version_raw` (bỏ trùng, gọn nhất); hoặc **(B)** giữ `natural_key` như §3.7 và `identity_key` **không** lặp lại version. Khuyến nghị **(A)** vì nó khiến DEDUP-06 ("thiếu notifyVersion vs `"00"`") đúng một cách hiển nhiên: state khác nhau thì identity khác nhau, không phụ thuộc cách encode natural_key |
| impact | **RẤT CAO — đây là quyết định không đảo ngược được.** `identity_key` nằm trong UNIQUE của `source_records`. Chọn sai nghĩa với lịch sử hai lần công bố của cùng một thông báo → hoặc gộp nhầm, hoặc tách đôi record. Đổi sau khi khách có kho = migration dữ liệu khách |

### C-4 — Observation UNRESOLVED không biểu diễn được

| | |
|---|---|
| spec_clause | §3.10 `observation_key TEXT NOT NULL, -- 'SR:<id>' \| 'UNRESOLVED:<hash>'`; `record_object_sha TEXT NOT NULL REFERENCES raw_objects(sha256)` |
| conflict | Một member **không trích được hồ sơ** thì không tồn tại record object. Cột NOT NULL + FK bắt nó phải trỏ vào một raw object hợp lệ. Nếu trỏ vào response object thì **phạm §3.2** ("Hai định danh khác nhau, không được dùng lẫn") |
| evidence | `UNRES.1 FAIL`. Test `test_unresolved_observation_conflict_is_recorded` |
| proposed_resolution | Chọn 1: **(A)** cho phép `record_object_sha NULL` khi `observation_key LIKE 'UNRESOLVED:%'` (cần CHECK hoặc trigger, SQLite không có partial unique); **(B)** quy định "lưu fragment không parse được như một RECORD object" — giữ NOT NULL, nhưng phải định nghĩa canonical serialisation của fragment để hash ổn định. Khuyến nghị **(B)**: nó giữ nguyên tắc "mọi observation trỏ vào bytes đọc được" của §5.3 và không thêm nhánh NULL lan khắp query |
| impact | **CAO.** Liên quan trực tiếp FAULT-13 (quarantine mức page) và UI-09. Không chốt thì code đầu tiên gặp member hỏng sẽ ghi NULL và vỡ FK, hoặc trỏ sai loại object |

### C-5 — Hai loại mồ côi không có chỗ ghi

| | |
|---|---|
| spec_clause | §5.2 phân biệt "Object file mồ côi" (file có, row không → GC an toàn) và "Object row mồ côi" (row có, không tham chiếu → **giữ**, chỉ GC theo tuổi) |
| conflict | `raw_objects` có `integrity_status` (OK/CORRUPT/QUARANTINED) nhưng **không có cột nào** ghi "row này chưa được tham chiếu" hay tuổi để áp GC |
| evidence | `B2.2 GAP` |
| proposed_resolution | Thêm `last_referenced_at_utc TEXT` và để GC quyết định bằng `referenced-exists` query; hoặc thêm `orphan_kind TEXT CHECK (orphan_kind IN ('NONE','FILE_ONLY','ROW_ONLY'))` do bước recovery ghi. Khuyến nghị cột đầu — suy ra được, không phải khai, nên không thể tự mâu thuẫn |
| impact | **TRUNG BÌNH.** FAULT-16 cần kiểm tra được hai loại này khác nhau |

### C-6 — Quy tắc quy đổi biên không được lưu

| | |
|---|---|
| spec_clause | §4.2 bảng: mỗi adapter phải khai "Quy tắc quy đổi: công thức từ `[from,to)` UTC → tham số nguồn", và proof phải chứng minh |
| conflict | DDL lưu `filter_boundary_kind` và `granularity` nhưng **không lưu công thức quy đổi**. `source_query_fingerprint` và `adapter_sha` là hash của những thứ khác, không tái dựng được quy đổi |
| evidence | `B5.3 GAP`; `coverage_proofs` không có cột nào tên `boundary_rule`/`conversion`. Test `test_filter_boundary_conversion_rule_not_persisted` |
| proposed_resolution | Thêm `filter_boundary_rule TEXT NOT NULL` (ví dụ `to_utc - 1ms → closed_local_day(Asia/Ho_Chi_Minh)`), hoặc nhét vào `terminal_evidence_json` với key bắt buộc. Khuyến nghị cột riêng: COV-17 cần so khớp rule giữa hai proof, so trong JSON thì rẻ và dễ sai |
| impact | **CAO.** COV-17 phát biểu "proof ghi `filter_boundary_kind`" nhưng nếu chỉ ghi kind mà không ghi rule thì **không tái lập được** vì sao bản ghi sát biên bị mất |

### C-7 — Định dạng `identity_key` khi `version_raw` NULL

| | |
|---|---|
| spec_clause | §3.4 `identity_key` mã hóa **cả** `version_state` và `version_raw`, dạng `family\|natural_key\|state\|version_raw`; `version_raw TEXT NULL` |
| conflict | Với `version_state = MISSING`, `version_raw` là NULL — spec không định nghĩa chuỗi thay thế. `"…\|MISSING\|"` và `"…\|MISSING\|None"` là **hai identity_key khác nhau** cho cùng một hồ sơ |
| evidence | `IDK.1 GAP` |
| proposed_resolution | Chốt: khi `version_state ∈ {MISSING}` thì phần tử cuối là chuỗi rỗng; khi `UNPARSABLE` thì là `version_raw` đã escape. Viết test bất biến: hai đường code cùng sinh identity_key cho một payload → phải bằng nhau |
| impact | **RẤT CAO.** `identity_key` nằm trong UNIQUE → sai một ký tự là tách đôi source record, và sửa sau khi khách có kho là migration dữ liệu khách |

### C-8 — Ký tự phân cách `|` không có quy tắc escape

| | |
|---|---|
| spec_clause | §3.4 format `family\|natural_key\|state\|version_raw`; §3.7 natural key lấy từ dữ liệu nguồn |
| conflict | `natural_key` là **dữ liệu nguồn**. Nếu `notifyNo`/`planNo`/`contractCode` chứa `|`, hai bộ khoá khác nhau có thể sinh cùng `identity_key`. Spec không nêu escape, cũng không nêu độ dài tối đa |
| evidence | `IDK.2 GAP` |
| proposed_resolution | Escape `|` trong từng thành phần (ví dụ `\|` → `\u007c`, `\` → `\\`), hoặc thay bằng delimiter không xuất hiện trong dữ liệu + encode độ dài. Kèm test: payload chứa `A\|B` và `A`+`B` phải cho identity_key khác nhau |
| impact | **CAO.** Đây là lớp lỗi "collision im lặng": hai hồ sơ thật khác nhau gộp thành một, không exception, chỉ sai số liệu |

### C-9 — Nullability của `current_content_hash`

| | |
|---|---|
| spec_clause | §3.5: record mới có `seq=1 (NEW)` với `content_hash NOT NULL` |
| conflict | `source_records.current_content_hash` để NULL trong khi `source_record_versions.content_hash NOT NULL` và `current_content_version NOT NULL`. Không tồn tại trạng thái hợp lệ nào có version mà chưa có hash |
| evidence | `HASH.1 GAP` |
| proposed_resolution | Đổi `current_content_hash` thành NOT NULL, hoặc nêu tường hợp ngoại lệ (ví dụ record chỉ có raw revision, chưa normalize được) |
| impact | **THẤP–TRUNG BÌNH.** Để NULL thì mọi query phải xử lý nhánh không tồn tại |

### C-10 — Thứ tự proof/interval ngược với yêu cầu ghi

| | |
|---|---|
| spec_clause | §4.1 mục 8: "Proof ghi durable **cùng transaction** với interval, trước khi UI/scheduler có thể skip" |
| conflict | `coverage_intervals.proof_id REFERENCES coverage_proofs(id)` được khai báo **trước** `coverage_proofs`. SQLite cho phép forward reference nên không lỗi, nhưng migration phải insert **proof trước rồi interval** — thứ tự ngược với DDL, dễ viết sai |
| evidence | `ORD.1 GAP` (vị trí intervals < proofs trong file) |
| proposed_resolution | Sắp xếp lại thứ tự khai báo trong migration, và thêm test: ghi interval mà proof chưa tồn tại phải fail FK |
| impact | **THẤP.** Chỉ là rủi ro khi viết migration |

## 3. Ba hạng mục spec nói "coder viết" nhưng chưa đủ dữ kiện

| ID | Hạng mục | Thiếu gì |
|---|---|---|
| U-1 | `resolve_coverage(source_key, family, from_utc, to_utc)` (§4.5) | §9 G0-B exit ghi "resolver spec viết xong", nhưng §4.5 chỉ nêu **luật ưu tiên**, không định nghĩa **shape kết trả** (interval nào được coi là phủ kín `[from,to)` khi granularity khác nhau?). Cần chốt trước G1 |
| U-2 | Tiền / số lượng / thuế / tiền tệ / cấu hình lô (§0.2 mục 6) | §3.10 không có cột nào; "bảng bắt buộc còn lại" chỉ liệt kê tên. Đây là trường nghiệp vụ, không được suy ra sau |
| U-3 | `page_receipts`, `watermarks`, `reconcile_gaps`, `unresolved_records` (§3.10 cuối) | Không có cột. FAULT-03 và COV-10 cần `page_receipts` và `reconcile_gaps` có hình dạng xác định |

## 4. Khuyến nghị: đóng G0-B theo thứ tự nào

| Bước | Việc | Vì sao trước |
|---|---|---|
| 1 | C-2 (thêm CHECK từ vựng) | Rẻ nhất, chặn đúng bug đã xảy ra, không cần quyết định nghiệp vụ |
| 2 | C-3 + C-7 + C-8 (chốt `identity_key`) | Ba cái này **cùng một quyết định**: công thức identity_key. Không đảo ngược được sau khi khách có kho → phải xong trước freeze |
| 3 | C-4 (UNRESOLVED) | Ảnh hưởng B1 và FAULT-13; chỉ chọn được sau khi quyết định C-3 vì nó đụng cùng bảng |
| 4 | C-1 (bảng deep capture) + U-2, U-3 | B6 không đóng được nếu thiếu; U-2 là nghiệp vụ → cần Founder |
| 5 | C-5, C-6, C-9, C-10 | Sửa trong migration, không đổi semantics |
| 6 | U-1 (resolver shape) | Cần trước G1 port, không cần trước freeze |

## 5. Trạng thái G0-B

**Sẵn sàng có điều kiện.** B1, B3, B4, B7 đóng được về mặt schema ngay; B2 và B5 đóng được sau C-5/C-6;
**B6 chưa đóng được** (C-1). Năm mâu thuẫn C-2…C-8 cần Founder chốt, trong đó **C-3, C-7, C-8 là quyết
định không đảo ngược được** vì `identity_key` nằm trong UNIQUE.

Theo chỉ thị §3 của đề bài, tôi **dừng ở đây** và không viết migration production. Ba câu hỏi cần trả lời
để đi tiếp:

1. **C-3:** `natural_key` của TBMT là `notifyNo` đơn thuần (phương án A) hay `(notifyNo, notifyVersion)` (phương án B)?
2. **C-4:** member không parse được lưu thành RECORD object (phương án B) hay cho phép `record_object_sha NULL` (phương án A)?
3. **C-1:** chấp nhận thêm bảng `deep_captures` vào §3.10 như một phần của G0-B freeze?
