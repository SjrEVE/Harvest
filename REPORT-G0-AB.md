# SĂN THẦU DATA STUDIO — BÁO CÁO TỔNG HỢP G0-A + G0-B

**Ngày:** 03/11/2026 · **Spec nền:** Master Spec Consolidated **v1.3** (một nguồn duy nhất; đã cập nhật
các mục điều chỉnh với nhãn `[G0-B]`, không tạo bản song song)
**Repo nguồn port:** `Gia-Su-AI/DauThauAI` @ `codex/radar-harvester` = `aa65f0a6edcb98877d705c8495ca00c79303f86e`
→ SHA khớp chính xác với spec. Repo nguồn **không bị sửa đổi**: chỉ `git show` / `ls-tree` / `log` read-only.
**Workspace:** `D:\Harvest\santhau-data-studio` · repo git local, **không remote, chưa commit**.

---

## 1. Kết luận

| | |
|---|---|
| **G0-A** | 6/8 mục **PASS**. A1 **BLOCKED** (xác minh xong, thu hồi cần quyền owner). A8 **PASS có điều kiện** (VM sạch NOT_TESTED) |
| **G0-B** | **ĐÓNG**. B1–B7 đóng về schema. 10 finding C/U đóng. 5 sửa bắt buộc đợt 2 đã vào schema + có test |
| **G1** | **READY** với 4 điều kiện khi port — không chặn bắt đầu, chặn exit (§8) |
| **Rủi ro còn lại lớn nhất** | V-1/V-2: test chạy qua helper tự viết, chưa qua writer thật (§7) |

### Số đo nghiệm thu — mọi dòng là một lệnh đã chạy, không phải tuyên bố

```
ddl_contract_check  §3.10 baseline : PASS 12 / GAP  8 / FAIL 23
ddl_contract_check  G0-B final     : PASS 43 / GAP  0 / FAIL  0
pytest (toàn bộ)                    : 135 passed
    tests/contract/test_g0_b_invariants.py      71   ← SQLite thật
    tests/unit/test_sanitizer.py                27
    tests/unit/test_ddl_contract.py             25
    tests/unit/test_secret_gate.py              10
    tests/unit/test_console_encoding.py          2
ruff check src tools tests          : All checks passed
secret-gate                         : 62 file staged, 0 findings
DDL trên SQLite 3.53.1              : 41 bảng, 6 view, 8 trigger, 54 FK khai báo, 45 index
PRAGMA integrity_check              : ok
PRAGMA foreign_key_check            : 0 vi phạm
sanitizer verify × 5 capture thật   : 5 × verdict PASS, identity_leaks = 0
installer                           : SanThau-Data-Studio-Setup-0.0.1-spike.exe, 40.807.031 bytes
```

---

## 2. Bảng gate

| # | Việc | Trạng thái | Bằng chứng chính |
|---|---|---|---|
| A1 | Thu hồi token đã lộ | **BLOCKED** · xác minh PASS | fingerprint `144e724076c545c8` tính độc lập bởi 3 đường code, khớp nhau; probe tunnel → HTTP 502 |
| A2 | Rà git history | **PASS** (chờ chữ ký owner cho quyết định) | 3 pass: pickaxe / tree-grep / object-sweep trên 28.234 objects |
| A3 | Cách ly dữ liệu thật | **PASS** | 14 file có marker danh tính; README nguồn tự khai "REAL captured payload" |
| A4 | Sanitizer | **PASS** | 27 test; 5/5 capture thật → synthetic, 0 giá trị thật sót |
| A5 | License | **PASS** (2 quyết định chờ legal) | 180/180 native file phân loại, 0 unclassified |
| A6 | Pin dependency | **PASS** | Python 3.12.14; `uv.lock` 15 packages; `requires-python = "==3.12.*"` |
| A7 | Pre-commit secret scan | **PASS** | 10 test end-to-end qua `git commit` thật; gate trên repo này 0 finding |
| A8 | Packaging smoke | **PASS có điều kiện** | installer build + cài + chạy + gỡ; **VM Windows sạch NOT_TESTED** |
| G0-A→G0-B | 10 finding C/U | **ĐÓNG** | `ddl_contract_check` 43/0/0 |
| G0-B | B1–B7 + 5 sửa bắt buộc | **ĐÓNG** | 71 contract test trên SQLite thật |

---

## 3. Phần A — An toàn & cách ly (G0-A)

### 3.1 Token đã lộ: spec hiểu sai bản chất, và điều đó đổi cách xử lý

`HANDOFF_WEB_DISPLAY.md:127` chứa token + host tunnel công khai — **CONFIRMED**.

Nhưng đây **không phải credential VNEPS**. `serve_api.py` (dòng 4, 6, 286–289) cho thấy `?t=` là API token
của **bảng tiến độ harvester**, guard một server đọc-only trên kho của chính máy đó, mở ra internet qua
InstaBox port tunnel.

