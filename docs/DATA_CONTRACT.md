# DATA CONTRACT — Săn Thầu Data Studio

**Spec nền:** Master Spec Consolidated v1.3. Tài liệu này **cụ thể hoá** các quyết định O-3/O-4/O-5
và những điểm §3.10 để ngỏ (C-1…C-10, U-1…U-3). Nó không thay thế spec; khi mâu thuẫn, spec thắng và
phải báo owner.

DDL nghiệm thu kèm theo: `docs/ddl/g0_b_proposed.sql` (39 bảng, 4 view, chạy được trên SQLite 3.53.1).
Machine check: `tools/ddl_contract_check.py --sql docs/ddl/g0_b_proposed.sql` → **39 PASS / 0 GAP / 0 FAIL**.

---

## 1. Identity (O-3)

### 1.1 Hai trục, không trộn

| Khái niệm | Bản chất | Where |
|---|---|---|
| `natural_key` | khoá nghiệp vụ **không kèm phiên bản công bố** | `source_records.natural_key` |
| `version_state` / `version_raw` | phiên bản công bố **của nguồn**, hoặc trạng thái thiếu của nó | `source_records.version_state`, `.version_raw` |
| `identity_key` | digest canonical của (family, natural_key, version_state, version_raw) | `source_records.identity_key` |
| `content_version` | bộ đếm **nội dung chuẩn hoá đổi lần thứ mấy** — của ta | `source_record_versions.content_version_seq` |
| `raw revision` | bộ đếm **trạng thái raw đã thấy** — của ta | `raw_revisions.revision_no` |
| `notice_group` | nhóm các bản công bố của cùng một thông báo | `notice_groups` + `notice_group_members` |

`notifyVersion` là **phiên bản công bố của nguồn**. Không phải semantic version, không phải
content version, và không được xuất hiện trong `natural_key` (đây là phương án A owner chốt).

### 1.2 Natural key đã xác minh từ adapter gốc

Không suy luận từ spec — đọc trực tiếp `harvester/adapters/egp_home.py` tại `aa65f0a`, trường
`id_fields: tuple[str, str]  # (no_field, version_field)`:

| Family | Dòng | `(no_field, version_field)` | `natural_key` mới (phương án A) |
|---|---|---|---|
| TBMT | 235 | `notifyNo`, `notifyVersion` | `notifyNo` |
| KQLCNT | 244 | `notifyNo`, `notifyVersion` | `notifyNo`, namespace KQLCNT |
| HỢP ĐỒNG | 265 | `contractCode`, `contractVersion` | `contractCode` |
| KHLCNT | 283 | `planNo`, `planVersion` | `planNo` |
| DỰ ÁN | 300 | `pno`, `pversion` | `pno` |
| YCBG | 315 | `notifyNo`, `notifyVersion` | `notifyNo`, namespace YCBG |

`namespace` ở đây là `family` trong `identity_key`, nên TBMT và YCBG cùng `notifyNo` vẫn là hai
record khác nhau (DEDUP-04).

### 1.3 Phát hiện blocking cho G1: adapter nguồn tự ép `or "00"`

```
egp_home.py:158  version = _first(doc.get("notifyVersion")) or "00"
egp_home.py:341  version = _first(doc.get(spec.id_fields[1])) or "00"
```

Dòng 341 nằm trên **đường dùng chung cho cả sáu family**, không riêng TBMT. Nghĩa là code cũ biến
mọi version thiếu thành `"00"` trước khi identity kịp thấy nó — đúng BUG-08, và là điều O-3 cấm.

**Quy tắc port bắt buộc:** adapter mới trả `(version_state, version_raw)`, không trả một chuỗi đã
chữa. `MISSING` là trạng thái, không phải giá trị. Regression test phải fail trên code cũ — đã có
`test_missing_version_cannot_carry_raw_value_in_sql` và `test_null_version_is_not_the_string_none_nor_zero_zero`.

### 1.4 Canonical serialization

`domain/identity.py`, `IDENTITY_SCHEMA_VERSION = 1`, output `"id1:" + sha256hex` (68 ký tự).

