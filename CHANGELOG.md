# Changelog — Săn Thầu Data Studio

Chỉ ghi thay đổi quan trọng tới hợp đồng kỹ thuật. Spec nền: **Master Spec Consolidated v1.3**
(không đổi; mọi mục dưới là cụ thể hoá hoặc sửa đổi cần owner phê chuẩn).

## RC3-11.1 — Parser v6, sửa lỗi M1 NO_TABLE cho goodsList phẳng (13/11/2026)

- **Parser v6** (`PARSER_VERSION` 5→6): `goodsList` có thêm dạng hàng phẳng thứ ba
  (lot LCNT_DB/CDTRG): entry tự là mặt hàng — `name`/`quantity`/`unit`/`description`/
  `origin`/`priceUnit`, `formValue` null, không có `lotContent.Table` — và `lotResultItems`
  null, nên không có đường fallback. `priceUnit` chính là đơn giá trúng thầu (đã kiểm chứng
  trên 7 record live: qty × priceUnit cộng lại đúng bằng `bidWiningPrice` của nhà thầu trúng
  ở 6/7; record còn lại lệch 206đ = 0,00009% do quantity float 5034.06 làm tròn ở nguồn).
  Parser v5 báo `M1_LOT_*_GOODSLIST_ENTRY_*_NO_TABLE` cho mỗi entry và fail cả record,
  làm rơi 144 mặt hàng + 144 `ITEM_WINNING_PRICE` (lot + `LOT_PRICE` vẫn được ghi).
  Parser v6 parse entry phẳng thành hàng hóa + `ITEM_WINNING_PRICE` từ `priceUnit`
  (source_path ghi đúng `.priceUnit`); entry phẳng có tên nhưng không có giá vẫn ghi hàng
  hóa, chỉ cảnh báo `GOODSLIST_FLAT_NO_ITEM_PRICE`, không fail record. Parser bump kích hoạt
  chọn lại 7 task PARSE_ERROR từ raw đã lưu (không gọi lại E-GP).
- **Không đổi**: không có migration mới (008/009 của RC3-11 đã đủ); M2/M3 vẫn RC3-12 sau.
- **Kiểm chứng**: parser v6 chạy trên cả 7 raw live: 0 problems, 144/144 hàng hóa +
  144/144 `ITEM_WINNING_PRICE`; CI pytest subset 536 passed, 4 skipped, 1 failed
  (`test_budget_windowing::test_early_split...` — fail sẵn y hệt trên baseline RC3-11
  sạch trong môi trường này, do httpx parse proxy env, không liên quan M1); chạy mở rộng
  (CI subset + test_m1_parser + test_deep_run + test_parser_v4_money + test_deep_retry +
  test_deep_capture + test_deep_reuse) 604 passed, 4 skipped, cùng 1 fail có sẵn đó;
  5 test hồi quy mới cho dạng phẳng (parse, không giá → cảnh báo, entry không tên vẫn lỗi,
  lot Table + lot phẳng cùng tồn tại, dispatch end-to-end ra FETCHED).

## RC3-11 — Lane M1, giá trúng thầu theo mặt hàng KQLCNT (13/11/2026)

- **Lane M1 mới** (`PARSER_VERSION` 4→5): giá trúng thầu theo từng mặt hàng trong KQLCNT
  qua endpoint `expose/contractor-input-result/get`, 1 request cho 1 record theo
  `inputResultId` (626.953 record, không dedup). Parser ghi `ITEM_WINNING_PRICE`
  (đơn giá trúng thầu: `lotResultItems[].formValue[].lotWiningPrice`, hoặc `bidPrice`
  của item trong `goodsList` khi không có `lotWiningPrice`) và `ITEM_BID_PRICE` (chỉ khi
  `bidPrice` khác `lotWiningPrice`), kèm `LOT_PRICE`/`LOT_ESTIMATE_PRICE` theo lot; tiền tệ
  lấy từ `bidPriceUnit` của DTO (VND), không có mã tiền tệ thì UNPARSABLE chứ không đoán.
  **Migration 008** mở rộng enum role của `money_amounts` (2 role mới); **migration 009**
  mở rộng enum lane của `deep_captures` và enum job_kind của `runs` cho `M1` (rebuild 2 bảng,
  giữ nguyên dữ liệu; file DDL v1 không sửa).
