# SECURITY-AUDIT — G0-A (A1–A4)

**Ngày:** 03/11/2026 · **Phạm vi:** `Gia-Su-AI/DauThauAI` @ `aa65f0a6edcb98877d705c8495ca00c79303f86e`
**Trạng thái tài liệu:** ĐÃ REDACTED — không chứa giá trị credential. Mọi tham chiếu tới secret dùng SHA-256 prefix 16 ký tự.

---

## 1. A1 — Secret exposure

### 1.1 Claim của spec và kết quả xác minh

| Claim (§1.2 BUG-01) | Kết quả | Bằng chứng |
|---|---|---|
| `HANDOFF_WEB_DISPLAY.md:127` chứa token sống | **CONFIRMED** | `tools/radar-harvester/HANDOFF_WEB_DISPLAY.md` dòng 127, blob tại `aa65f0a` |
| …+ host tunnel công khai | **CONFIRMED** | cùng dòng: host `3000-***[45 ký tự].instabox.tech` (không ghi lại slug) |

Định danh secret (không phải giá trị):

| Đối tượng | SHA-256 prefix | Độ dài |
|---|---|---|
| token query `?t=` | `144e724076c545c8` | 22 ký tự |
| host tunnel | `a02ef6612830adb9` | 45 ký tự |

Fingerprint này được tính **ba lần bởi ba đường code độc lập** và khớp nhau:
`_evidence/a1/probe_tunnel.py`, `tools/secret_scan.py`, `tools/git_history_secret_audit.py`.

### 1.2 Phát hiện chỉnh lại cách hiểu của spec: đây KHÔNG phải credential VNEPS

`serve_api.py` (dòng 4, 6, 286–289) cho thấy token `?t=` là **API token của bảng tiến độ harvester**,
guard một server đọc-only trên warehouse của chính máy đó, chạy ở `0.0.0.0:3000` và được mở ra
internet qua port tunnel InstaBox.

Chi tiết này đổi cách thu hồi và đổi blast radius, nên ghi rõ:

| | Nếu là credential VNEPS | Thực tế (dashboard token) |
|---|---|---|
| Thu hồi bằng | đổi mật khẩu/tài khoản VNEPS | restart `serve_api.py` với token mới, hoặc hạ tunnel |
| Blast radius | danh tính thầu của bạn với nguồn | đọc được bảng tiến độ + mọi dữ liệu board hiển thị |
| Ghi chú | — | `Access-Control-Allow-Origin: *` (`serve_api.py:253`) → **một trang web bất kỳ cũng gọi được API** nếu có token |

`serve_api.py:287` fail-closed đúng (`not TOKEN or token != TOKEN` → 404), nên đây là lộ credential,
không phải lộ lỗ xác thực.

### 1.3 Trạng thái khai thác được — đo, không suy luận

Probe duy nhất, **không gửi token** (GET `https://<host>/` không kèm `?t=`):

```
dns              : RESOLVES (2 records)
GET / (no token) : HTTP 502
```

Đọc kết quả:

- DNS **vẫn resolve** và TLS handshake **thành công** → hàng đợi tunnel của InstaBox còn sống.
- HTTP **502** → origin (`serve_api.py`) **đang chết**. Token hiện **không dùng được**.
- Kết luận: phơi nhiễm ở trạng thái **tiềm ẩn**, chưa active. Nó kích hoạt lại ngay khi ai đó
  chạy lại `serve_api.py` với cùng token đó — tức **không tự hết**.

### 1.4 A1 = BLOCKED cho thao tác thu hồi

Tôi **không** có quyền với: InstaBox box `santhau`, GitHub repo, hay secret store. Thu hồi là thao tác
ghi vào hệ thống ngoài → cần Founder. Việc owner cần làm:

1. **Hạ hoặc cấp lại route tunnel** cho hostname có fingerprint `a02ef6612830adb9`.
   Làm việc này *trước* — nó cắt đường khai thác ngay cả khi chưa đổi token.
