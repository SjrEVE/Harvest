# SĂN THẦU DATA STUDIO — BÁO CÁO TỔNG HỢP G1 (A → G)

**Ngày:** 03/11/2026 · **Spec nền:** Master Spec Consolidated **v1.3**
**Workspace:** `D:\Harvest\santhau-data-studio` · git local, **9 checkpoint commit, không push, không remote, không PR**
**Repo nguồn `Gia-Su-AI/DauThauAI`: read-only trong toàn bộ batch** — không sửa, không copy mã.
File này là báo cáo hợp nhất; tên `G1-1-STORAGE-INGEST-REPORT.md` trong lệnh giao tương ứng mục A dưới đây.

---

## 1. Kết luận theo milestone

| Milestone | Trạng thái | Bằng chứng chính |
|---|---|---|
| **A — Storage & ingest (G1.1)** | **PASS** (offline, qua writer thật) | `ed6f103` |
| **B — Collectors / HTTP lane** | **PASS** (offline; lane live **BLOCKED** B7) | `31ff787` |
| **C — Runner + coverage/proof** | **PASS** | `c4bb00d` |
| **D — Deep collection** | **IMPLEMENTED + VERIFIED offline**; live canary **BLOCKED** | `b3b3fd8` |
| **E — Worker runtime + IPC** | **PASS** (pipe thật, SID thật trên Windows) | `5d99a8f` |
| **F — UI-01..UI-10** | **PASS headless**; render visual trên desktop thật **NOT_VERIFIED** | `c24b109` |
| **G — Packaging + E2E** | **PASS một phần** — xem §5 (clean-VM NOT_TESTED) | commit sau F |

**Toàn bộ suite:** `415 tests passed` · **ruff:** `All checks passed` (mỗi commit đều chạy lại).

Điều kiện escalation đã cam kết **không** bị chạm: không đổi bất biến G0-B, không dùng token/session
VNEPS, không mang dữ liệu thật vào repo, không push/merge/deploy.

---

## 2. Bảng số đo (mọi dòng là lệnh đã chạy)

```
pytest (toàn bộ)                  : 415 passed
ruff check src tools tests        : All checks passed
commit list                       : ed6f103 → 31ff787 → c4bb00d → b3b3fd8 → 5d99a8f → c24b109 → c3007f7 → a45439d → (doc commit này)
benchmark raw store (5k pages)    : 4 pass, verify_failures = 0  (xem §5.3)
```

Chi tiết test theo module (số test mới trong batch):

| Module | Số test | Nội dung |
|---|---|---|
| `tests/integration/test_runner_modes.py` | 28 | 3 run mode qua writer thật, split, proof gate, watermark |
| `tests/integration/test_deep_capture.py` | 17 | capture/history/derived một transaction, staleness 4 lý do, multiplicity verbatim |
| `tests/integration/test_worker_ipc.py` | 15 | SID allow/deny, squatting, pause/resume/stop, crash recovery, FAULT-12 subprocess |
| `tests/integration/test_desktop_backend.py` | 15 | 10 màn UI đọc serving snapshot thật |
| `tests/integration/test_desktop_qml.py` | 2 | QML shell instantiate headless, mọi slot bridge trả JSON |
| `tests/integration/test_desktop_supervisor.py` | 2 | GUI spawn worker thật → attach → FORCE_TERMINATE |
| `tests/integration/test_desktop_*` + toàn bộ suite cũ | 415 tổng | không regression |

---

## 3. Tóm tắt từng milestone

### A — Storage & ingest engine (`ed6f103`)
- Raw object store append-only gzip 2 cấp, fsync + atomic rename, recovery; `WarehouseWriter`
  single-writer qua `DataRootLock` (FAULT-06); ingest qua extraction thật, quarantine, receipt.
- Identity `id1:` + 64 hex; hai vai trò hash (`response_object_sha` vs `record_object_sha`) tách rời.

### B — Network lane + adapter e-GP (`31ff787`)
- HTTP client với allowlist, rate limiter per-host, retry/backoff, scrub URL/exception.
- 6 family từ `source_registry`; field projection từ source repo **đọc để tham chiếu, không copy mã**.
- **B7 giữ nguyên:** lane chưa proven access ⇒ `requires_session=True`; không chạy canary thật.