| | Nếu là credential VNEPS | Thực tế |
|---|---|---|
| Thu hồi | đổi tài khoản VNEPS | restart `serve_api.py` với token mới / hạ tunnel |
| Blast radius | danh tính thầu với nguồn | đọc được bảng tiến độ + dữ liệu board hiển thị |

`Access-Control-Allow-Origin: *` (`serve_api.py:253`) làm sự cố nặng hơn: trang web bất kỳ gọi được API nếu
có token. `serve_api.py:287` fail-closed đúng (404), nên đây là **lộ credential**, không phải lỗ xác thực.

**Trạng thái khai thác — đo, không suy luận:** probe duy nhất, **không gửi token**:
`dns: RESOLVES` · `GET / (no token): HTTP 502`.
→ Route tunnel còn sống, origin đã chết. Phơi nhiễm **tiềm ẩn, chưa active, và không tự hết** — nó kích hoạt
lại ngay khi ai đó chạy lại `serve_api.py` với cùng token.

**Vì sao A1 = BLOCKED:** exit criterion "token cũ trả 404/401" **không verify được** lúc này, vì origin chết
(502) nên 404/401 và "server chết" không phân biệt được từ ngoài. Tôi không có quyền InstaBox / GitHub /
secret store. Thao tác owner cần làm, theo thứ tự:

1. Hạ hoặc cấp lại route tunnel cho hostname có fingerprint `a02ef6612830adb9` — cắt đường khai thác trước.
2. Xoay token dashboard (≥32 byte ngẫu nhiên), lưu token mới **trong secret store**, không ghi vào docs.
3. Production Data Studio **không** dùng token trên query string (§6 đã cấm; chính cơ chế `?t=` gây ra sự cố).
4. Chọn cách xử lý GitHub (§3.2) — quyết định của owner.
5. Sau đó chạy lại scanner và verify token cũ trả 404 → A1 mới đạt exit.

### 3.2 Secret đã nằm trên GitHub — và rewrite history không giải quyết được

Ba pass độc lập trên `DauThauAI` (28.234 objects, 183 refs):

| Pass | Lệnh | Kết quả |
|---|---|---|
| Pickaxe | `git log --all -S<secret>` | **1 commit**: `6a6fcb5551b1` |
| Tree grep | `git grep -F -f patterns <refs>` | **2 refs** |
| Object sweep | `git cat-file --batch-all-objects --batch` | **1 blob** |

- Commit duy nhất: `6a6fcb5551b1873df11cf76084004153e5e00e5c`, 02/11/2026 02:51:33 +07, author `SjrEVE`,
  chỉ thêm 1 file (138 dòng).
- Có trong tree của `refs/heads/codex/radar-harvester` **và `refs/remotes/origin/codex/radar-harvester`**
  → **đã push lên GitHub**.
- **Không** phải tổ tiên `origin/main` → không nằm trong lịch sử nhánh default.
- Object sweep tìm thấy **đúng 1 blob** chứa pattern trong toàn bộ object database → **loại được giả thuyết
  còn bản sao ẩn do amend/force-push**. Đây là thứ pickaxe và tree-grep đều không chứng minh được.

Vì commit đã ở remote, **rewrite history local không rút secret ra khỏi GitHub** (vẫn reachable theo SHA,
có thể đã vào fork/PR cache). Chi phí phá branch đang có người dùng lớn hơn lợi ích. Hai lựa chọn cho owner:

| | Nội dung | Tradeoff |
|---|---|---|
| **A (khuyến nghị)** | Rotate token + hạ tunnel, không đụng history | Rẻ, không phá branch. History vẫn lộ *hostname* |
| B | GitHub support purge cho `6a6fcb55` | Cần ticket; vẫn phải force-push → rủi ro cho session khác |

### 3.3 Dữ liệu thật: nhiều hơn spec nêu

**Bằng chứng gốc tự khai** (mạnh hơn suy đoán từ shape):

| File | Tuyên bố trong repo |
|---|---|
| `tests/fixtures/egp_home_page0.json` | **"REAL captured payload"** — capture 31/10/2026 từ `POST .../smart/search`, page 0, pageSize 10 |
| `examples/*` | **"Bản copy báo cáo + inventory của lần chạy thật đầu tiên (2026-10-31)"** |
| `tests/fixtures/egp_search_tbmt.json` | synthetic — "Every identifier … is invented for tests" (README ở `tests/fixtures/egp/`) |

Claim "no tokens/session material in the payload" của fixture README: **đã kiểm chứng PASS** (quét
cookie/authorization/bearer/session/JWT/long-hex → 0 hit).

Machine scan 88 file tại `aa65f0a` → **14 file có marker danh tính**. Đỉnh bảng:

| File | Marker | Loại |
|---|---|---|
| `tests/fixtures/egp_home_page0.json` | 81 | REAL — cấm mang sang |
| `examples/report-backfill.json` / `report.json` / 4 file `.md` | 40–42 mỗi file | REAL run output |
| `tests/fixtures/egp_home_type_samples.json` | 28 | **provenance chưa rõ — không có README** |
| `tests/test_serving.py` | 24 | dữ liệu thật **trong code test** |
| `tests/fixtures/egp_search_tbmt.json` | 10 | SYNTHETIC (chuỗi provenance đã xác minh) |
| `tests/test_home_types.py` | 8 | dữ liệu thật trong code test |