- Thành phần được **length-tag bằng JSON array**, không nối bằng delimiter → `("A|B","C")` ≠ `("A","B|C")`.
- Chuỗi được **NFC-normalise** → `é` U+00E9 và `e`+U+0301 là một identity (ngược lại sẽ tách đôi
  một thông báo thật).
- `None` vào JSON là `null`, phân biệt với `"null"`, `""`, và `"00"`.
- **Version prefix là bắt buộc về mặt vận hành**: `identity_key` nằm trong UNIQUE trên kho của khách.
  Đổi công thức = migration dữ liệu khách, nên lần đổi sau phải là `id2:` chứ không sửa `id1:`.

Từ chối thay vì chữa: `MISSING` kèm `version_raw` → `IdentityError`. `natural_key` rỗng → `IdentityError`
(payload không identity thì là UNRESOLVED, không phải một record).

---

## 2. Unresolved và quarantine (O-4)

Ba tình huống, ba đường xử lý — **không có đường nào tạo bản ghi giả**:

| Tình huống | RECORD object? | Observation? | Ghi ở đâu |
|---|---|---|---|
| Trích được hồ sơ, đủ identity | có | có, `observation_key='SR:<identity_key>'` | `observations` + `source_records` |
| Trích được hồ sơ, **thiếu identity** | có | có, `observation_key='UNRESOLVED:<sha>'`, `status='UNRESOLVED'` | `observations` + `unresolved_records` |
| **Không parse được** response/member | **không** | **không** | `quarantined_responses` (mức page/response) |

Hệ quả dây chuyền, đều có test:

- `record_object_sha` giữ **NOT NULL** và là chủ đích: mọi observation thật sự có record object.
- **Không còn** `CHECK (response_object_sha <> record_object_sha)`. Nó mâu thuẫn với D-0: trong store
  content-addressed thuần, nếu bytes của một trang thực sự bằng bytes của một hồ sơ (đúng hình dạng
  `obs.raw = doc` của code cũ) thì hai hash bằng nhau là **hợp lệ**, và CHECK đó từ chối một row đúng.
  Thay bằng `object_derivation` + trigger `trg_observation_derivation`: observation chỉ được ghi khi có
  chứng cứ `(response, record, locator, redaction_policy_version)`. Cơ chế này **mạnh hơn** CHECK cũ —
  nó chặn được cả cặp hash *khác nhau thật* nhưng chưa từng được trích từ nhau, thứ mà CHECK so-hash
  không hề thấy.
- `extract_by_locator()` trích thật (RFC 6901 pointer, hoặc `row:<n>` khi payload là mảng). Test so
  khớp digest kết quả trích với `record_object_sha` đã ghi: chứng minh dẫn xuất, không tuyên bố.
- `unresolved_records` không được cộng vào `records_count` (view `metric_counts` tách
  `unresolved_count` riêng).
- Quarantine hiển thị có cảnh báo, không âm thầm ẩn, không cộng vào kết quả đã xác minh (§5.5).
- Không thể cô lập record bên trong một member hỏng → member hỏng là **page-level**, đúng §5.5.

---

## 3. Raw object store và xung đột SHA (owner yêu cầu kiểm tra)

### 3.1 Xung đột là có thật

§3.10 đặt đồng thời:

```sql
sha256 TEXT PRIMARY KEY,
object_kind TEXT NOT NULL CHECK (object_kind IN ('RESPONSE','RECORD'))
```

Hai ràng buộc đó loại trừ nhau khi **cùng một payload đóng hai vai trò**. Trường hợp này không phải
hộp tưởng tượng: §13.1 ghi `obs.raw = doc` (`egp_home.py:205,402`) — code cũ lưu **nguyên trang** làm
raw của từng record. Trang một record thì bytes của "response" và của "record" là một. Row thứ hai
fail UNIQUE.

Test chứng minh: `test_reference_ddl_rejects_the_second_role` dựng DB từ §3.10 nguyên bản và thấy
`UNIQUE constraint failed`.