2. **Xoay token dashboard**: khởi động lại `serve_api.py` với token sinh ngẫu nhiên ≥32 byte,
   và **lưu token mới trong secret store**, không ghi vào file docs nào.
3. **Không** đưa token vào URL query cho production Data Studio: §6 đã cấm, và chính cơ chế `?t=`
   là nguồn gốc sự cố này.
4. **Quyết định xử lý GitHub** (xem §2.3) — đây là quyết định của owner, không phải của coder.
5. Sau 1–4, chạy lại `tools/secret_scan.py --mode audit` trên nhánh để xác nhận 0 finding, và
   verify token cũ trả 404 → khi đó A1 mới đạt exit criterion "token cũ trả 404/401".

**Exit criterion hiện tại:** "token cũ trả 404/401" — **chưa verify được**, vì origin đang chết (502),
nên 404/401 và "server chết" không phân biệt được từ bên ngoài. A1 = **BLOCKED**, không phải PASS.

---

## 2. A2 — Git history

Ba pass độc lập trên `DauThauAI` (28.234 objects, 183 refs). Xem `history-audit-redacted.json`.

| Pass | Lệnh | Kết quả |
|---|---|---|
| Pickaxe (diff thêm/xoá string) | `git log --all -S<secret>` | **1 commit**: `6a6fcb5551b1` |
| Tree grep (tree hiện hành của từng ref) | `git grep -F -f <patterns> <refs>` | **2 refs** |
| Object sweep (kể cả blob mồ côi) | `git cat-file --batch-all-objects --batch` | **1 blob** |

### 2.3 Kết luận

- Commit duy nhất: `6a6fcb5551b1873df11cf76084004153e5e00e5c`, 02/11/2026 02:51:33 +07,
  author `SjrEVE`, chỉ thêm 1 file (138 dòng).
- Có mặt trong tree của `refs/heads/codex/radar-harvester` **và `refs/remotes/origin/codex/radar-harvester`**
  → **secret đã được push lên GitHub**.
- **Không** phải tổ tiên của `origin/main` (`git merge-base --is-ancestor` → false) → không nằm trong
  lịch sử nhánh default.
- Object sweep chỉ ra **đúng 1 blob** chứa pattern trong toàn bộ object database → **không có bản sao
  đã bị amend/force-push bỏ lại**. Đây là điểm mà tree grep và pickaxe đều không chứng minh được;
  nó làm giả thuyết "còn bản ẩn" bị loại.

### 2.4 Không rewrite history — và lý do đây là lựa chọn đúng

Theo chỉ thị G0-A, tôi không rewrite. Phân tích thêm: vì commit đã ở **remote**, rewrite local
không rút secret ra khỏi GitHub (nó vẫn reachable theo SHA, và có thể đã vào fork/PR cache).
Chi phí của rewrite (phá branch đang có người dùng, xung đột với session song song) lớn hơn lợi ích.

**Khuyến nghị cho owner:** thu hồi credential làm cho giá trị trong history trở nên vô ích; đó là
biện pháp có hiệu lực. Purge history chỉ cần nếu bạn muốn xoá cả *host tunnel* khỏi record công khai.
Hai lựa chọn:

| Lựa chọn | Nội dung | Tradeoff |
|---|---|---|
| A (khuyến nghị) | Rotate token + hạ tunnel, **không** đụng history | Rẻ, không phá branch. History vẫn lộ *hostname* |
| B | GitHub support purge cho commit `6a6fcb55` | Mất công, cần mở ticket; branch vẫn phải force-push → rủi ro cho session khác |

**A2 = PASS** cho phần rà soát (đủ 3 pass, có quyết định ghi rõ ở trên). **Chờ chữ ký owner** cho
lựa chọn A/B — spec §9 A2 yêu cầu "quyết định có chữ ký owner".

---

## 3. A3 — Cách ly dữ liệu thật