**Hai phát hiện ngoài claim của spec:**

1. `egp_home_type_samples.json` **không có README provenance** nhưng chứa shape thật (kể cả `ctPriceAfter`
   11 chữ số). Spec chỉ nêu `egp_home_page0.json` → đối xử như REAL.
2. `test_serving.py` và `test_home_types.py` nhúng dữ liệu thật **ngay trong source code test**. Spec chỉ
   cảnh báo fixture. Port 2 file này không sanitize sẽ mang dữ liệu thật vào repo mới **dưới dạng code**,
   nơi secret-scan **không bắt được** — nó tìm credential, không tìm PII.

**Cách ly đã thực thi:** không file nguồn nào copy vào repo mới; fixture thật trích ra
`%LOCALAPPDATA%\Temp\santhau-g0a-real\` (ngoài repo) chỉ để chạy sanitizer; `.gitignore` chặn `data/`,
`captures/`, `*.raw.json`, `warehouse*.sqlite`; quét lại repo mới → 0 finding, **0/10 notifyNo thật** và
**0/8 investorName thật** sót lại.

### 3.4 Sanitizer: ba lỗi thật tìm thấy khi xây, mỗi lỗi có regression test

Giá trị của A4 không phải "code đã viết xong" mà là "các lỗ đã bị đóng":

| # | Lỗi | Hệ quả nếu không sửa |
|---|---|---|
| 1 | List scalar (`contractorName: ["Công ty X"]`) mất tên field cha nên **không được biến đổi** | Tên nhà thầu thật lọt nguyên văn sang fixture "synthetic" |
| 2 | `createdBy` khớp rule `created` → xử lý như timestamp; `_shift_timestamp` **âm thầm trả nguyên văn** chuỗi không phải date | `createdBy = vn9876543210-30` (MST có chi nhánh) lọt |
| 3 | `totalElements = 4321` bị xếp vào `keep` | Số đo thật của nguồn lọt |

Lỗi 1 và 2 là loại nguy hiểm nhất: công cụ báo "đã sanitize" trong khi giá trị thật còn nguyên.

**Verifier là hợp đồng.** `find_leaks()` so **theo đường dẫn** + kiểm tra cross-path cho giá trị entropy
cao. So cả-document sẽ báo mọi enum được giữ (`PARTIAL`, `KHAC`) là leak → người dùng tắt tool. Có negative
control: bản copy nguyên văn **phải** FAIL.

**Caveat phương pháp quan trọng:** sanitizer **chủ đích giữ shape** (notifyNo vẫn đúng regex, giá trị vẫn
đúng độ lớn) để fixture còn dùng được cho parser. Vì vậy marker-scan theo shape **không thể** phân biệt
synthetic với thật. Bằng chứng duy nhất có giá trị là `find_leaks`. Báo cáo này không dùng marker-scan làm
bằng chứng A4.

Kết quả trên capture thật (seed 20261103, strategy_version 3): 5/5 `identity_leaks: 0`, `verdict: PASS`.

---

## 4. Phần B — Dependency, license, đóng gói

### 4.1 A6 — pin

`requires-python = "==3.12.*"`, `.python-version = 3.12.14`, `uv.lock` 15 packages.

Ba phát hiện thật trong lúc pin:

- **`orderedset` build fail trên Python 3.12** (`cl` exit 2). Nó chỉ là gia tốc tuỳ chọn của Nuitka → bỏ.
- **`tzdata` bắt buộc trên Windows.** Máy không có hệ thống timezone; `ZoneInfo("Asia/Ho_Chi_Minh")` raise
  khi viết resolver. Repo nguồn pin cùng package vì đúng lý do này; tôi đã bỏ nó ở A6 vì khi đó chưa cần.
  → thêm `tzdata==2026.6; sys_platform == 'win32'`.
- **`PySide6-Essentials` thay metapackage `PySide6`.** Metapackage kéo theo `pyside6-webengine` +
  `pyside6-pdf` (~150 MB). §2.1 loại webview/Electron, và QtQuick nằm trong Essentials. Hệ quả: mặt bằng
  license hẹp hơn, installer 39 MB thay vì ~180 MB. **Cần bạn phủ quyết nếu muốn giữ WebEngine/PDF.**

### 4.2 A5 — license sinh từ deployment thật

`tools/license_manifest.py` đọc metadata đã cài (PEP 639) **và** liệt kê file thật trong `.dist`, không đoán.

| Thành phần | License | Vai trò |
|---|---|---|
| PySide6_Essentials 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | shipped |
| shiboken6 6.12.0 | như trên | shipped |
| santhau-data-studio 0.0.1 | — | shipped (compiled vào exe) |
| Nuitka 4.3.4 | **AGPLv3** + Runtime Library Exception v1.0 | build only |
| pytest / ruff / pytest-qt / zstandard | MIT / MIT+Apache / BSD | build/dev only |

Phát hiện quyết định cho câu hỏi AGPL: **Nuitka ships `LICENSE-RUNTIME.txt`** (Runtime Library Exception
v1.0) cho phép propagate Target Code mà không nhiễm AGPL. Đã đọc văn bản gốc, không suy luận. Nuitka là
build tool, không ship trong product.

Vai trò shipped xác định bằng **đối chiếu file thật của package (đọc RECORD) với deployment**, không bằng
danh sách tự duy trì — nên `colorama`, `iniconfig`, `packaging`, `pluggy`, `Pygments`, `typing_extensions`
(bản transitive của dev tools) đúng ra là **build/dev only**, không phải shipped.

**Caveat:** PySide6 wheel **chỉ ship** `LicenseRef-Qt-Commercial.txt`, không kèm `LICENSE.*` từng module Qt.
Dòng LGPL lấy từ metadata PySide6, **chưa đối chiếu với Qt 6.12 release archive**. Cần legal (O-6).

`--standalone` (không `--onefile`) giữ `qt6*.dll` thành file rời — đúng dạng liên kết LGPL tối thiểu mong
đợi, và cho phép khách thay DLL.

### 4.3 A8 — packaging smoke

| Hạng mục | Trạng thái |
|---|---|
| Cửa sổ Qt Quick/QML render | **PASS** |
| Worker headless riêng, GUI nhận status | **PASS** |
| Nuitka standalone 2 exe | **PASS** — `SanThauDataStudio.exe` 5.619.200 B, `SanThauCollector.exe` 4.980.736 B |
| Stage folder gộp | 1.895 files / ~164 MB |
| Inno Setup installer | **PASS** — compile 68,8 s, 40.807.031 B |
| Cài per-user không admin | **PASS** — `%LocalAppData%\Programs\SanThauDataStudio`, 1.900 file |
| Chạy từ vị trí cài | **PASS** exit 0 |
| Không phụ thuộc môi trường dev | **PASS** — chạy với `PATH=System32` chỉ, `python` không tồn tại; **mọi module đang load nằm trong thư mục cài** |
| Uninstall sạch | **PASS** — "install dir fully removed", registry entry hết, dữ liệu ngoài `{app}` còn nguyên |
| **Cài trên VM Windows sạch** | **NOT_TESTED** |

Bốn lỗi chỉ lộ ra khi chạy bản đóng gói — giá trị chính của spike:

1. **Nuitka không đặt `sys.frozen`** → exe tìm sai đường dẫn QML. Sửa: resolve theo thứ tự đường dẫn.
2. **stdout cp1258 làm crash bản package** khi in tiếng Việt. Đây là **lỗi sản phẩm**, không phải lỗi test:
   mọi log tiếng Việt sẽ sập khi chạy từ shell/Task Scheduler. Sửa: `force_utf8_stdout()`, có test hai
   chiều (bản không fix phải FAIL với đúng `UnicodeEncodeError`).
3. **Screenshot "PASS" nhưng là cửa sổ app khác.** `QScreen.grabWindow(hwnd)` khi window không foreground
   trả nội dung *vùng màn hình* tại toạ độ đó. Hai lần smoke báo PASS kèm ảnh 720×420 hợp lệ — nhưng ảnh là
   UI khác. Đã **vứt 2 ảnh sai** và chụp lại; sửa bằng `raise_()` + `requestActivate()` + guard
   `winId != 0` / `isVisible` / kích thước khớp. Bài học cho G3/G4: smoke screenshot phải chứng minh nó chụp
   đúng window, nếu không nó tạo bằng chứng giả.
4. **Uninstall để lại 38 file khi app đang chạy.** `CloseApplications=yes` không đóng được GUI ở chế độ
   `/SILENT`. Đã kiểm chứng phân biệt: cùng installer, không process → sạch hoàn toàn; có 1 process → 38 file
   còn lại và `Remove-Item` báo "Access … is denied". **Gap cho G4:** app cần tạo **named mutex** để Inno
   phát hiện và đóng.

**Vì sao VM sạch NOT_TESTED:** Windows container chạy Server Core, **không có GUI subsystem** → cửa sổ Qt
không thể render, nên nó không nghiệm thu được tiêu chí này. Docker Desktop trên máy đang ở Linux mode.
Instabox là Linux box. Những gì đã làm thay thế: chạy với PATH tối giản + liệt kê modules của process đang
chạy (chứng minh không phụ thuộc dev env) — nhưng điều đó **không tương đương** máy chưa từng có Qt/MSVC.

---

## 5. Phần C — Hợp đồng dữ liệu G0-B

### 5.1 Quyết định owner đã triển khai

**O-3 Identity.** `natural_key` bỏ phiên bản công bố; `identity_key` là canonical digest có version prefix
`id1:<sha256>` (68 ký tự), JSON array length-tagged, chuỗi NFC-normalised.

Natural key của **6 family đã đối chiếu adapter nguồn**, không đoán — `egp_home.py` khai
`id_fields: tuple[str, str]  # (no_field, version_field)`:

| Family | Dòng | `(no_field, version_field)` | natural_key mới |
|---|---|---|---|
| TBMT | 235 | `notifyNo`, `notifyVersion` | `notifyNo` |
| KQLCNT | 244 | `notifyNo`, `notifyVersion` | `notifyNo` + namespace `family` |
| HỢP ĐỒNG | 265 | `contractCode`, `contractVersion` | `contractCode` |
| KHLCNT | 283 | `planNo`, `planVersion` | `planNo` |
| DỰ ÁN | 300 | `pno`, `pversion` | `pno` |
| YCBG | 315 | `notifyNo`, `notifyVersion` | `notifyNo` + namespace `family` |

> **Phát hiện blocking cho G1.** `egp_home.py:158` và `:341` làm `version = _first(...) or "00"`.
> **Dòng 341 là đường dùng chung cho cả sáu family**, không riêng TBMT. Adapter cũ chữa version thiếu thành
> `"00"` *trước khi* identity kịp thấy — đúng BUG-08 và đúng điều O-3 cấm. Port rule: adapter mới trả
> `(version_state, version_raw)`, không trả chuỗi đã chữa.

**O-4 Unresolved** — ba nhánh, không nhánh nào tạo bản ghi giả:

| Tình huống | RECORD object | Observation | Ghi ở |
|---|---|---|---|
| Trích được, đủ identity | có | `SR:<identity_key>` | `observations` + `source_records` |
| Trích được, thiếu identity | có | `UNRESOLVED:<sha>` | `observations` + `unresolved_records` |
| Không parse được | **không** | **không** | `quarantined_responses` (mức page) |