### C — Runner + coverage/proof (`c4bb00d`)
- `Runner.backfill / incremental / reconcile`; skip **chỉ** qua resolver (§4.5).
- Adaptive split DAY→HOUR→MINUTE khi truncation/cap; chỉ ghi proof ở leaf (§4.3).
- Boundary rule là **dữ liệu có canary gate**; trigger DDL chặn COMPLETE thiếu rule/ proof adapter sha lệch.
- Watermark chỉ tiến trên COMPLETE liền kề; INCREMENTAL re-fetch overlap 2 ngày có chủ đích.

### D — Deep collection (`b3b3fd8`)
- `DeepLaneSpec` + `parse_detail` verbatim: HSMT 14 cột, lots, goods, memberships; multi-value
  **không tự chọn phần tử đầu**; số lượng `KNOWN/MISSING/UNPARSABLE`, không đoán.
- `storage.deep.record_deep_capture`: capture + history bất biến + derived rows **một transaction**;
  stale là quyết định (4 reason đóng); `done_ids` trả `captured_at + refresh_due_at`.
- Default specs `requires_session=True`, `paths=None` cho tới khi canary ⇒ **deep live BLOCKED**, đúng D-3.

### E — Worker runtime + IPC (`5d99a8f`)
- Job request bất biến dạng document trong `jobs/queued|active|done`; claim bằng atomic rename.
- `WorkerLoop` trên DDL `runs`/`run_tasks`/`job_commands`; hai trục lifecycle/status giữ tách biệt.
- PAUSE cắt ở page boundary (flag bật từ IPC thread, audit row ghi ở loop thread);
  RESUME = re-dispatch một **run mới** (resolver làm idempotent); STOP_GRACEFUL → `TERMINATED/INTERRUPTED`.
- Crash recovery: run không terminal của worker chết → `TERMINATED/INTERRUPTED`, request re-queue
  (RECOVERABLE); PAUSED giữ nguyên.
- Named pipe ctypes: SDDL owner-only + `FILE_FLAG_FIRST_PIPE_INSTANCE` + chuỗi PID→token→SID
  fail-closed; không có bind mạng.
- **FAULT-15** (SID lạ bị từ chối), **squatting** (bind thứ hai fail), **FAULT-12** (worker sống khi
  client đóng; mở lại attach; spawn thứ hai bị từ chối) đều có test trên pipe/process thật.

### F — UI-01..UI-10 (`c24b109`)
- `desktop/backend.py`: mọi màn đọc **serving snapshot** qua kết nối `mode=ro` + `query_only`;
  GUI không mở warehouse để ghi; job request là file duy nhất GUI phát hành.
- Worker truth = lock probe không chặn + pipe ping (không suy diễn từ file).
- QML 10 màn: onboarding (validate data root), control center (records/versions/revisions/obs/verified/
  disk/snapshot), sources (access state + boundary rule), jobs (enqueue + PAUSE/RESUME/STOP/FORCE),
  coverage (claimed vs walked + interval + proof + granularity), explorer (FTS5 fold tiếng Việt →
  record detail), detail (timeline version + raw revision + deep/lots/goods/docs/memberships/group),
  deep queue (FULL/PARTIAL/stale/refresh due), integrity (orphan/quarantine/unresolved/gaps/marker),
  settings/backup.
- **Verify:** 15 backend test trên warehouse seed thật + snapshot thật; QML instantiate headless
  (offscreen) với cả 10 trang; smoke render 1180×760 PASS.
- **CHƯA verify:** chữ tiếng Việt trên desktop thật — screenshot từ phiên agent chụp nhầm cửa sổ
  foreground (`Screen.grabWindow` khi cửa sổ không được raise); offscreen thiếu font nên glyph ra ô
  vuông. Cần một lần chạy tay trên máy user hoặc trong E2E VM (G).

### G — Packaging + E2E
- Build script chuyển worker từ fake A8 sang **worker thật** (`workers/service.py`); dist rename
  `service.dist → SanThauCollector.dist`; stage gộp hai dist theo topology §2.2.
- **Đo được:**
  - `BUILD_OK` / `ASSEMBLE_OK`, log `_evidence/g1/build-standalone-4.log`
  - `SanThauDataStudio.dist`: 2.515 file / 184.548.356 bytes
  - `SanThauCollector.dist`: 635 file / 40.046.471 bytes
  - `build/stage`: 2.517 file / 201.682.131 bytes (đủ `SanThauDataStudio.exe`,
    `SanThauCollector.exe`, `qml/Main.qml`, `docs/ddl/g0_b_proposed.sql`)
  - Worker đóng gói E2E: tạo schema, `ping`/`status` OK qua pipe, `FORCE_TERMINATE` → **rc 0**,
    lock nhả sạch
  - GUI đóng gói smoke (offscreen): **PASS**, ảnh 1180×760, exit 0
  - Installer: `build/inno/SanThau-Data-Studio-Setup-0.0.1-spike.exe`, **48.735.438 bytes**,
    compile 93,5 s