### 3.1 Bằng chứng gốc tự khai (mạnh hơn suy đoán từ shape)

| File | Tuyên bố trong repo | Nguồn |
|---|---|---|
| `tests/fixtures/egp_home_page0.json` | **"REAL captured payload"** — capture 31/10/2026 từ `POST https://muasamcong.mof.gov.vn/o/egp-portal-home/services/smart/search`, page 0, pageSize 10 | `tests/fixtures/egp_home_page0.README.md` |
| `examples/*` | **"Bản copy báo cáo + inventory của lần chạy thật đầu tiên (2026-10-31)"** | `examples/README.md` |
| `tests/fixtures/egp_search_tbmt.json` | synthetic, "Every identifier … is invented for tests" | `tests/fixtures/egp/README.md` (ngoài subtree harvester) |

Claim "no tokens/session material in the payload" của fixture README: **đã kiểm chứng PASS** — quét
cookie/authorization/bearer/session/JWT/long-hex trên cả 3 fixture → 0 hit.

### 3.2 Kiểm kê machine-scanned

`tools/real_marker_scan.py` trên 88 file tại `aa65f0a` → **14 file có marker danh tính**
(`source-real-data-inventory.json`):

| File | Marker danh tính | Loại |
|---|---|---|
| `tests/fixtures/egp_home_page0.json` | 81 (20 notifyNo, 47 số 10 chữ số, 4 tên tổ chức) | REAL — cấm mang sang |
| `examples/report-backfill.json` | 42 | REAL run output |
| `examples/report.json` | 41 | REAL run output |
| `examples/report-{backfill,backfill-jan2024,reconcile-jan2024}.md` | 40 mỗi file | REAL run output |
| `tests/fixtures/egp_home_type_samples.json` | 28 | **provenance chưa rõ — không có README** |
| `tests/test_serving.py` | 24 | dữ liệu thật nằm **trong code test** |
| `tests/fixtures/egp_search_tbmt.json` | 10 | SYNTHETIC (đã xác minh chuỗi provenance) |
| `tests/test_home_types.py` | 8 | dữ liệu thật trong code test |
| `analysis/crawl_detail_full.py` | 2 | id mẫu trong code |
| `HANDOFF_WEB_DISPLAY.md` | 0 (+1 tunnel host) | A1 |

Hai phát hiện ngoài phạm vi claim của spec, ghi lại vì chúng đổi kế hoạch port:

1. **`egp_home_type_samples.json` không có README provenance** nhưng chứa giá trị shape thật, gồm
   `ctPriceAfter` 11 chữ số. Spec chỉ nêu `egp_home_page0.json`. → đối xử như REAL.
2. **`tests/test_serving.py` và `tests/test_home_types.py` nhúng dữ liệu thật trong code.** Port test
   mà không sanitize sẽ mang dữ liệu thật vào repo mới **dưới dạng source code**, nơi secret-scan
   không bắt được (nó trông credential, không trông PII). → port các test này phải qua sanitizer.

### 3.3 Cách ly đã thực thi