- **Canary M1** (13/11/2026, 2 record live IB2600073112 + IB2600363545): endpoint bắt buộc
  có tham số query `token=` — không token thì HTTP 400; token là presence-only, server không
  kiểm tra giá trị (dummy và rỗng đều HTTP 200, payload giống hệt nhau) nên collector gửi
  `token=` rỗng, không giải captcha, không có credential trong URL. Bulk probe: `{"id": [...]}`
  → HTTP 400, `{"ids": [...]}` → HTTP 500 — **không có endpoint bulk**; backfill vẫn 1
  request/record (~22 ngày Linode-alone, ~7–8 ngày chia 3 máy).
- **`deep-sweep --lane M1`** (mặc định chỉ xem trước, `--execute` mới xếp hàng; tối đa 10
  record/job): quét bù M1 cho các record KQLCNT chưa có capture FETCHED/SOURCE_EMPTY.
  Auto-deep nhận thêm lane M1 (KQLCNT có `inputResultId`), nên record mới về sau tự có M1.
  `deep-retry --lane` thêm `M1`; `deep-status` hiện cả lane M1.
- **Không đổi**: M2/M3 (biên bản mở thầu, điểm kỹ thuật) chưa làm — RC3-12 sau M1.
- **Kiểm chứng**: canary M1 PASS (0 shape problems, raw lưu tại store canary); CI pytest subset
  537 passed, 4 skipped; chạy mở rộng (CI subset + test_m1_parser + test_deep_run +
  test_parser_v4_money + test_deep_retry + test_deep_capture + test_deep_reuse) 600 passed,
  4 skipped; full suite (trừ 30 file Windows-only): 720 passed, 21 failed, 14 skipped — toàn
  bộ 21 fail đều fail sẵn trên baseline RC3-10 (import Windows-only `workers.ipc` / thiếu
  PySide6 trên venv Linux, không liên quan M1), 0 regression;
  self-test-deep PASS (money_amounts: 22 trên store scratch).

## RC3-10 — Parser v4, deep retry, HSMT 1958/1959 (08/11/2026)

- **Parser v4** (`PARSER_VERSION` 3→4): trích giá vào `money_amounts` từ raw đã lưu — REOFFER
  `reofferPrice`/`reofferPriceFinal`, `bidPrice`/`bidFinalPrice`, `priceInit`/`priceStep`; HSMT
  `lotPrice`/`lotEstimatePrice` theo lot. Tiền tệ VND, `tax_basis` UNKNOWN (nguồn không nói đã
  gồm thuế hay chưa). **Migration 007** mở rộng enum role của `money_amounts` (9 role mới:
  REOFFER_PRICE, REOFFER_PRICE_FINAL, BID_FINAL_PRICE, PACKAGE_PRICE, PACKAGE_PRICE_INIT,
  PACKAGE_PRICE_STEP, LOT_PRICE, LOT_ESTIMATE_PRICE, LOT_PLAN_PRICE); file DDL v1 không sửa.
- **replay-deep mở rộng**: nhắm mọi capture còn parser cũ (cả 3 lane HSMT/DETAIL/REOFFER), không
  chỉ HSMT PARSE_ERROR — chạy lại từ raw đã lưu, **0 request mạng**. Kỳ vọng ~20.4k money rows
  trên Linode sau replay.
- **HSMT form 1958/1959** đăng ký `STRUCTURED_CODES` sau canary toàn bộ 393 payload
  UNVERIFIED_FORM (1958: 296/296 form, 3,596/3,596 dòng; 1959: 96/96 form, 1,971 dòng hàng +
  931 dòng lot) — 392/393 payload trở thành STRUCTURED sau replay; 1 form 3150 còn lại
  (qty không tên, ngoài phạm vi).
- **Deep retry (P1-2)**: task lỗi liên tiếp được cooldown leo thang 1h→6h→24h→72h, quarantine sau
  lỗi thứ 5 (~4.3 ngày); chỉ mở lại khi listing đổi version, parser/adapter bump, hoặc lệnh tay
  `deep-retry`. TRANSIENT (timeout/429/5xx) không bao giờ bị quarantine. Batching: gom task theo
  lane, tối đa 10 record/job, ≤3 job mỗi lần quét (trước đây tới 30 job).
- **lots.goods_count**: điều tra xong — KHÔNG phải bug (SUM `goods_count` khớp `goods_rows` có
  `lot_id`; ~64% gói thầu không có lot header trong dữ liệu nguồn). Không đổi code.
