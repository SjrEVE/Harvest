# G0-B FINAL FIXES — đợt 2 (03/11/2026)

Owner duyệt hướng G0-B và ra 5 sửa bắt buộc trước freeze. Tài liệu này bổ sung cho
`G0-B-CLOSURE.md`; tất cả đã triển khai vào schema và có test chạy trên SQLite thật.

## A. Năm sửa đổi bắt buộc

| # | Yêu cầu | Triển khai | Test |
|---|---|---|---|
| 1 | Mâu thuẫn raw SHA dùng chung hai vai trò vs `CHECK(response_object_sha <> record_object_sha)` | **Bỏ CHECK.** Thay bằng bảng `object_derivation` (response, record, locator, redaction_policy_version) + trigger `trg_observation_derivation` chặn observation không có chứng cứ dẫn xuất | `test_identical_bytes_in_both_roles_is_now_representable`, `test_response_hash_cannot_be_recorded_as_a_record_without_derivation` |
| 2 | Test chứng minh RECORD được trích đúng từ RESPONSE qua `record_locator` | `extract_by_locator()` thực hiện trích thật — RFC 6901 pointer cho JSON, `row:<n>` cho payload là mảng — rồi so digest kết quả trích với `record_object_sha` đã ghi. Cặp (response, record, locator) bịa ra bị trigger chặn | `test_record_object_is_actually_extracted_from_response_via_locator`, `test_row_locator_resolves_array_payloads`, `test_locator_that_does_not_resolve_is_rejected_not_silently_null` |
| 3 | `redaction_policy_version` đổi mà hồ sơ nguồn không đổi → không tạo revision nghiệp vụ giả | `raw_revisions.redaction_policy_version` NOT NULL. `RAW_ONLY_CHANGE` = **nguồn** đổi cùng policy; `REPLAY` = cùng nội dung nguồn, khác policy. Hai view tách sẵn `business_raw_revisions` / `policy_driven_revisions`; `metric_counts.raw_revisions_count` chỉ đọc view thứ nhất | `test_redaction_policy_change_does_not_invent_a_business_revision`, `test_replay_may_not_carry_a_content_version` |
| 4 | Filter boundary rule và tham số mà proof lịch sử tham chiếu không sửa tại chỗ được | 4 trigger: `trg_boundary_rule_immutable_after_use`, `trg_boundary_rule_no_delete_after_use`, `trg_proof_immutable`, `trg_proof_no_delete`. Rule **chưa** bị proof nào tham chiếu vẫn sửa được, nên bảng không thành write-once | `test_boundary_rule_cited_by_a_proof_is_immutable`, `test_unreferenced_boundary_rule_is_still_editable`, `test_proof_referenced_by_an_interval_is_immutable` |
| 5 | Làm rõ lifecycle `runs.status` so với `PAUSED/RUNNING` của job | Tách `runs.lifecycle` (điều khiển, §2.3) khỏi `runs.status` (kết quả, §3.6, NULL tới khi run dừng). CHECK `(lifecycle ∈ {FINISHED,TERMINATED}) = (status IS NOT NULL)`; `COMPLETED` chỉ khi `FINISHED` | `test_run_lifecycle_and_outcome_are_separate_axes` |

### Vì sao sửa #1 là sửa đúng hướng, không phải sửa cho qua

CHECK so-hash vừa **quá chặt** vừa **quá lỏng**:

- Quá chặt: trong store content-addressed thuần (D-0), nếu bytes của một trang thực sự bằng bytes của
  một hồ sơ thì hai hash bằng nhau là hợp lệ — mà đó đúng là hình dạng `obs.raw = doc` của code cũ
  (§13.1). CHECK từ chối một row đúng.
- Quá lỏng: nó hoàn toàn không thấy được cặp hash *khác nhau thật* nhưng chưa từng được trích từ nhau,
  tức là lỗi gán nhầm mà §3.2 định chặn.

`object_derivation` chặn đúng thứ cần chặn và cho phép đúng thứ cần cho phép.

## B. D-1 / D-2 / D-3 cũng đã vào schema