- **Chưa chạy:** cài đặt thật (silent install/uninstall) và clean-VM — Docker engine không chạy
  trong phiên này; cài đặt per-user ghi registry/profile nên **không tự làm** khi chưa có duyệt.
- Inno script giữ nguyên bất biến: per-user install (không UAC), không xoá data root khi uninstall.

---

## 4. Lỗi thật phát hiện trong batch và cách sửa (đáng đọc)

| # | Lỗi | Nơi | Sửa |
|---|---|---|---|
| 1 | Loader boundary rule thiếu cột `source_timezone`/`offset_minutes` → `KeyError` khi Runner đọc rule từ DB (seam chưa từng chạy vì Milestone C inject `rule_for`) | `storage/coverage.py` | SELECT đủ cột `BoundaryRule.from_row` cần |
| 2 | `busy_timeout` đặt **sau** `journal_mode=WAL` → reader mở giữa lúc writer commit nổ `database is locked` | `storage/warehouse.py` | busy_timeout trước; thêm `connect_read_only` (mode=ro, không đổi journal mode) cho GUI/test |
| 3 | GUI lock-probe (acquire thử) đua cửa sổ startup của worker → worker `DataRootBusy` chết im lặng | `storage/warehouse.py` | startup acquire retry 5×0.2s; second writer thật vẫn bị từ chối (~1s) |
| 4 | PAUSE chỉ được áp dụng khi loop rảnh → không bao giờ cắt page boundary của run đang chạy | `workers/jobs.py` | IPC thread bật flag ngay; audit row vẫn ghi ở loop thread |
| 5 | `WorkerController` mở read conn ở thread IPC nhưng `close()` ở main thread → `ProgrammingError` cross-thread | `workers/jobs.py` | conn `mode=ro` với `check_same_thread=False` |
| 6 | Claim không tạo thư mục `active/` → rename fail bị nuốt như thua race | `workers/jobs.py` | mkdir trước vòng claim |
| 7 | Test recovery dùng reader `query_only` để UPDATE | test | dùng `connect()` cho hai test recovery (không có worker cạnh tranh) |
| 8 | Job kind BLOCKED được kỳ vọng lifecycle `FINISHED` — sai §3.6 (`finish_run` map BLOCKED→TERMINATED) | test | sửa kỳ vọng theo DDL CHECK |
| 9 | Packaged worker chết rc=1 vì dist **thiếu `docs/ddl/g0_b_proposed.sql`** | build script | `--include-data-files` cho worker |
| 10 | Trong layout đóng gói `__file__` sâu hơn source một cấp ⇒ `parents[3]` trỏ sai thư mục; worker không thấy DDL | `storage/warehouse.py` | `find_schema()` dò ngược lên; 3 test layout source/package/không-có |
| 11 | GUI-subsystem build không có console ⇒ lỗi startup mất dấu | `workers/service.py` + `jobs.py` | ghi `logs/worker.log` trong data root, `last_error` giữ traceback đầy đủ |

---

## 5. Việc CHƯA verify / BLOCKED (nói thẳng)

1. **A1 — token dashboard đã lộ:** vẫn **BLOCKED** tới khi owner thu hồi. Không dùng, không kiểm tra.
2. **Live lane e-GP / deep (B7):** chưa chạy canary thật (cần session user + pháp lý); mọi thứ
   được chứng minh offline trên shape portal ghi lại. Đây là **BLOCKED có chủ đích**, không phải PASS.