- Không file nào của repo nguồn được copy vào `D:\Harvest\santhau-data-studio`.
- Fixture thật được trích ra **ngoài repo**: `%LOCALAPPDATA%\Temp\santhau-g0a-real\` (chỉ để chạy sanitizer).
- `.gitignore` mới chặn `data/`, `captures/`, `*.raw.json`, `warehouse*.sqlite` — để một lần capture
  thật không tình cờ trở thành file được commit.
- Quét lại repo mới: `tools/secret_scan.py --mode gate .` → **0 finding**; 35 file staged, 0 finding.

**A3 = PASS.**

---

## 4. A4 — Sanitizer

`tools/sanitizer.py` + `tests/unit/test_sanitizer.py` (27 test).

### 4.1 Ba lỗi thật tìm thấy trong lúc xây, mỗi lỗi có regression test

Đây là phần đáng giá nhất của A4 — không phải "code đã viết xong" mà là "các lỗ đã bị đóng":

| # | Lỗi | Hệ quả nếu không sửa | Test |
|---|---|---|---|
| 1 | List scalar (`contractorName: ["Công ty X"]`) mất tên field cha nên **không được biến đổi** | Tên nhà thầu thật lọt nguyên văn sang fixture "synthetic" | `test_list_of_names_is_transformed_regression` |
| 2 | `createdBy` khớp rule `created` → bị xử lý như timestamp; `_shift_timestamp` **âm thầm trả nguyên văn** với chuỗi không phải date | `createdBy = vn9876543210-30` (MST có chi nhánh) lọt | `test_createdby_principal_is_not_mistaken_for_a_timestamp` |
| 3 | `totalElements = 4321` bị xếp vào `keep` | Số đo thật của nguồn lọt | `test_aggregate_counts_are_synthesized_not_kept` |

Lỗi 1 và 2 là loại nguy hiểm nhất: công cụ báo "đã sanitize" trong khi giá trị thật còn nguyên.

### 4.2 Verifier là hợp đồng, shape-preservation là chủ đích

`find_leaks()` so **theo đường dẫn** (giá trị còn nguyên tại chỗ của nó) cộng kiểm tra cross-path
cho giá trị entropy cao. Lý do không so cả-document: nó báo mọi enum được giữ (`PARTIAL`, `KHAC`)
là leak → người dùng sẽ tắt tool. Có test khoá quyết định này:
`test_kept_enum_elsewhere_is_not_reported_as_a_leak`, `test_verify_detects_leak_when_output_is_a_copy`
(negative control: bản copy nguyên văn **phải** FAIL).

**Hệ quả quan trọng cho A3:** vì sanitizer **chủ đích giữ shape** (notifyNo vẫn đúng regex, giá trị
vẫn đúng độ lớn), một scanner dựa trên shape **không thể** phân biệt synthetic với thật. Bằng chứng
duy nhất có giá trị là `find_leaks`. Báo cáo này không dùng marker-scan làm bằng chứng A4.

### 4.3 Kết quả chạy trên capture thật

5 file thật → synthetic, seed 20261103, strategy_version 3:

| Cặp | identity_leaks | verdict |
|---|---|---|
| `egp_home_page0` | 0 | PASS |
| `egp_home_type_samples` | 0 | PASS |
| `egp_search_tbmt` | 0 | PASS |
| `ex_report` | 0 | PASS |
| `ex_report-backfill` | 0 | PASS |

Kiểm tra độc lập bổ sung trên chính file output:

- 10 `notifyNo` thật trong nguồn → **0** xuất hiện trong output.
- 8 `investorName` thật trong nguồn → **0** xuất hiện trong output.
- `muasamcong.mof.gov.vn` / `instabox.tech` → **0** trong output.
- Cấu trúc giữ nguyên: key, nesting, độ dài list, `record_locator` dùng được cho test B1/DEDUP-15.
- Deterministic: cùng seed → cùng output (test `test_same_seed_is_deterministic`).

**A4 = PASS.**

---

## 5. Việc còn mở

| ID | Hạng mục | Trạng thái | Chủ thể |
|---|---|---|---|
| S-1 | Hạ tunnel + rotate token dashboard | BLOCKED | Founder |
| S-2 | Chọn A (revoke) hay B (purge GitHub) cho commit `6a6fcb55` | chờ chữ ký | Founder |
| S-3 | Verify token cũ trả 404/401 sau rotate | NOT_TESTED (origin đang chết) | Founder + coder |
| S-4 | Provenance của `egp_home_type_samples.json` | chưa rõ | Founder |
| S-5 | `tests/test_serving.py`, `test_home_types.py` chứa dữ liệu thật trong code | đã ghi nhận, xử lý ở G1 | coder |
| S-6 | `egp_v2.py:213` truyền token VNEPS qua query string | đã xác nhận, thuộc B7/§6 | G0-B/G1 |