- **D-1** — giữ `identity_key = id1:<sha256>`, **không** thêm `identity_components` trùng lặp. Yêu cầu
  "phải tái lập được digest từ các cột canonical" khoá bằng test
  `test_identity_digest_is_recomputable_from_stored_columns`: đọc `family`/`natural_key`/`version_state`/
  `version_raw` từ row, tính lại digest, đòi khớp — trên 5 canonical tuple gồm cả `MISSING` và
  notifyNo có dấu tiếng Việt. Test thứ hai (`test_missing_and_zero_zero_stay_distinct_end_to_end`) asserts
  cùng phân biệt đó **qua DB**, không chỉ qua encoder.
- **D-2** — `identity_audit_actions` ghi `action` (đóng ở `CONFIRM_LINK|REJECT_LINK|MERGE|SPLIT|UNDO`,
  **không có** `AUTO_MERGE`), `actor_sid` là SID thật, `reason`, `evidence_json`, `undo_action_id`.
  Hai trigger chặn `relation_edges.confidence` và `entity_source_links.method` leo thang khi thiếu audit
  row. Test dựng đúng trường hợp hấp dẫn nhất (title giống 93%) và đòi DB từ chối, rồi cho qua khi có audit.
- **D-3** — `documents`/`lots`/`goods_rows` ghi là **schema nền**; trường phụ thuộc API deep chưa canary
  để mở. Bổ sung sau canary là additive migration, không phải đổi khoá.

## C. Số đo cuối

```
ddl_contract_check  §3.10 baseline : PASS 12 / GAP  8 / FAIL 23
ddl_contract_check  G0-B final     : PASS 43 / GAP  0 / FAIL  0
pytest (toàn bộ)                   : 135 passed   (71 là contract test, 0.45 s)
ruff check src tools tests         : All checks passed
secret-gate                        : 59 file staged, 0 findings
DDL trên SQLite 3.53.1             : 41 bảng, 6 view, 8 trigger, 54 FK khai báo
PRAGMA integrity_check             : ok
PRAGMA foreign_key_check           : 0 vi phạm
```

Bảng dưới là 6 check mới đo riêng 5 sửa đổi, chạy trên cả hai DDL để chứng minh checker phân biệt được:

| Check | §3.10 baseline | G0-B final |
|---|---|---|
| `CL-ROLE` (dẫn xuất thay CHECK so-hash) | FAIL | PASS |
| `CL-REDACT` (policy không sinh revision) | FAIL | PASS |
| `CL-IMMUT` (rule/proof bất biến) | FAIL | PASS |
| `CL-LIFECYCLE` (lifecycle tách outcome) | FAIL | PASS |
| `CL-SHA` (xung đột vai trò) | FAIL | PASS |
| `CL-O4` (quarantine + dẫn xuất) | FAIL | PASS |

`docs/ddl/v1_3_reference.sql` đã dán nhãn lại thành **BASELINE, KHÔNG PHẢI SPEC HIỆN HÀNH**, vì Master
Spec đã được cập nhật tại §3.2/§3.4/§3.6/§3.10 (nhãn `[G0-B]`) nên file đó không còn khớp từng ký tự với
thân bài. Giữ nó làm mốc so sánh — 18/43 check chỉ đóng được nhờ phép so sánh trước/sau đó.

## D. Danh sách điểm CHƯA kiểm chứng

Không tô xanh. Mỗi mục ghi rõ rủi ro nếu bỏ qua.

