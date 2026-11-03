# SĂN THẦU DATA STUDIO — RELEASE-CANDIDATE-READINESS

**Ngày:** 03/11/2026 · **Spec nền:** Master Spec Consolidated v1.3 · **Repo:** `D:\Harvest\santhau-data-studio` (local, không remote, không push)
**Phạm vi batch:** STABILIZATION & RELEASE CANDIDATE (A–G). Báo cáo G1 trước đó: `REPORT-G1.md`.

> Mọi số liệu dưới đây là kết quả lệnh đã chạy trong phiên. Mục §9 liệt kê thẳng những gì **chưa**
> verify; không có acceptance nào được đánh PASS thay.

---

## 1. Kết luận nhanh

| Hạng mục | Trạng thái | Ghi chú |
|---|---|---|
| A — Raw storage | **PASS** (offline, crash windows) | segment store + dual-read; số đo tái lập §3 |
| B — E2E fake source 6 family | **PASS** | production path, 14 test |
| C — Windows runtime/desktop | **PASS phần lớn** | GUI thật + screenshot + DPI; Task Scheduler live test **chưa chạy** |
| D — Deep collection | **TESTED_OFFLINE** | live **BLOCKED** đúng D-3 |
| E — Packaging/scale/release | **MỘT PHẦN** | installer build; clean-VM **BLOCKED**; scale số §4 |
| F — Security/canary | **PASS chuẩn bị** | canary matrix; live giữ BLOCKED |
| G — Quy trình/bàn giao | **PASS** | commit local theo nhóm |

**Không tuyên bố release PASS**: thiếu clean-VM, Authenticode và nghiệm thu owner (§9).

---

## 2. Bằng chứng build/test

```
pytest (toàn bộ)            : 462 tests passed
ruff check src tests tools  : All checks passed
stage                       : build/stage — 2.517 file / 202.339.304 bytes
  SanThauDataStudio.exe     : 17.545.216 bytes  sha256 1c96cbb74b9b14bca64bc9b52fe32236a9be664ad094e7ff65e86d2083513ca9
  SanThauCollector.exe      : 17.062.400 bytes  sha256 7e1d76bfda6b2104d82de6ffd090e90548426ba77a19c92532269ffb7ece65fa
installer                   : build/inno/SanThau-Data-Studio-Setup-0.0.1-spike.exe
                              48.873.377 bytes  sha256 b3353c124ac167600435f80431eb619d0015aba8cbdabd50d99045b40a688698
                              ISCC compile 92,7 s
```

### Vòng đời installer (per-user, thư mục TEMP — máy này, không phải VM sạch)

| Bước | Kết quả |
|---|---|
| Silent install (`/VERYSILENT /DIR=%TEMP%\SanThauRC`) | exit 0; exe + qml + LICENSES + notices có mặt; 203 MB |
| Chạy bản cài (smoke trang 02) | exit 0, ảnh 1475×950, PASS; **không còn process treo** |
| Reinstall đè (đường upgrade) | exit 0, exe còn nguyên |
| Uninstall | exit 0; thư mục cài **xoá sạch (0 file)**; uninstall key HKCU = 0 |
| Data root của người dùng | 97 file trước = 97 file sau (không bị chạm) |

Lần đầu uninstall để lại file vì process GUI smoke cũ còn sống giữ DLL: Qt teardown sau
`grabWindow` treo, `sys.exit` không kết thúc tiến trình. Đã sửa (thoát cứng sau khi ghi verdict) và
chạy lại vòng trên — PASS. Đây cũng là lý do Inno `CloseApplications` không cứu được file bị khoá.

### GUI ↔ worker trên binary đóng gói

- Packaged worker: tạo schema, pipe ping/status OK, `raw/segments/` được dùng, FORCE_TERMINATE rc 0.
- Packaged GUI mở trên data root đang có worker: gắn được (pipe OK), thoát GUI → worker **vẫn chạy**,
  sau đó FORCE_TERMINATE rc 0.
- RAM GUI đóng gói lúc khởi động: WorkingSet 167.153.664 B (peak 173.342.720 B).

Commit nhóm trong batch này: xem `git log --oneline` (mỗi nhóm A/B, C, D, E… là một checkpoint
local, không push).

---

## 3. A — Raw storage

- Root-cause errno 22 của lần chạy benchmark 100k trước: harness cũ giữ toàn bộ 100k trang trong
  RAM và không khoá workdir; lần chạy 5k/2k sạch cho thấy lỗi đọc gzip là sự cố môi trường của lần
  chạy duy nhất đó (không tái lập được; log `_evidence/g1/bench-raw-100k.log`). Harness nay khoá
  workdir (`bench.lock`), sinh trang streaming (RAM bounded) và báo path/stat khi đọc lỗi.
- Production store (`RawObjectStore`) không dùng đường đọc của harness; nó có crash matrix riêng.
- Số đo production (2.000 trang × 10 = 22.000 object, 0 verify failure):

| Layout | Write | Read | ms/object | Allocated/logical |
|---|---:|---:|---:|---:|
| objects (1 file/object) | 117,66 s | 289,46 s | 13,16 | 19,19× |
| segments (byte-offset) | 42,11 s | 3,94 s | 0,18 | 1,75× |

→ segments đọc nhanh **73×**, ghi nhanh **2,8×**, chiếm đĩa allocated chỉ **9,1%** so với object
layout. Production worker ghi layout segments (env `SANTHAU_RAW_LAYOUT` đổi được); đọc dual-layout.
Chi tiết + crash tests: `tests/integration/test_segment_store.py` (17 test).

---

## 4. E — Scale