**O-5 Deep capture** — `deep_capture_history` (bất biến, INSERT-only, `UNIQUE(deep_capture_id, capture_seq)`)
tách khỏi `deep_captures` (latest + `latest_capture_seq` + `completeness` + `stale` + `refresh_due_at_utc`).
`deep_capture_latest` là view JOIN con trỏ xuống history; nếu refresh ghi đè history, view trả rỗng.

### 5.2 Xung đột raw SHA vs hai vai trò (D-0)

§3.10 đặt đồng thời `sha256 PRIMARY KEY` và `object_kind CHECK (IN ('RESPONSE','RECORD'))`. Hai ràng buộc
loại trừ nhau khi một payload đảm hai vai trò — và điều đó **xảy ra thật**: §13.1 ghi `obs.raw = doc`
(`egp_home.py:205,402`), code cũ lưu nguyên trang làm raw của từng record.

Chứng minh bằng chạy: `test_reference_ddl_rejects_the_second_role` dựng DB từ §3.10 **nguyên bản** và nhận
`UNIQUE constraint failed`.

**Giải pháp (owner đã duyệt D-0):** vai trò là thuộc tính **của cạnh**, không của object. `raw_objects` bỏ
`object_kind`; view `raw_object_roles` UNION 18 cạnh để suy lại.

| Phương án bị loại | Vì sao |
|---|---|
| PK composite `(sha256, object_kind)` | cùng bytes → hai file trên đĩa, phá dedup, trái §5.1 |
| Hash domain-separated (`"RECORD\0"+bytes`) | `sha256` không còn là "hash của object SAU redaction" |
| Lưu `object_kind` của vai trò đầu | trạng thái dư, nói dối khi vai trò hai xuất hiện |

### 5.3 Năm sửa bắt buộc (đợt 2)

| # | Yêu cầu | Triển khai |
|---|---|---|
| 1 | Mâu thuẫn SHA dùng chung vs `CHECK(response<>record)` | **Bỏ CHECK**, thay `object_derivation` + trigger. CHECK cũ vừa **quá chặt** (từ chối row đúng khi bytes trùng) vừa **quá lỏng** (không thấy cặp hash khác nhau nhưng chưa từng trích từ nhau) |
| 2 | Test chứng minh RECORD trích từ RESPONSE qua locator | `extract_by_locator()` trích thật (RFC 6901 + `row:<n>`), so digest kết quả trích với `record_object_sha` đã ghi |
| 3 | `redaction_policy_version` đổi không sinh revision nghiệp vụ giả | `raw_revisions.redaction_policy_version` NOT NULL; `REPLAY` (khác policy) tách khỏi `RAW_ONLY_CHANGE` (nguồn đổi); `metric_counts.raw_revisions_count` chỉ đếm `business_raw_revisions` |
| 4 | Rule/proof đã bị tham chiếu không sửa tại chỗ | 4 trigger chặn UPDATE/DELETE; rule chưa bị tham chiếu vẫn sửa được; đường hợp lệ là INSERT `rule_version` mới |
| 5 | Lifecycle `runs` vs `PAUSED/RUNNING` | Tách `runs.lifecycle` (điều khiển §2.3) khỏi `runs.status` (kết quả §3.6, NULL tới khi dừng). `COMPLETED` chỉ khi `FINISHED` |