- **Không đổi**: P2 tải tài liệu (document download) chưa làm — cần canary endpoint live, báo cáo riêng.
- **Kiểm chứng**: CI pytest subset 602 passed, 4 skipped; self-test-deep PASS (money_amounts: 22
  trên store scratch).

## G0-B — Contract closure (03/11/2026)

### Quyết định owner đã triển khai

- **O-3 identity.** `natural_key` bỏ phiên bản công bố (TBMT = `notifyNo`), version ở
  `version_state`/`version_raw`. `identity_key` chuyển từ chuỗi nối `family|key|state|raw` sang
  **canonical digest có version** (`id1:<sha256>`), length-tagged nên không thể collision vì delimiter,
  NFC-normalised nên Unicode không tách đôi một hồ sơ. `MISSING` có NULL riêng và **bị từ chối** nếu
  kèm `version_raw`. Đã xác minh natural key của cả 6 family bằng cách đọc `egp_home.py`
  (`id_fields`, dòng 235/244/265/283/300/315), không đoán.
- **O-4 unresolved.** Ba nhánh tách bạch: có identity → record; trích được nhưng thiếu identity →
  RECORD object + `UNRESOLVED` observation; không parse được → `quarantined_responses` mức page,
  **không** tạo record và **không** tạo observation. Thêm `CHECK (response_object_sha <> record_object_sha)`.
- **O-5 deep capture.** `deep_capture_history` (bất biến, INSERT-only) tách khỏi `deep_captures`
  (latest + `latest_capture_seq` + `stale` + `refresh_due_at_utc`). Refresh không xoá lịch sử.

### Sửa đổi cần owner phê chuẩn

- **Bỏ `object_kind` khỏi `raw_objects`** (đề xuất `[G0B-SHA]`). §3.10 vừa đặt `sha256 PRIMARY KEY`
  vừa đặt `object_kind`, hai ràng buộc loại trừ nhau khi một payload đảm hai vai trò — xảy ra thật với
  hình dạng code cũ `obs.raw = doc` (§13.1). Vai trò nay suy ra qua view `raw_object_roles`.
  Đã chứng minh bằng test dựng DB từ §3.10 nguyên bản: `UNIQUE constraint failed`.

### Phát hiện mới khi đối chiếu adapter nguồn

- `egp_home.py:158` và `:341` làm `version = _first(...) or "00"`. **Dòng 341 là đường dùng chung cho
  cả sáu family**, tức adapter cũ biến mọi version thiếu thành `"00"` trước khi identity kịp thấy —
  đúng BUG-08 và là điều O-3 cấm. Port rule cho G1: adapter trả `(version_state, version_raw)`,
  không trả chuỗi đã chữa.

### Đóng finding

| Finding | Trạng thái |
|---|---|
| C-2 từ vựng không CHECK | **đóng** — CHECK trên `interval_status`/`attempt_outcome`/`observation_status`/`lane_status`/`reason`. DB giờ từ chối `COMPLETED` trong `interval_status` |
| C-5 mồ côi | **đóng** — view `orphan_object_rows`; ghi rõ file mồ côi **không** phải câu hỏi SQL, thuộc bước quét filesystem |
| C-6 boundary rule | **đóng** — bảng `filter_boundary_rules` có id/version/timezone/precision/`conversion_params`/`canary_verified`; proof trỏ `boundary_rule_id` |
| C-7 / C-8 | **đóng** bởi canonical identity serialization |
| C-9 | **đóng** — `current_content_hash NOT NULL` |
| C-10 | **đóng** — `coverage_proofs` khai báo trước `coverage_intervals`; FK buộc đúng thứ tự ghi |
| U-1 resolver | **đóng** — `CoverageResolution` với `proven`/`gaps` disjoint, `can_skip` chỉ khi COMPLETE |
| U-2 money | **đóng** — `money_amounts` + `presence_state`, **không `DEFAULT 0`** ở mọi cột số; UNKNOWN ≠ 0 bị chặn ở schema |
| U-3 4 bảng | **đóng** — `page_receipts` (CHECK đơn điệu receipt), `watermarks`, `reconcile_gaps` (không có đường DELETE record), `unresolved_records` |
| B7.2 lý do BLOCKED | **đóng** — `source_access_events` với `reason_kind` đóng và `probe_uri` cấm query string |

### Ba phát hiện kỹ thuật đáng ghi