3. **Benchmark raw store:** lần chạy 100k **crash** (`OSError: [Errno 22]` khi đọc gzip layout
   object-per-page, log `_evidence/g1/bench-raw-100k.log`); cây 1,1 triệu file thử nghiệm đã dọn.
   Benchmark thu nhỏ **5k pages × 10 records (55.000 object)** chạy lại **thành công, 0 verify
   failure** (`_evidence/g1/bench-raw-5k.json`). Số đo thật:

   | Pass | Files | Write | Read (scaled) | Verify | Alloc / Logical |
   |---|---:|---:|---:|---:|---:|
   | objects-buffered | 55.000 | 45,45 s | **702,02 s** | 28,92 s | 225.280.000 / 34.383.357 |
   | objects-fsync | 55.000 | 256,04 s | 726,84 s | 21,16 s | 6,55× |
   | segments-buffered | 2 | 6,12 s | **2,08 s** | 1,02 s | 34.385.920 / 34.383.357 |
   | segments-fsync | 2 | 36,93 s | 1,26 s | 0,54 s | 1,00× |

   Đọc object layout chậm **~128×** so với segments ở quy mô này (13,2 ms/object vs 0,023 ms);
   overhead cấp phạt 6,55×. **100k chưa chạy lại** — read phase 5k đã mất 12 phút nên 100k không
   khả thi trong một phiên.
4. **Clean-VM E2E (Docker Windows container):** **NOT_TESTED** — Docker engine không chạy trong
   phiên này; build + smoke trên máy hiện tại là mức đã đạt. Cài đặt thật (silent install/uninstall)
   chưa chạy vì ghi registry/profile: cần duyệt hoặc chạy trong VM.
5. **UI visual trên desktop thật:** như §3-F — cần mở tay hoặc VM.
6. **Task Scheduler `--scheduled --profile default`:** chưa wire (spec §2.3 opt-in; cần GUI settings).

---

## 6. Quyết định cần owner chốt

| # | Quyết định | Đề xuất | Tradeoff |
|---|---|---|---|
| D1 | Layout raw store (object-per-page+record vs byte-offset segment) | **Chuyển sang segment** theo số 5k: đọc nhanh ~128×, cấp phạt 1,00× vs 6,55×, cùng verify 0 failure; fsync cost tương đương (5,6× vs 6,0×). Object layout chỉ còn lợi thế “mỗi object một file” khi cần phục hồi từng phần | Đổi layout là thay đổi hạ tầng lớn: cần migration/dual-read, và segment làm hỏng một file thì mất cả lô span (bù bằng checksum + ghi append) |
| D2 | Tiêu đề record khi nguồn không có (`title=None`) | Hiển thị “(không tiêu đề)” + `version_state` thật, không bịa | Đúng §0.2; UI trông “trống” hơn |
| D3 | Deep lane LIVE | Khoá tới khi có canary dashboard (D-3 giữ nguyên) | Không có dữ liệu HSMT live; đổi lại không claim bừa |
| D4 | RESUME semantics | Run mới cho cùng job request (resolver idempotent) | Run PAUSED giữ làm lịch sử; UI thấy 2 run cùng job |
| D5 | Crash recovery auto-requeue | Job INTERRUPTED được đưa lại queue ở lần start worker kế tiếp | Job “độc” sẽ retry mỗi lần start cho tới khi BLOCKED/FAILED |
| D6 | Phạm vi named pipe | 1 pipe/user (không theo data root) ⇒ 1 worker/user tại một thời điểm | Đơn giản, đúng §2.4; đa data root đồng thời cần đổi tên pipe theo root sau này |
| D7 | Benchmark scope | 5k đã xong và dùng làm bằng chứng D1; 100k để nightly/VM có thời gian | 5k đủ phân hạng hai layout; 100k sẽ cho số tuyệt đối nhưng tốn nhiều giờ trên máy này |

---

## 7. Cách chạy lại (không cần đọc code)

```bash
uv run pytest -q                       # 415 tests
uv run ruff check src tools tests      # lint
powershell -ExecutionPolicy Bypass -File packaging/nuitka/build_standalone.ps1
iscc /DStageDir=<repo>\build\stage packaging\inno\santhau-data-studio.iss
```

Smoke GUI (không mở app lâu):

```bash
uv run python -m santhau_studio.app_main --data-root <path> --smoke <shot.png>
```

Worker thật:

```bash
uv run python -m santhau_studio.workers.service --data-root <path>
```

---

## 8. Giới hạn của báo cáo này

- Mọi số “passed/OK” ở trên đến từ lệnh chạy trong phiên; không có tuyên bố nào dựa trên trí nhớ.
- Không có acceptance nào bị đánh PASS thay: A1, live lane, deep live, clean-VM, benchmark 100k được
  liệt kê là BLOCKED/NOT_TESTED đúng trạng thái.
- Batch chưa đóng sản phẩm: G chỉ đạt mức build + smoke local; E2E VM và quyết định owner ở §6 là
  điều kiện trước khi coi G1 là hoàn tất.