# G0-A-REPORT — Săn Thầu Data Studio

**Gate:** G0-A — Security & Source Quarantine (A1–A8) · **Ngày:** 03/11/2026
**Spec:** Master Spec Consolidated **v1.3** ngày 03/11/2026 (đọc toàn bộ 916 dòng trước khi làm)
**Nguồn port được phép:** `Gia-Su-AI/DauThauAI` @ `codex/radar-harvester` = `aa65f0a6edcb98877d705c8495ca00c79303f86e` — **SHA khớp chính xác** với spec.
**Repo nguồn:** không sửa đổi. Toàn bộ thao tác là `git show` / `git ls-tree` / `git log` read-only; không checkout, không merge, không push, không PR, không tạo remote.

---

## 1. Bảng trạng thái gate

| # | Việc | Trạng thái | Bằng chứng chính |
|---|---|---|---|
| A1 | Thu hồi token đã lộ | **BLOCKED** (xác minh: PASS) | `SECURITY-AUDIT.md` §1; fingerprint 3 đường code độc lập khớp nhau; probe tunnel → HTTP 502 |
| A2 | Rà git history | **PASS** (chờ chữ ký owner cho quyết định) | `history-audit-redacted.json` — 3 pass: pickaxe / tree-grep / object-sweep |
| A3 | Cách ly dữ liệu thật | **PASS** | `source-real-data-inventory.json` — 14 file có marker danh tính; README tự khai "REAL captured payload" |
| A4 | Sanitizer | **PASS** | 27 test; 5/5 capture thật → synthetic, `identity_leaks: 0` |
| A5 | License | **PASS** (2 quyết định chờ owner/legal) | `LICENSE-MANIFEST.md` + `THIRD_PARTY_NOTICES.txt` + `LICENSES/` — 180/180 native file phân loại, 0 unclassified |
| A6 | Pin dependency | **PASS** | `.python-version` = 3.12.14; `uv.lock` 14 packages, `requires-python = "==3.12.*"` |
| A7 | Pre-commit secret scan | **PASS** | 9 test end-to-end qua `git commit` thật; gate chạy trên repo này: 35 file, 0 finding |
| A8 | Packaging smoke | **PASS có điều kiện** | `PACKAGING-SMOKE-RESULT.md` — installer 39 MB, cài + chạy + gỡ sạch; **VM Windows sạch = NOT_TESTED** |
| G0-B | Readiness | **Sẵn sàng có điều kiện** | `G0-B-READINESS.md` — tally PASS 12 / GAP 8 / **FAIL 5**; **B6 chưa đóng được** |

**Không dòng nào báo PASS dựa trên code review.** Mỗi trạng thái gắn với một lệnh đã chạy và log đã lưu.

## 2. Ba phát hiện đổi cách hiểu spec

### 2.1 Token đã lộ **không phải** credential VNEPS

`serve_api.py` cho thấy `?t=` là API token của **bảng tiến độ harvester**, guard một server đọc-only
trên kho của chính máy đó, expose ra internet qua InstaBox tunnel. `HANDOFF_WEB_DISPLAY.md:127` là
lộ credential của dashboard, không phải lộ danh tính thầu của bạn với nguồn.

Vì sao quan trọng: nó đổi **cách thu hồi** (restart service + đổi token, không phải đổi tài khoản),
đổi **blast radius**, và đổi **thứ tự ưu tiên**. Nó cũng giải thích vì sao `Access-Control-Allow-Origin: *`
(`serve_api.py:253`) đi kèm làm sự cố nghiêm trọng hơn: trang web bất kỳ gọi được API nếu có token.

### 2.2 Secret **đã nằm trên GitHub**

Commit `6a6fcb5551b1` (02/11/2026 02:51:33 +07) có mặt trong tree của **`origin/codex/radar-harvester`**.
Nó **không** phải tổ tiên của `origin/main`.

Hệ quả: rewrite history local **không** rút secret ra khỏi GitHub. Biện pháp có hiệu lực là thu hồi
credential — làm giá trị trong history trở nên vô ích. Object sweep trên toàn bộ 28.234 objects tìm
thấy **đúng 1 blob** chứa pattern → loại được giả thuyết "còn bản ẩn do amend/force-push".