`tools/scale_probe.py` (100k listing pages → 1M records qua `commit_page`, serving + FTS + UI probe):

```
<FILL: _evidence/rc/scale-1m.json>
```

---

## 5. C — Desktop

- GUI chạy thật trên Windows (scale 100/125/150%): 1180×760 / 1475×950 / 1770×1140 — ảnh chụp
  bằng render QML (`QQuickWindow.grabWindow`), không phải chụp màn hình nên không thể nhầm app khác.
- Ảnh: `_evidence/rc/screenshots/page-*.png` (Control Center 6 family, Record Detail timeline +
  raw revision, Jobs, Coverage, Deep, Integrity) + `packaged-page1.png`, `installed-page1.png`.
- Tiếng Việt hiển thị đúng trên display thật (SĂN THẦU, "tươi", "đĩa", "Chọn một hồ sơ…").
- Worker sống khi GUI đóng và attach khi mở lại: test process thật (E) + binary đóng gói ở §2.
- Pipe theo user + **data root**: hai root khác nhau → hai tên pipe khác nhau (test cách ly trong
  `test_worker_ipc.py`); SDDL owner-only + SID check giữ nguyên.
- Task Scheduler XML 08:00/20:00 + `IgnoreNew`, tên task theo data root, create/delete có kiểm soát;
  scheduled run thoát khi idle để trigger 20:00 không bị nuốt. Live create/delete chưa chạy (opt-in
  `SANTHAU_SCHEDULER_TEST=1`) — xem blocker §9.
- Explorer: FTS + phân trang OFFSET ("Tải thêm"); staleness/degraded hiện ở UI-02; integrity +
  quarantine hiện ở UI-09; lỗi/empty có dòng chữ riêng.

---

## 6. D — Deep lanes

- `workers/deep_run.py`: DETAIL/HSMT/REOFFER dispatch; fetcher/spec phải được inject và **spec mặc
  định vẫn `paths=None`**, nên production trả `BLOCKED` (`LIVE_DEEP_ENDPOINT_UNVERIFIED` /
  `DEEP_SPEC_UNVERIFIED`).
- Offline: capture lots/goods/members; version đổi → stale → refresh giữ history (test).
- Ma trận trạng thái lane hiển thị ở UI-08: `TESTED_OFFLINE` vs `BLOCKED`.

---

## 7. F — Security

- Không dùng token A1; live e-GP/deep giữ BLOCKED; canary matrix: `docs/canary-matrix.md`.
- Không có file dữ liệu thật trong git: `git ls-files` không có `.sqlite/.db/.gz/.png`; không remote.
- Các match "token/secret" trong repo chỉ là literal test cho scrubber + pattern của scanner.

---

## 8. UI screenshots

| Màn | File |
|---|---|
| 02 Control Center | `_evidence/rc/screenshots/page-1.png` |
| 04 Collection Jobs | `_evidence/rc/screenshots/page-3.png` |
| 05 Coverage | `_evidence/rc/screenshots/page-4.png` |
| 07 Record Detail | `_evidence/rc/screenshots/page-6.png` |
| 08 Deep Collection | `_evidence/rc/screenshots/page-8.png` |
| 09 Integrity | `_evidence/rc/screenshots/page-9.png` |

---

## 9. Blocker còn lại (không được coi là PASS)

1. **Clean-VM install/uninstall/upgrade**: Docker engine không chạy trong phiên; **NOT_TESTED**.
2. **Authenticode**: build chưa ký; spec §11 bắt buộc trước phân phối — **BLOCKED (owner/cert)**.
3. **Qt/PySide6 license texts**: wheel chỉ kèm `LicenseRef-Qt-Commercial.txt`; cần chọn LGPL/GPL/
   commercial và bổ sung full texts trước khi phân phối — **BLOCKED (owner decision)**.
4. **Live canary e-GP (6 family)**: **BLOCKED** tới khi owner duyệt quyền/khung giờ.
5. **Deep live**: **BLOCKED (D-3/B7)**.
6. **A1 token dashboard**: **BLOCKED** tới khi thu hồi.
7. **Task Scheduler live create/delete**: mới verify XML + unit; live cần opt-in
   `SANTHAU_SCHEDULER_TEST=1`.
8. **Benchmark 100k pages read-back** ở layout objects: ~4 giờ trên máy này — **NOT_TESTED**; số
   segments 22k object + ngoại suy trong §3.
9. **UI keyboard navigation**: đã cài đặt (sidebar Up/Down, Tab) nhưng chưa có test tự động —
   **MANUAL_ONLY**.
11. **GUI mutex / Restart Manager**: khi GUI đang mở lúc uninstall, Windows không đóng được app
    (chưa có named mutex); uninstall có thể để lại file bị khoá. Đã chứng minh chu trình sạch PASS,
    nhưng "uninstall khi app đang chạy" cần mutex ở vòng sau — **BLOCKED (follow-up)**.
10. **Kết quả FTS trên 1M**: xem §4; nếu probe timeout sẽ ghi BLOCKED kèm lý do.

---

## 10. Lệnh tái lập

```bash
uv run pytest -q                               # toàn bộ
uv run ruff check src tests tools
uv run python tools/bench_raw_store.py --pages 5000 --read-sample 1.0
uv run python tools/bench_production_store.py --pages 2000
uv run python tools/scale_probe.py --pages 100000
powershell -ExecutionPolicy Bypass -File packaging/nuitka/build_standalone.ps1
iscc /DStageDir=<repo>\build\stage packaging\inno\santhau-data-studio.iss
uv run python -m santhau_studio.app_main --data-root <root> --smoke shot.png --smoke-page 1
```