**D-1**: giữ digest, không thêm `identity_components`. Khoá "tái lập được từ cột canonical" bằng test đọc
4 cột → tính lại digest → đòi khớp, trên 5 tuple gồm cả `MISSING` và notifyNo có dấu tiếng Việt.
**D-2**: `identity_audit_actions` (action đóng ở `CONFIRM_LINK|REJECT_LINK|MERGE|SPLIT|UNDO`, **không có**
`AUTO_MERGE`, `actor_sid` là SID) + 2 trigger chặn `CANDIDATE → EXACT/LINKED` thiếu audit.
**D-3**: `documents`/`lots`/`goods_rows` là schema nền; trường phụ thuộc deep API chưa canary để mở.

### 5.4 Ràng buộc đáng chú ý nhất về mặt kỹ thuật

**SQLite cấm subquery trong CHECK** (`subqueries prohibited in CHECK constraints` — đo được). Ràng buộc
"proof phải viện dẫn rule đã canary" vì thế nằm ở **trigger**, vẫn DB-enforced mà không phải nhân bản
`canary_verified` sang bảng proof.

### 5.5 Ba lỗi tôi mắc, bị chính công cụ của mình bắt

Ghi lại vì đây là bài học, không phải thanh minh:

1. **Off-by-one làm mọi INSERT fail.** DDL ghi `length(identity_key) = 67`, encoder sinh 68. Không đọc ra
   được; chỉ test đối chiếu Python↔DDL mới bắt được.
2. **View đặt tên `orphan_object_files` nhưng trả row mồ côi.** Tên sai dẫn tới "tưởng đã quét GC xong".
   Hai loại mồ côi **không đối xứng**: row mồ côi SQL thấy được (view `orphan_object_rows`), file mồ côi thì
   không — SQL chỉ thấy row, nên loại đó thuộc bước quét filesystem §5.2.
3. **Checker ban đầu mã hoá *vị trí* spec đặt cột, không phải *hợp đồng* phải thỏa.** Khi DDL G0-B chuyển
   nghĩa vụ sang chỗ khác (boundary ra bảng rule, orphan sang view, unresolved sang bảng quarantine), nó
   báo đó là **regression**. Đã viết lại placement-agnostic. Đây là lỗi nghiêm trọng nhất vì nó đánh dấu đúng
   vào thứ tiến bộ.

---

## 6. Test đã chạy — ánh xạ yêu cầu → kết quả

71 contract test, SQLite `:memory:` + `PRAGMA foreign_keys=ON`, 0.45 s. Không test nào assert "DDL thiếu
cột"; phần lớn assert bằng cách **thử ghi cái sai và đòi DB từ chối**.

| Yêu cầu của owner | Test | Kết quả |
|---|---|---|
| Không collision identity với NULL, Unicode, delimiter | 6 test: `test_null_version_is_not_the_string_none_nor_zero_zero`, `test_delimiter_in_source_value_cannot_shift_the_boundary`, `test_delimiter_in_family_and_state_cannot_collide_either`, `test_unicode_nfc_and_nfd_forms_are_one_identity`, `test_notice_group_key_is_immune_to_delimiter_shift`, `test_missing_rejects_a_raw_value_instead_of_silently_ignoring_it` | PASS |
| Không merge sai hai bản công bố | `test_notify_version_00_and_01_are_two_records_one_notice_group` (DEDUP-16) | PASS — 2 record, 1 group, `COUNT(DISTINCT identity_key)=2` |
| Không tạo observation cho response không parse được | `test_unparseable_response_produces_no_record_and_no_observation`, `test_response_hash_cannot_be_recorded_as_a_record_without_derivation` | PASS |
| Page 10 record, chỉ 1 đổi → chỉ record đó có raw revision mới | `test_page_of_ten_records_only_one_changed` | PASS — 9 record giữ nguyên hash & `content_version=1`; #10 có revision 2 |
| Deep capture refresh không mất lịch sử | `test_deep_capture_refresh_appends_and_keeps_history`, `test_latest_pointer_must_reference_an_existing_capture` | PASS — history 2 row, latest trỏ seq 2 |
| Coverage resolver union đúng, không COMPLETE giả | 11 test: `test_union_merges_touching_and_overlapping`, `test_subtract_produces_exact_gaps`, `test_three_hours_of_a_day_is_not_a_complete_day` (COV-13), `test_hourly_split_covering_the_whole_day_is_complete` (COV-14), `test_newer_partial_does_not_mask_verified_complete` (COV-05), `test_legacy_complete_without_proof_is_never_skipped` (COV-06), `test_leap_day_window_unions_correctly` (COV-11), `test_historical_access_unsupported_is_its_own_status` (COV-12) | PASS |
| Transaction, FK, CHECK, UNIQUE hoạt động trên SQLite thật | `test_transaction_rolls_back_wholly_on_midway_failure`, `test_interval_before_proof_is_refused_by_fk`, `test_complete_interval_requires_a_proof`, `test_completed_never_enters_interval_status`, `test_run_lifecycle_and_outcome_are_separate_axes`, `test_unique_constraint_blocks_duplicate_observation`, `test_blocked_reason_is_recordable_without_becoming_a_token_leak` | PASS |