### 2.3 Dữ liệu thật nằm trong **code test**, không chỉ trong fixture

`tests/test_serving.py` (24 marker) và `tests/test_home_types.py` (8 marker) nhúng dữ liệu đấu thầu
thật **ngay trong source code test**. Spec §1.1 chỉ cảnh báo fixture. Port 2 file này mà không sanitize
sẽ mang dữ liệu thật vào repo mới dưới dạng code — nơi secret-scan **không bắt được**, vì nó tìm
credential chứ không tìm PII.

## 4. Những gì đã xây

```
santhau-data-studio/
├── pyproject.toml  uv.lock  .python-version  .gitignore  .gitattributes
├── src/santhau_studio/
│   ├── app_main.py            # QML viewer + smoke mode (--smoke)
│   ├── worker_main.py         # headless fake worker, receipt vocabulary §2.3
│   └── desktop/qml/Main.qml
├── tools/
│   ├── secret_scan.py              # gate/audit 2 mode, output luôn masked
│   ├── pre_commit_secret_gate.py   # scan STAGED BLOB, không scan worktree
│   ├── hooks/pre-commit            # qua core.hooksPath
│   ├── git_history_secret_audit.py # 3 pass history
│   ├── real_marker_scan.py         # kiểm kê dữ liệu thật
│   ├── real_data_audit.py          # phân loại field theo shape
│   ├── sanitizer.py                # real → synthetic + verifier
│   ├── license_manifest.py         # sinh từ deployment thật
│   └── ddl_contract_check.py       # G0-B: 25 kiểm tra DDL
├── tests/unit/                       # 65 test
├── tests/fixtures/synthetic/         # 5 fixture tổng hợp
├── docs/ddl/v1_3_reference.sql       # §3.10 trích nguyên văn
├── packaging/nuitka/build_standalone.ps1
├── packaging/inno/santhau-data-studio.iss
├── LICENSE-MANIFEST.md  THIRD_PARTY_NOTICES.txt  LICENSES/
└── GATE-G0-A/                        # bộ báo cáo + evidence
```

## 5. Test đã chạy (số thật)

| Lệnh | Kết quả |
|---|---|
| `pytest -q` (toàn bộ) | **65 passed** |
| `pytest tests/unit/test_sanitizer.py` | 27 passed |
| `pytest tests/unit/test_secret_gate.py` | 9 passed |
| `pytest tests/unit/test_ddl_contract.py` | 25 passed |
| `pytest tests/unit/test_console_encoding.py` | 2 passed (1 là negative control) |
| `ruff check src tools tests` | **All checks passed** |
| `secret_scan --mode gate .` (repo mới) | **0 finding** |
| `secret_scan --mode audit` trên `tools/radar-harvester` @ `aa65f0a` | 1 finding = đúng lộ A1 |
| `sanitizer verify` × 5 capture thật | 5 × `verdict: PASS`, `identity_leaks: 0` |
| `ddl_contract_check` | PASS 12 / GAP 8 / FAIL 5 |
| Nuitka build | `BUILD_OK` + `ASSEMBLE_OK`, exit 0 |
| `iscc` | `Successful compile (68.844 sec)` |
| Cài → chạy → gỡ | exit 0 cả ba; "install dir fully removed" |

Test nào cũng có mục tiêu. Tôi **không** chạy suite 117 test của repo nguồn: spec §13.1 ghi 47/117 test
canh code sẽ bị xoá, và G0-A không port engine → chạy nó không cho tín hiệu gì.

## 6. Việc cần Founder / owner