1. **SQLite cấm subquery trong CHECK** (`subqueries prohibited in CHECK constraints`). Ràng buộc
   "proof phải viện dẫn rule đã được canary" chuyển sang **trigger** `trg_proof_boundary_canary` —
   vẫn DB-enforced mà không phải nhân bản `canary_verified` sang bảng proof.
2. `tzdata` **bắt buộc trên Windows**: máy không có hệ thống timezone, `ZoneInfo("Asia/Ho_Chi_Minh")`
   raise. Đã thêm `tzdata==2026.6; sys_platform == 'win32'` vào lockfile. Repo nguồn pin cùng package
   vì đúng lý do này; tôi đã bỏ nó ở A6 vì khi đó chưa cần timezone.
3. Hai lỗi tôi mắc và bị chính test bắt: DDL ghi `length(identity_key) = 67` trong khi encoder sinh 68
   (mọi INSERT sẽ fail), và view `orphan_object_files` thực chất trả row mồ côi (tên sai dẫn tới
   "tưởng đã quét GC").

### Phương pháp

25 contract check gốc mã hoá **vị trí** spec đặt cột thay vì **hợp đồng** phải thỏa, nên khi DDL G0-B
chuyển nghĩa vụ sang chỗ khác (boundary ra bảng rule, orphan sang view, unresolved sang bảng
quarantine) chúng báo đó là *regression*. Đã viết lại placement-agnostic.

### Số đo

```
ddl_contract_check  §3.10 baseline : PASS 12 / GAP  8 / FAIL 19
ddl_contract_check  G0-B proposal  : PASS 39 / GAP  0 / FAIL  0
pytest                             : 122 passed (58 contract test, 0.27 s)
ruff check src tools tests         : All checks passed
secret-gate trên 55 file staged    : 0 findings
DDL trên SQLite 3.53.1             : 39 bảng, 4 view, 1 trigger, 43 index, 51 FK
```

### Trạng thái gate

- **G0-B:** đóng về schema; **NO-GO freeze DDL** tới khi owner trả lời D-0 (bỏ `object_kind`),
  D-1 (digest có kèm `identity_components` để debug không), D-3 (cột `documents`/`lots`/`goods_rows`).
- **G1: READY có 4 điều kiện** — xem `GATE-G0-B/G0-B-CLOSURE.md` §7.
- **A1:** vẫn **BLOCKED** chờ owner thu hồi token. **A8:** VM Windows sạch **NOT_TESTED**.
- Boundary rule hiện mang giá trị **giả định** (`CLOSED_LOCAL`, offset 420) — chưa canary, nên chưa
  được cấp `COMPLETE` cho bất kỳ interval nào.

## G0-B final fixes — đợt 2 (03/11/2026, owner duyệt)

Owner chấp thuận hướng và ra 5 sửa bắt buộc trước freeze. Tất cả đã vào schema + có test.

### Năm sửa đổi

1. **Bỏ `CHECK (response_object_sha <> record_object_sha)`**, thay bằng `object_derivation` + trigger
   `trg_observation_derivation`. CHECK cũ vừa quá chặt vừa quá lỏng: quá chặt vì trong store
   content-addressed thuần (D-0) hai vai trò cùng bytes cho hash bằng nhau **hợp lệ** — đúng hình dạng
   `obs.raw = doc` của code cũ; quá lỏng vì nó không thấy được cặp hash khác nhau thật nhưng chưa từng
   trích từ nhau.
2. **Test chứng minh dẫn xuất thật**, không so hash: `extract_by_locator()` thực hiện trích theo
   RFC 6901 (và `row:<n>` cho payload là mảng), so digest kết quả với `record_object_sha` đã ghi.
3. **`raw_revisions.redaction_policy_version` NOT NULL** + tách `REPLAY` (khác policy, nguồn không đổi)
   khỏi `RAW_ONLY_CHANGE` (nguồn đổi). Hai view `business_raw_revisions` / `policy_driven_revisions`;
   `metric_counts.raw_revisions_count` chỉ đếm loại nghiệp vụ → đổi chính sách redaction không phình counter.
4. **4 trigger bất biến**: rule/proof đã bị tham chiếu thì không sửa, không xoá tại chỗ được. Rule chưa
   bị tham chiếu vẫn sửa được (draft không thành write-once). Đường hợp lệ: INSERT `rule_version` mới.
5. **Tách `runs.lifecycle` (điều khiển, §2.3) khỏi `runs.status` (kết quả, §3.6)**. Trước đây một cột làm
   cả hai nên worker ghi `PAUSED` vỡ CHECK của từ vựng kết quả. CHECK mới buộc `COMPLETED` chỉ khi
   `FINISHED` — đúng §2.3 "process chết → INTERRUPTED, không tự đổi COMPLETED".