| # | Chưa verify | Vì sao | Rủi ro |
|---|---|---|---|
| V-1 | **Toàn bộ contract test chạy qua helper, không qua writer thật** | `ingest_record()` là transcription của §3.5 do chính tôi viết. Test xanh chứng minh **schema cho phép** bảng quyết định, chưa chứng minh engine sẽ làm đúng | G1 phải chạy lại 71 test trên writer thật; writer sai thì test hiện không biết |
| V-2 | **`object_derivation` mới chứng minh là CẦN, chưa chứng minh là ĐÚNG** | Trigger chặn observation thiếu derivation row, nhưng không có gì kiểm tra row derivation đó phản ánh trích xuất thật. `extract_by_locator` chạy trong test, không chạy trong writer | Writer có thể ghi derivation row bừa. G1 phải buộc derivation sinh từ extraction thật |
| V-3 | **`filter_boundary_rules` chứa giá trị GIẢ ĐỊNH** (`CLOSED_LOCAL`, offset 420, `conversion_params`) | Chưa canary VNEPS. §13.2 vẫn để ngỏ | Trigger đã chặn `COMPLETE` khi `canary_verified=0` nên chưa claim coverage sai được — nhưng bản thân quy tắc chưa đúng |
| V-4 | **Natural key của detail/HSMT/Reoffer chưa đối chiếu** | Script nằm ở `analysis/crawl_*`, đang QUARANTINE (owner xác nhận D-4) | Port deep lane không verify là lặp lại BUG-05 |
| V-5 | **Hiệu năng 1M records (§7.4) chưa đo** | Chưa có dữ liệu lớn. 54 FK + 8 trigger + 6 view có thể có giá | `raw_object_roles` UNION 18 cạnh và `orphan_object_rows` có thể chậm ở scale; UI-09 không nên gọi đồng bộ |
| V-6 | **Đường chuyển đổi `redaction_policy_version` thật chưa có** | Chưa có nhiều policy để migrate object cũ sang policy mới | Test #3 dùng policy 1→2 tự tạo, chưa phải kịch bản thật |
| V-7 | **Trigger promotion chưa test với 2 tiến trình / transaction thật** | Chỉ test một kết nối `:memory:` | Race giữa worker và GUI ở G2 có thể cho phép promote không audit |
| V-8 | **A1 token dashboard chưa thu hồi** | Không có quyền InstaBox / GitHub / secret store | Tunnel host vẫn resolve; origin chết nên chưa khai thác được, nhưng **không tự hết** |
| V-9 | **A8 VM Windows sạch NOT_TESTED** | Windows container không có GUI subsystem; máy này sẵn Python/Git/MSVC | Không kết luận được hành vi trên máy khách chưa có gì |
| V-10 | **Qt per-module license files chưa đối chiếu** | PySide6 wheel chỉ ship `LicenseRef-Qt-Commercial.txt` | Thuộc legal / G4 (O-6) |

## E. Trạng thái G1

**READY.** Không còn FAIL hay GAP nào trên contract check; 5 sửa bắt buộc đã vào schema và có test.

Bốn điều kiện khi port — không chặn bắt đầu, chặn exit:

1. Adapter **không** được `or "00"` (`egp_home.py:158,341`); regression test phải fail trên code cũ.
2. Writer thật phải chạy xanh **71 contract test**, không viết lại test.
3. `object_derivation` phải sinh từ extraction thật trong writer (V-2).
4. Boundary rule phải được canary trước khi bất kỳ interval nào nhận `COMPLETE` (V-3).

Thứ tự owner đã duyệt, giữ nguyên: 6 adapter → writer + raw object store → contract tests qua writer →
BACKFILL/INCREMENTAL/RECONCILE + resolver → fault injection + benchmark → **báo cáo trước canary**.

## F. Cách tái lập

```powershell
cd D:\Harvest\santhau-data-studio
uv sync --all-groups

# DDL thực thi được, FK bật thật
python -c "import sqlite3,pathlib;c=sqlite3.connect(':memory:');c.execute('PRAGMA foreign_keys=ON');c.executescript(pathlib.Path('docs/ddl/g0_b_proposed.sql').read_text(encoding='utf-8'));print(c.execute('PRAGMA integrity_check').fetchone()[0])"

# Contract check — baseline vs final
python tools/ddl_contract_check.py --sql docs/ddl/v1_3_reference.sql
python tools/ddl_contract_check.py --sql docs/ddl/g0_b_proposed.sql

# Test
python -m pytest tests/contract -q     # 71 passed
python -m pytest                       # 135 passed
python -m ruff check src tools tests   # All checks passed
```

**DỪNG SAU G0-B.** Không tự chuyển G1, không push/PR/deploy, không canary VNEPS.
59 file vẫn staged, chưa commit.