Test thêm: `test_content_unchanged_but_raw_changed_is_revision_only` (DEDUP-09 qua đường ingest thật),
`test_abab_keeps_three_content_versions`, `test_recovered_retry_is_allowed_and_counted` (COV-16),
`test_unknown_amount_is_null_not_zero` / `test_quantity_unknown_is_not_zero` (U-2),
`test_receipt_steps_are_monotonic` (§2.3), `test_redaction_policy_change_does_not_invent_a_business_revision`,
`test_boundary_rule_cited_by_a_proof_is_immutable`, `test_ambiguous_candidate_cannot_self_promote`,
`test_identity_digest_is_recomputable_from_stored_columns`.

**Test đã làm thay đổi code** (không xanh để trang trí): 7 test fail trước và buộc sửa thật — off-by-one
67/68, view sai tên cột `sha256`, view sai bản chất file/row orphan, helper bỏ qua `normalized` của caller,
3 test schema-shape.

---

## 7. DANH SÁCH ĐIỂM CHƯA KIỂM CHỨNG

Không tô xanh. Sắp theo mức độ rủi ro nếu bỏ qua.

| # | Chưa verify | Vì sao | Rủi ro |
|---|---|---|---|
| **V-1** | **Toàn bộ contract test chạy qua helper, không qua writer thật** | `ingest_record()` là transcription của §3.5 do chính tôi viết. Test xanh chứng minh **schema cho phép** bảng quyết định, chưa chứng minh engine làm đúng | G1 phải chạy lại 71 test trên writer thật; writer sai thì test hiện không biết |
| **V-2** | **`object_derivation` mới chứng minh là CẦN, chưa chứng minh là ĐÚNG** | Trigger chặn observation thiếu derivation row, nhưng không gì kiểm tra row đó phản ánh trích xuất thật. `extract_by_locator` chạy trong test, không chạy trong writer | Writer ghi derivation row bừa được. G1 phải buộc derivation sinh từ extraction thật |
| **V-3** | **`filter_boundary_rules` chứa giá trị GIẢ ĐỊNH** (`CLOSED_LOCAL`, offset 420, `conversion_params`) | Chưa canary VNEPS; §13.2 vẫn để ngỏ | Trigger đã chặn `COMPLETE` khi `canary_verified=0` nên chưa claim coverage sai được — nhưng quy tắc chưa đúng |
| V-4 | Natural key detail/HSMT/Reoffer chưa đối chiếu | Script ở `analysis/crawl_*`, đang QUARANTINE (owner xác nhận D-4) | Port deep lane không verify = lặp BUG-05 |
| V-5 | Hiệu năng 1M records (§7.4) chưa đo | Chưa có dữ liệu lớn. 54 FK + 8 trigger + 6 view có thể có giá | `raw_object_roles` UNION 18 cạnh và `orphan_object_rows` có thể chậm ở scale; UI-09 không nên gọi đồng bộ |
| V-6 | Đường chuyển đổi `redaction_policy_version` thật chưa có | Chưa có nhiều policy để migrate object cũ | Test #3 dùng policy 1→2 tự tạo, chưa phải kịch bản thật |
| V-7 | Trigger promotion chưa test 2 tiến trình / transaction thật | Chỉ test một kết nối `:memory:` | Race worker/GUI ở G2 có thể cho phép promote không audit |
| V-8 | **A1 token dashboard chưa thu hồi** | Không có quyền InstaBox / GitHub / secret store | Tunnel host vẫn resolve; chưa khai thác được nhưng **không tự hết** |
| V-9 | **A8 VM Windows sạch NOT_TESTED** | Windows container không GUI subsystem; máy này sẵn Python/Git/MSVC | Không kết luận được hành vi trên máy khách chưa có gì |
| V-10 | Qt per-module license files chưa đối chiếu | PySide6 wheel chỉ ship `LicenseRef-Qt-Commercial.txt` | Thuộc legal / G4 (O-6) |

### Phạm vi tôi đã vượt — khai báo tường minh

Đề bài nói chưa viết code sản phẩm ở G0-B, nhưng cũng đòi "test bất biến thực tế" và "resolver tính đúng
union". Không thể có cả hai nếu không có code để gọi. Tôi viết **hai primitive hợp đồng** thuần (không I/O,
không DB): `domain/identity.py` và `domain/coverage.py`. `tests/contract/helpers.py` chứa `ingest_record()`
— bản chép trung thực bảng quyết định §3.5, **chỉ để test**. Nếu bạn muốn chúng nằm ngoài `src/` tới G1,
một câu là tôi dời sang `contracts/`.

---

## 8. Cần bạn quyết, và G1 handoff

### 8.1 Còn chờ owner