### D-1/D-2/D-3

- **D-1**: giữ digest `id1:<sha256>`, không thêm `identity_components`. Khoá yêu cầu "tái lập được từ cột
  canonical" bằng test đọc 4 cột, tính lại digest, đòi khớp — trên 5 tuple gồm cả `MISSING` và notifyNo
  có dấu tiếng Việt.
- **D-2**: `identity_audit_actions` (action đóng, `actor_sid` là SID, có `undo_action_id`) + 2 trigger chặn
  `CANDIDATE → EXACT/LINKED` khi thiếu audit. Không có `AUTO_MERGE` trong từ vựng.
- **D-3**: `documents`/`lots`/`goods_rows` là schema nền; trường phụ thuộc deep API chưa canary để mở.

### Cập nhật Master Spec v1.3 (một nguồn duy nhất, không tạo bản song song)

Nhãn `[G0-B]` tại §3.2, §3.4, §3.6, §3.10; §9 G0-B chuyển sang **ĐÓNG**; §14 thêm mục
"v1.3 — điều chỉnh G0-B". `docs/ddl/v1_3_reference.sql` dán nhãn lại thành **BASELINE, không phải spec
hiện hành** vì nó không còn khớp từng ký tự với thân bài sau điều chỉnh.

### Ba lỗi phương pháp bị chính công cụ bắt lại

1. `ruff format` tách lời gọi `log(probe=...)` ra nhiều dòng, làm marker `secret-scan:allow` rớt khỏi dòng
   chứa fake token — gate chặn đúng. Đây là lần thứ ba cùng một loại lỗi; đã chuyển sang directive cấp file.
2. Check `CL-ROLE` ban đầu so chuỗi trên **toàn văn bản**, nên đọc cả comment giải thích việc *đã bỏ*
   CHECK và tưởng CHECK vẫn còn. Phải strip comment trước khi so.
3. Helper `add_record` tự tính `normalized` bỏ qua giá trị caller truyền → content hash sai, làm test
   DEDUP-09 fail. Sửa để caller kiểm soát normalized.

### Số đo

```
ddl_contract_check  §3.10 baseline : PASS 12 / GAP  8 / FAIL 23
ddl_contract_check  G0-B final     : PASS 43 / GAP  0 / FAIL  0
pytest                             : 135 passed (71 contract test, 0.45 s)
ruff check                         : All checks passed
secret-gate                        : 59 file staged, 0 findings
DDL SQLite 3.53.1                  : 41 bảng, 6 view, 8 trigger, 54 FK
PRAGMA integrity_check / fk_check  : ok / 0 vi phạm
```

### Chưa kiểm chứng (10 mục, ghi trong G0-B-FINAL-FIXES.md §D)

Đáng chú ý nhất: **V-1** test chạy qua helper chứ không qua writer thật — xanh hôm nay chứng minh schema
*cho phép* bảng quyết định §3.5, chưa chứng minh engine sẽ làm đúng; **V-2** `object_derivation` mới chứng
minh là *cần*, chưa chứng minh là *đúng*; **V-3** boundary rule vẫn là giá trị giả định chưa canary.

## G0-A — Security & Source Quarantine (03/11/2026)

- A2/A3/A4/A5/A6/A7 **PASS**; A1 **BLOCKED** (xác minh PASS, thu hồi cần owner); A8 **PASS có điều kiện**.
- Token dashboard (không phải credential VNEPS) lộ tại `HANDOFF_WEB_DISPLAY.md:127`, fingerprint
  `144e724076c545c8`; commit `6a6fcb55` đã ở `origin/codex/radar-harvester`.
- 14 file trong repo nguồn chứa dữ liệu đấu thầu thật; 2 trong số đó là **code test**, không phải fixture.
- Sanitizer sinh fixture tổng hợp, verifier chứng minh 0 giá trị thật sót lại trên 5 capture.
- Packaging spike: PySide6-Essentials + Nuitka standalone + Inno Setup, installer 39 MB, cài/chạy/gỡ sạch.
- Quyết định: dùng `PySide6-Essentials` thay metapackage (loại WebEngine/PDF ~150 MB, trái §2.1 vốn
  cấm webview); `orderedset` bỏ vì build fail trên Python 3.12.