### 3.2 Cách G0-B giải quyết

**Vai trò là thuộc tính của cạnh, không phải của object.** `raw_objects` bỏ `object_kind`; store trở
thành content-addressed thuần, khớp đúng §5.1 ("hai source cùng nội dung dùng chung object, identity
vẫn riêng"). Vai trò suy ra qua view `raw_object_roles` (UNION trên 18 cạnh).

Vì sao không chọn phương án khác:

| Phương án | Vấn đề |
|---|---|
| PK composite `(sha256, object_kind)` | cùng bytes -> hai file trên đĩa, phá dedup, phình kho |
| Domain-separate hash (`"RECORD\0"+bytes`) | `sha256` không còn là "hash của object" như §3.10/§5.1 định nghĩa |
| Lưu `object_kind` của vai trò "đầu tiên" | trạng thái dư, nói dối khi vai trò thứ hai xuất hiện |

View không thể tự mâu thuẫn vì nó tính từ dữ liệu thật. Benchmark §5.1 và GC vẫn query được vai trò.

### 3.3 Hai loại mồ côi không đối xứng (§5.2)

| Loại | SQL thấy được? | View / công cụ | Chính sách |
|---|---|---|---|
| **ROW** mồ côi (row có, không cạnh nào trỏ) | có | view `orphan_object_rows` | **GIỮ**, chỉ GC theo tuổi, ghi log |
| **FILE** mồ côi (file có, không row) | **không** — SQL chỉ thấy row | bước quét filesystem §5.2 bước 5 | GC sau khi job verify xong |

Đặt tên view là `rows` chứ không phải `files` để không ai gọi nó rồi tưởng đã quét xong GC. Đây là chỗ
tôi sửa tên theo đúng bản chất sau khi test đầu tiên của chính tôi đánh lừa.

---

## 4. Coverage resolver (U-1)

### 4.1 Kiểu trả về

```python
CoverageResolution(
    source_key, family, requested: Interval,
    status: COMPLETE | PARTIAL | NOT_ACCESSED | NEEDS_RECONCILE | BLOCKED | UNSUPPORTED,
    proven:  tuple[Interval, ...]   # disjoint, sorted, maximal, ∩ requested
    gaps:    tuple[Interval, ...]   # requested − proven, disjoint
    legacy_unverified / stale / blocked: tuple[Interval, ...]
    granularity: DAY|HOUR|MINUTE|SECOND|MILLISECOND | None
    proof_ids: tuple[int, ...]
    reasons:   tuple[str, ...]
)
```

`can_skip` **chỉ** đúng khi `status == COMPLETE`. Consumer không tự suy lại coverage (§4.5).

### 4.2 Quy tắc union

- Interval half-open `[from, to)` UTC. **Kề nhau thì sát nhập**: `[a,b) ∪ [b,c) = [a,c)` — không sát
  nhập là nguồn của double-count.
- `proven` chỉ nhận row `COMPLETE` **có proof có `proof_version`**.
- `PARTIAL` mới hơn **không che** `COMPLETE` đã verify — ưu tiên theo trạng thái, không theo `MAX(id)`
  (COV-05, BUG-04).
- `COMPLETE` thiếu proof → vào `legacy_unverified`, **không** vào `proven`, không bao giờ skip (BUG-15).
- `granularity` chỉ báo `DAY` khi thực sự có một row DAY phủ toàn range. Một row DAY + chín row HOUR
  không được phép báo DAY.
- UNSUPPORTED ≠ 0 ≠ PARTIAL (COV-12).

### 4.3 Ngày VN

`covered_days` là **giá trị suy ra**. `vn_calendar_day(date)` sinh `[00:00, 24:00)` Asia/Ho_Chi_Minh
quy đổi UTC bằng datetime arithmetic thật, nên ngày nhuận và cuối tháng đúng theo construction
(COV-11). 3/24 giờ → `PARTIAL`, không phải `COMPLETE` (COV-13).

---

## 5. Hợp đồng biên filter (C-6)

`filter_boundary_rules` là bảng **có version**, vì quy đổi sai là mất bản ghi ở biên — lỗi im lặng
nguy hiểm nhất của coverage.

| Cột | Vì sao bắt buộc |
|---|---|
| `rule_key` + `rule_version` | proof phải trỏ vào một quy tắc cụ thể, không trỏ vào "hợp đồng hiện hành" |
| `boundary_kind` | `HALF_OPEN_UTC` / `CLOSED_LOCAL` / `CLOSED_UTC` / `OPEN_UTC` / `UNKNOWN` |
| `precision` | nguồn chỉ lọc tới ngày thì không được báo granularity giây |
| `source_timezone` + `offset_minutes` | chứng minh không phụ thuộc múi giờ máy chủ chạy crawler |
| `conversion_params` (JSON) | **công thức** quy đổi `[from,to)` UTC → tham số nguồn, tái lập được |
| `canary_verified` | kind mà chưa canary thì chỉ là lời tuyên bố |

`coverage_proofs.boundary_rule_id` NOT NULL FK. Trigger `trg_proof_boundary_canary` chặn proof khi rule
chưa canary, và chặn proof có `adapter_sha` khác rule nó viện dẫn.

Vì sao trigger chứ không CHECK: **SQLite cấm subquery trong CHECK** (đo được: `subqueries prohibited in
CHECK constraints`). Trigger vẫn là DB-enforced mà không phải nhân bản `canary_verified` sang bảng proof —
thứ sẽ tự mâu thuẫn với bảng rule.

---

## 6. Deep capture (O-5)

Hai bảng, một con trỏ:

| Bảng | Vai trò | Ghi |
|---|---|---|
| `deep_capture_history` | **bất biến**, mỗi lần capture một row `(deep_capture_id, capture_seq)` | INSERT only |
| `deep_captures` | latest state + `latest_capture_seq` + `completeness` + `stale` + `refresh_due_at_utc` | UPDATE con trỏ |

`FULL_RESPONSE` chỉ đúng **tại thời điểm capture** — không phải quyền skip vĩnh viễn (§10.4).
`stale_reason ∈ {SOURCE_VERSION_CHANGED, REFRESH_INTERVAL_ELAPSED, RECONCILE_MISMATCH, QUARANTINED_MEMBER}`,
và CHECK buộc `stale=1` phải có lý do.

`deep_capture_latest` là view JOIN con trỏ xuống history. Nếu refresh ghi đè history, view trả rỗng —
test `test_latest_pointer_must_reference_an_existing_capture` khoá hành vi đó.

---

## 7. Tiền, số lượng, thuế, đơn vị, lô (U-2)

§0.2 mục 5–6: UNKNOWN ≠ 0, và giá gói / giá trúng / đơn giá / số lượng / thuế / tiền tệ / lô là
**trường khác nhau**.

`money_amounts` một row cho mỗi (owner, role, currency, tax_basis):

```
presence_state ∈ {KNOWN, MISSING, UNPARSABLE}
value          NULL khi MISSING/UNPARSABLE   (CHECK)
currency       ∈ {KNOWN, MISSING, UNPARSABLE} — tách khỏi presence_state của value
currency_code  3 ký tự, bắt buộc khi KNOWN
tax_basis      ∈ {INCLUDING_TAX, EXCLUDING_TAX, UNKNOWN, NOT_APPLICABLE}
scale_exp      giữ 1.000.000.000 vs 1e9
provenance_json endpoint + locator + adapter_sha
```

**Không có `DEFAULT 0` ở bất kỳ cột số nào.** Muốn nói "bằng 0" phải assert `KNOWN` + value 0 + có
currency. Muốn nói "chưa biết" thì `MISSING` + NULL. Đại số hoá "chưa biết" thành 0 bị chặn ở schema,
không phải ở lời khuyên.

Cùng khuôn mẫu cho `goods_rows.quantity_presence` và `contractor_memberships.party_tax_id_presence`.
Nhiều nhà thầu / nhiều giá / multi-currency / thiếu tax basis → mỗi thứ một row, **không tự chọn phần
tử đầu**.

### 7.1 Deep-lane price roles (parser v4, migration 007)

Ngoài 7 role gốc (`PACKAGE_VALUE`, `BID_PRICE`, `WIN_PRICE`, `UNIT_PRICE`,
`CONTRACT_VALUE`, `BUDGET`, `ADJUSTMENT`), parser v4 ghi thêm:

| Role | Owner | Nguồn |
|---|---|---|
| `REOFFER_PRICE` / `REOFFER_PRICE_FINAL` | `OFFER` (contractor membership) | `contractorsResult[].reofferPrice` / `reofferPriceFinal` |
| `BID_PRICE` / `BID_FINAL_PRICE` | `PACKAGE` | `bidoListContractorReofferPassedDTOList[].bidPrice` / `bidFinalPrice` (tổng gói, lặp lại giống nhau trên mọi dòng lot) |
| `PACKAGE_PRICE` | `PACKAGE` | `bidDetail.bidPrice` + `bidPriceUnit` / `bidPriceDetail[].moneyType` |
| `PACKAGE_PRICE_INIT` / `PACKAGE_PRICE_STEP` | `PACKAGE` | top-level `priceInit` / `priceStep` |
| `LOT_PRICE` / `LOT_ESTIMATE_PRICE` / `LOT_PLAN_PRICE` | `LOT` | dòng lot HSMT: `lotPrice` / `lotEstimatePrice` / `pricePlan` |
| `UNIT_PRICE` | `GOODS_ROW` | `pricePlan` trên dòng hàng (hàng thuốc form 1960) |

`currency_code` chỉ lấy từ nguồn tự khai (`bidPriceUnit`, `bidPriceDetail[].moneyType`).
Giá lot/hàng không có currency riêng trong payload HSMT: chúng được ghi `VND` vì
`bidPrice` (khai `VND` trên 1.679/1.679 capture REOFFER) bằng tổng các `lotPrice`
cùng gói — cùng một đồng tiền theo số học trong gói, không phải đoán. `tax_basis`
luôn `UNKNOWN`: nguồn không nói giá đã gồm thuế hay chưa.

**Grain:** UNIQUE `(owner, role, currency, tax)` chỉ chứa một giá mỗi
(owner, role). Giá chào lại là theo từng (nhà thầu, lot): khi một nhà thầu có
nhiều lot với giá khác nhau (1.395/5.303 nhà thầu trên kho thật), parser **không**
ghi row nào cho role đó mà ghi warning `OFFEROR_<code>_<ROLE>_VARIES_BY_LOT`
— các giá từng lot vẫn nằm trong `contractor_memberships.provenance_json`
(`source_rows`). Ghi một giá đại diện sẽ là tự chọn phần tử đầu, vi phạm §10.4.
Muốn chứa đủ giá từng (nhà thầu, lot) thì `money_amounts` cần thêm grain
(phạm vi/scope) — quyết định schema riêng, chưa làm ở v4.

---

## 8. Bảng tối thiểu U-3

| Bảng | Điểm chính |
|---|---|
| `page_receipts` | receipt tách rời từng bước (§2.3) + CHECK đơn điệu: không `page_committed` khi chưa `response_received`; không `proof_verified` khi chưa commit; không `snapshot_published` khi chưa verify |
| `watermarks` | một row per (source, family); FK `last_complete_interval_id` — watermark chỉ tiến qua interval COMPLETE (§4.4) |
| `reconcile_gaps` | `kind ∈ {MISSING_FROM_SOURCE_IN_VERIFIED_WINDOW, HASH_DRIFT, COUNT_DRIFT, GRANULARITY_DRIFT}`; **không có đường DELETE record** (§4.4, COV-10) |
| `unresolved_records` | xem §2; có `resolved_record_id` để một unresolved về sau được resolve mà không mất dấu vết |

---

## 9. Từ vựng trạng thái (C-2)

Mỗi tầng một bộ từ vựng, **mỗi bộ một CHECK**. BUG-03 là một đường code ghi `COMPLETED` vào
`interval_status`; giờ DB từ chối:

| Cột | Giá trị hợp lệ |
|---|---|
| `runs.status` | COMPLETED, PARTIAL, FAILED, BLOCKED, INTERRUPTED |
| `coverage_intervals.status` | COMPLETE, PARTIAL, FAILED, BLOCKED, INTERRUPTED |
| `fetch_attempts.outcome` | OK, HTTP_ERROR, TIMEOUT, BLOCKED, MALFORMED |
| `observations.status` | NEW, UPDATED, UPDATED_RAW, UNCHANGED, DUPLICATE_IN_RUN, UNRESOLVED |
| `deep_captures.lane_status` | NOT_ATTEMPTED, IN_PROGRESS, FULL, PARTIAL, FAILED, BLOCKED, STALE, LEGACY_PARTIAL |
| `source_records.version_state` | KNOWN, MISSING, UNPARSABLE |
| `raw_objects.integrity_status` | OK, CORRUPT, QUARANTINED |

Thêm hai CHECK cùng tinh thần "không cho trạng thái tự mâu thuẫn":
`coverage_intervals.status='COMPLETE' → proof_id NOT NULL` (BUG-15), và
`raw_revisions.reason='RAW_ONLY_CHANGE' → content_version_id IS NULL` (§3.5).

---

## 10. Redaction policy và revision (sửa đổi #3)

`raw_revisions.redaction_policy_version` NOT NULL, và `reason` phân biệt hai thứ trước đây dễ lẫn:

| `reason` | Ý nghĩa | Có content version? | Đếm vào `raw_revisions_count`? |
|---|---|---|---|
| `NEW_RECORD` | lần đầu thấy hồ sơ | có (seq 1) | có |
| `SEMANTIC_CHANGE` | nội dung chuẩn hoá đổi | có | có |
| `RAW_ONLY_CHANGE` | **nguồn** đổi bytes, cùng policy | không | có |
| `REPLAY` | cùng nội dung nguồn, **khác policy redaction** | không | **không** |

Vì sao phải tách: đổi chính sách redaction làm đổi bytes dẫn xuất → `record_object_sha` đổi, dù nguồn
không đổi gì. Tính chung là revision nghiệp vụ thì một lần đổi policy tạo hàng loạt "revision" giả,
phình counter và khiến UI-07 báo "raw đã đổi" cho thứ nguồn chưa từng làm.

Hai view tách sẵn: `business_raw_revisions` (ba reason đầu) và `policy_driven_revisions` (`REPLAY`).
`metric_counts.raw_revisions_count` đọc từ view thứ nhất; policy-driven đếm ở cột riêng.
CHECK bổ trợ: `reason <> 'REPLAY' OR content_version_id IS NULL`.

## 11. Bất biến của bằng chứng (sửa đổi #4)

Proof là bằng chứng. Nếu thứ nó viện dẫn sửa được âm thầm, proof cũ mô tả một quy tắc không còn tồn tại
và COV-17 mất khả năng tái hiện. Bốn trigger chặn:

| Trigger | Chặn |
|---|---|
| `trg_boundary_rule_immutable_after_use` | UPDATE `filter_boundary_rules` đã bị proof tham chiếu |
| `trg_boundary_rule_no_delete_after_use` | DELETE rule đang được tham chiếu |
| `trg_proof_immutable` | UPDATE proof đã bị interval tham chiếu |
| `trg_proof_no_delete` | DELETE proof đã bị interval tham chiếu |

Đường hợp lệ để đổi quy tắc: INSERT `rule_version` mới, proof mới trỏ vào đó. Rule **chưa** bị proof nào
tham chiếu vẫn sửa được — bảng không thành write-once, draft vẫn chỉnh được.

## 12. Lifecycle job vs kết quả run (sửa đổi #5)

§2.3 và §3.6 nói hai chuyện khác nhau nhưng trước đây chỉ có một cột:

| Trục | Cột | Từ vựng |
|---|---|---|
| Điều khiển (§2.3) | `runs.lifecycle` | PENDING, RUNNING, PAUSING, PAUSED, RESUMING, STOPPING, FINISHED, TERMINATED |
| Kết quả (§3.6) | `runs.status` | COMPLETED, PARTIAL, FAILED, BLOCKED, INTERRUPTED — **NULL khi chưa dừng** |

CHECK buộc khớp: `(lifecycle ∈ {FINISHED,TERMINATED}) = (status IS NOT NULL)`; có `status` thì phải có
`finished_at_utc`; `status = COMPLETED` chỉ khi `lifecycle = FINISHED`. Hệ quả cho G2: worker ghi `PAUSED`
không còn vỡ CHECK của từ vựng kết quả. §2.3 "process chết → INTERRUPTED, không tự đổi COMPLETED" giờ
là ràng buộc DB.

## 13. Promotion của ứng viên gộp (D-2)

Owner chốt: **không** tự promote `AMBIGUOUS → LINKED` bằng ngưỡng confidence.

- `identity_audit_actions` ghi `action`, `target_kind`, `target_ref`, `actor_sid`, `reason`,
  `evidence_json`, `undo_action_id` — đúng những gì §3.8 đòi cho merge/split thủ công.
- `actor_sid` là SID, không phải tên tự khai, khớp hướng xác thực SID ở §2.4.
- `action` đóng ở `{CONFIRM_LINK, REJECT_LINK, MERGE, SPLIT, UNDO}` — **không có** `AUTO_MERGE`, nên
  không đường nào cho một tiến trình nền gộp mà không có người.
- `trg_edge_promotion_requires_audit` chặn `relation_edges.confidence` đi từ `CANDIDATE` lên
  `EXACT`/`LINKED` khi không có audit row. Trigger đọc trong cùng transaction, nên writer "quên" ghi
  audit thì bị chặn chứ không âm thầm gộp nhầm.
- `trg_link_promotion_requires_audit` áp cùng nguyên tắc cho `entity_source_links.method`.

## 14. Điều còn để ngỏ

| ID | Mở | Trạng thái |
|---|---|---|
| D-4 | Natural key của detail/HSMT/Reoffer (`inputResultId`, `notifyId+document_kind+processApply`) chưa đối chiếu được trong adapter home | Deep lane tiếp tục cách ly theo D-4; verify ở G1 |
| D-5 | `serving_meta` / `software_migrations` chưa có ràng buộc cross-table với warehouse | Thuộc G1 storage |
| D-6 | `documents`/`lots`/`goods_rows` duyệt làm **schema nền**; trường chi tiết phụ thuộc API deep chưa canary thì **chưa khoá** | Theo D-3; bổ sung sau canary là migration additive |
| D-7 | `filter_boundary_rules` hiện chứa giá trị **giả định** (`CLOSED_LOCAL`, offset 420) | Chưa canary; trigger đã chặn cấp `COMPLETE` khi `canary_verified=0` |

### Đã chốt trong đợt này

- **D-0**: bỏ `object_kind`, vai trò suy từ cạnh — đã triển khai.
- **D-1**: giữ `identity_key = id1:<sha256>`, **không** thêm `identity_components`. Yêu cầu "phải tái lập
  được digest từ các cột canonical" được khoá bằng test
  `test_identity_digest_is_recomputable_from_stored_columns` — đọc `family`/`natural_key`/`version_state`/
  `version_raw` từ row, tính lại digest, đòi khớp cột đã lưu.
- **D-2**: không auto-promote — đã triển khai bằng audit table + trigger (§13).
- **D-3**: duyệt schema nền, chưa khoá trường phụ thuộc deep API (§14 D-6).

---

## 15. Test khoá các quy tắc trên

`tests/contract/test_g0_b_invariants.py` — **69 test**, chạy trên SQLite thật (`:memory:`,
`PRAGMA foreign_keys=ON`), 0.4 s. Xem `GATE-G0-B/G0-B-CLOSURE.md` cho bảng ánh xạ finding → test.