| ID | Việc | Chặn |
|---|---|---|
| **O-1** | Hạ route tunnel + xoay token dashboard, lưu token mới trong secret store | A1 |
| **O-2** | Chọn A (revoke, không rewrite) hay B (GitHub purge) cho commit `6a6fcb55` | A2 exit |
| **O-6** | Legal: LGPL vs commercial Qt | A5 / G4 |
| **O-7** | Provenance của `egp_home_type_samples.json` (không README, chứa shape thật) | A3 |
| **O-8** | Cấp VM/Windows host sạch, hoặc chuyển tiêu chí sang G4 | A8 |
| **O-9** | `moh_quotes.py` / `mof_quotes.py` có **hai bản** trong repo nguồn (harvester + `server/app/radar/adapters/`); spec không chỉ rõ bản nào | port map |
| O-10 | Duyệt `PySide6-Essentials` thay metapackage (bỏ WebEngine/PDF ~150 MB) | A5/A8 |
| O-11 | 4 file `examples/*.md` chứa dữ liệu thật; sanitizer hiện chỉ nhận JSON → cần chế độ text | G1 |
| O-12 | `tests/test_serving.py`, `test_home_types.py` chứa dữ liệu thật trong code → port phải qua sanitizer | G1 |

### 8.2 G1: READY với 4 điều kiện

Không chặn bắt đầu port, **chặn exit**:

1. Adapter **không** được `or "00"` (`egp_home.py:158,341`); regression test phải fail trên code cũ.
2. Writer thật phải chạy xanh **71 contract test**, không viết lại test.
3. `object_derivation` phải sinh từ extraction thật trong writer (V-2).
4. Boundary rule phải được canary trước khi bất kỳ interval nào nhận `COMPLETE` (V-3).

**NO-GO cho:** freeze DDL (cần O-6/O-9 ngữ cảnh pháp lý và phạm vi), live canary, claim coverage.

### 8.3 Thứ tự port đã duyệt, giữ nguyên

1. Port 6 adapter nguồn, sửa triệt để việc tự gán version thiếu thành `"00"`.
2. Triển khai writer thực trên SQLite + raw object store.
3. Chạy lại contract tests **qua writer thực**, không chỉ helper.
4. Triển khai BACKFILL / INCREMENTAL / RECONCILE, checkpoint, coverage resolver.
5. Fault injection (§10.3) + benchmark raw storage (§5.1).
6. **Báo cáo trước khi bật live canary.**

---

## 9. File nào chứa gì

Báo cáo này là bản tổng hợp. Đào sâu hơn:

| File | Nội dung |
|---|---|
| `docs/DATA_CONTRACT.md` | Hợp đồng dữ liệu đầy đủ: identity, unresolved, raw store, resolver, boundary, deep capture, money, lifecycle, audit |
| `docs/ddl/g0_b_proposed.sql` | **DDL cuối cùng** để freeze — 41 bảng, 6 view, 8 trigger |
| `docs/ddl/v1_3_reference.sql` | **BASELINE** §3.10 trước điều chỉnh (không còn khớp spec hiện hành) |
| `tests/contract/test_g0_b_invariants.py` | 71 contract test trên SQLite thật |
| `tools/ddl_contract_check.py` | 43 check hợp đồng, chạy được trên mọi DDL |
| `tools/sanitizer.py` · `tools/secret_scan.py` · `tools/license_manifest.py` | công cụ A4 / A7 / A5 |
| `GATE-G0-A/` | 5 báo cáo G0-A + evidence (history audit redacted, real-data inventory, 5 verify result) |
| `GATE-G0-B/` | closure + final fixes + 2 bảng check baseline/final |
| `CHANGELOG.md` | mọi thay đổi hợp đồng, hai đợt |
| `_evidence/` (ngoài repo) | log chạy thật: probe tunnel, nuitka build 1–5, smoke screenshot |

---

## 10. Cách tái lập

```powershell
cd D:\Harvest\santhau-data-studio
uv sync --all-groups

# DDL thực thi, FK bật thật
python -c "import sqlite3,pathlib;c=sqlite3.connect(':memory:');c.execute('PRAGMA foreign_keys=ON');c.executescript(pathlib.Path('docs/ddl/g0_b_proposed.sql').read_text(encoding='utf-8'));print(c.execute('PRAGMA integrity_check').fetchone()[0])"

# Hợp đồng: baseline vs final
python tools/ddl_contract_check.py --sql docs/ddl/v1_3_reference.sql   # PASS 12 / GAP 8 / FAIL 23
python tools/ddl_contract_check.py --sql docs/ddl/g0_b_proposed.sql    # PASS 43 / GAP 0 / FAIL 0

# Test
python -m pytest tests/contract -o addopts='' -q   # 71 passed
python -m pytest -o addopts=''                     # 135 passed
python -m ruff check src tools tests               # All checks passed

# Packaging
powershell -ExecutionPolicy Bypass -File packaging\nuitka\build_standalone.ps1
& 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' /DStageDir=...\build\stage packaging\inno\santhau-data-studio.iss
```

---

**DỪNG SAU G0-B.** Không tự mở G1, không push/PR/deploy, không chạy canary VNEPS.
Repo local, không remote, 62 file **staged nhưng chưa commit** — chờ bạn duyệt.