| ID | Việc | Gate bị chặn |
|---|---|---|
| **O-1** | Hạ route tunnel + xoay token dashboard, lưu token mới trong secret store | A1 |
| **O-2** | Ký quyết định: (A) revoke-credential-không-rewrite hay (B) GitHub history purge cho `6a6fcb55` | A2 exit |
| **O-3** | Chốt `identity_key`: C-3 (natural_key có chứa notifyVersion hay không) + C-7 (NULL encoding) + C-8 (escape `\|`) | G0-B — **không đảo ngược được** |
| **O-4** | Chốt C-4: member không parse được → RECORD object hay `record_object_sha NULL` | G0-B |
| **O-5** | Duyệt thêm bảng `deep_captures` vào §3.10 (C-1) | G0-B / B6 |
| **O-6** | Quyết định LGPL vs commercial Qt cho phân phối thương mại (spec §13.4 đã đặt ngoài quyền coder) | A5 |
| **O-7** | Cho biết provenance của `egp_home_type_samples.json` (không có README, chứa shape thật) | A3 |
| **O-8** | Cấp VM/Windows host sạch (hoặc chuyển tiêu chí sang G4) | A8 |
| **O-9** | Chốt P-1: `moh_quotes.py`/`mof_quotes.py` có **hai bản** trong repo nguồn, spec không chỉ rõ bản nào | port map |

## 7. Ba quyết định kỹ thuật tôi đã nhân danh coder, cần bạn phủ quyết nếu không đồng ý

1. **Dùng `PySide6-Essentials` thay metapackage `PySide6`.** Metapackage kéo theo `pyside6-webengine`
   + `pyside6-pdf` (~150 MB). §2.1 loại webview/Electron, và QtQuick nằm trong Essentials. Hệ quả:
   mặt bằng license hẹp hơn và installer 39 MB thay vì ~180 MB. Nếu bạn cần PDF/WebEngine sau này thì
   phải thêm lại.
2. **Nuitka là AGPLv3 nhưng có `LICENSE-RUNTIME.txt` (Runtime Library Exception v1.0)** cho phép
   propagate Target Code mà không nhiễm AGPL. Đã đọc văn bản gốc, không suy luận. Nuitka là build tool,
   không ship trong product. Vẫn nên cho legal xem một lượt.
3. **`orderedset` bị bỏ khỏi dependency group `build`.** Nó là C extension cũ, **build fail trên
   Python 3.12** (`cl` exit 2 — đã gặp thật). Với Nuitka nó chỉ là gia tốc tuỳ chọn. Không ảnh hưởng kết quả.

## 8. Cái tôi **chưa** verify (nêu rõ, không tô xanh)

- **Token cũ còn sống hay đã chết**: không kết luận được. Origin trả 502 nên 404/401 và "server chết"
  không phân biệt được từ ngoài. Chỉ kết luận được: DNS còn resolve, TLS còn bắt tay, **chưa vô hiệu hoá**.
- **VM Windows sạch**: NOT_TESTED. Lý do kỹ thuật ở `PACKAGING-SMOKE-RESULT.md` §5 — Windows container
  chạy Server Core **không có GUI subsystem**, nên nó không nghiệm thu được "cửa sổ hiện lên".
- **Qt per-module license files**: PySide6 wheel **chỉ ship** `LicenseRef-Qt-Commercial.txt`, không kèm
  `LICENSE.*` của từng module Qt. Dòng LGPL trong manifest lấy từ metadata PySide6, **chưa đối chiếu
  với Qt 6.12 release archive**. Ghi rõ trong LICENSE-MANIFEST.md §3.
- **Inno `CloseApplications` với app có mutex**: chưa test vì spike chưa tạo mutex. Đã chứng minh
  *hành vi* (có process → 38 file lại; không process → sạch), còn *giải pháp* thuộc G2/G4.
- **Nghiệm thu installer đầy đủ**: upgrade path, uninstall giữ **data root thật**, Authenticode — thuộc
  G4 theo §9, không làm ở đây.
- **`examples/*.md` (4 file chứa dữ liệu thật)**: sanitizer hiện chỉ nhận JSON. Chưa có đường an toàn
  cho Markdown → ghi P-2, xử lý ở G1.

## 9. DỪNG

Theo chỉ thị §5 của đề bài: **dừng sau G0-A**. Không bắt đầu G1, không viết migration production, không
port engine, không tạo PR, không push. G0-B readiness đã giao ở `GATE-G0-A/G0-B-READINESS.md` với 3 câu
hỏi cần chốt (O-3, O-4, O-5) trước khi đóng băng DDL.
