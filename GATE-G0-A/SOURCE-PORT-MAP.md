# SOURCE-PORT-MAP — G0-A

**Repo nguồn:** `Gia-Su-AI/DauThauAI` · **nhánh:** `codex/radar-harvester` · **SHA:** `aa65f0a6edcb98877d705c8495ca00c79303f86e`
**SHA khớp spec:** có — `git rev-parse codex/radar-harvester` = `aa65f0a6…` (đúng ký tự).
**Repo nguồn không bị sửa đổi:** mọi thao tác là `git show`/`git ls-tree` read-only; không checkout, không ghi.

## 1. Correction về đường dẫn

Spec §1.1 ghi đường dẫn **không có prefix**. Đường dẫn thật nằm dưới `tools/radar-harvester/`.
Bảng dưới dùng đường dẫn đầy đủ tính từ gốc repo nguồn.

Spec §13.1 cũng ghi "57 file Python, 12.791 LOC" — **đã đối chiếu và khớp chính xác** tại SHA này
(57 file `.py`, 12.791 dòng kể cả test; riêng `harvester/` là 7.668 dòng).

## 2. Port map

Ký hiệu: **PORT** = viết lại theo interface mới; **ADAPT** = giữ logic, đổi kiến trúc;
**QUARANTINE** = không port ở G1; **DROP** = bỏ.

| Đường dẫn nguồn (đầy đủ) | Xử lý | Đích dự kiến trong Data Studio | Ràng buộc khi port |
|---|---|---|---|
| `tools/radar-harvester/harvester/adapters/base.py` | PORT | `collectors/adapters/base.py` | `Observation` phải mang **cả** `response_object_sha` + `record_object_sha` + `record_locator` (§3.2, B1). Scrub URL khỏi **mọi** exception text (§6) |
| `tools/radar-harvester/harvester/adapters/egp_home.py` | PORT | `collectors/adapters/egp_home.py` | Giữ page size thực, filter, canary. **Rate limiter phải theo host, không theo adapter instance** (§6 — 6 adapter cùng host thành 0.5 s/request là bug). Phải khai `filter_boundary_kind` + quy tắc quy đổi (B5) |
| `tools/radar-harvester/harvester/adapters/egp_v2.py` | ADAPT tuỳ chọn | `collectors/adapters/egp_v2.py` | Dòng 213 truyền token **qua query** — đã xác nhận. Chỉ bật nếu canary chứng minh nguồn nhận header; nếu không → lane `BLOCKED` (B7). Không bypass CAPTCHA |
| `tools/radar-harvester/harvester/adapters/moh_quotes.py` | QUARANTINE | — | Nguồn có bản thứ hai ở `server/app/radar/adapters/moh_quotes.py`; spec không chỉ rõ bản nào. **Bản harvester** là đối tượng quarantine |
| `tools/radar-harvester/harvester/adapters/mof_quotes.py` | QUARANTINE | — | Như trên, hai bản tồn tại |
| `tools/radar-harvester/harvester/registry.py` | PORT | `collectors/source_registry.py` | Registry duy nhất; half-open windows; timezone nguồn |
| `tools/radar-harvester/harvester/modes.py` | PORT | `collectors/runner.py` (mode) | Ba mode theo §4.4; bỏ khớp tuple (BUG-11) |
| `tools/radar-harvester/harvester/runner.py` | ADAPT | `collectors/runner.py` | Dòng 443/445 dùng `date.today()` → **Clock injection** (§3.9, BUG-12). Dòng 480 ghi `COMPLETED` vào `coverage_windows.status` → đổi sang từ vựng `interval_status` (BUG-03) |
| `tools/radar-harvester/harvester/storage/warehouse.py` | ADAPT CẨN THẬN | `storage/warehouse.py` + `storage/schema/` | Viết lại theo DDL §3.10. Dòng 506 đọc `'COMPLETE'` (đúng phía interval). Dòng 641–646 `UPDATED_RAW` **có tăng version_no** → phải sửa: raw-only không tăng content version (§3.5, DEDUP-09) |
| `tools/radar-harvester/harvester/storage/rawstore.py` | ADAPT | `storage/rawstore.py` | Thay bằng object store hai cấp §5.1. Dòng 42: `gzip.close()` flush nhưng **không fsync** (BUG-06) → theo giao thức §5.2 |
| `tools/radar-harvester/harvester/locking.py` | ADAPT | `platform_win/os_lock.py` | fsync chỉ tồn tại ở dòng 106 — giữ native single-writer lock, tách khỏi raw write path |
| `tools/radar-harvester/harvester/storage/serving.py` | PORT | `storage/serving.py` | `build_serving_db` swap `os.replace` đã đúng → **giữ nguyên** (§5.5) |
| `tools/radar-harvester/harvester/reports/generator.py` | PORT | `reports/` | Không HTTP public |
| `tools/radar-harvester/harvester/reports/inventory.py` | PORT | `reports/` | Dòng 107 `MAX(id)` che COMPLETE đã verify → gọi `resolve_coverage()` duy nhất (§4.5, BUG-04) |
| `tools/radar-harvester/harvester/operations.py` | THAM KHẢO | `storage/integrity.py` | Tách source/index/detail/item rows. fsync có ở dòng 487 |
| `tools/radar-harvester/analysis/collection_common.py` | PORT | `collectors/deep/` | `done_ids` phải trả kèm `captured_at` + `refresh_due_at` (B6). Dòng 209 `None` trần → `version_state` (§3.4, BUG-08) |
| `tools/radar-harvester/analysis/crawl_index.py` | PORT THÀNH TASK | `collectors/index/` | Adaptive split + union identity, hợp nhất coverage interval §4. Dòng 164 `"None"` → sentinel |
| `tools/radar-harvester/analysis/crawl_detail_full.py` | QUARANTINE → TASK | `collectors/deep/` | Chứa id mẫu thật (2 marker). Chỉ bật sau canary |
| `tools/radar-harvester/analysis/crawl_hsmt.py` | QUARANTINE → TASK | `collectors/deep/` | 14 cột HSMT giữ từ raw, không crop (§10.4) |
| `tools/radar-harvester/analysis/crawl_reoffer.py` | QUARANTINE → TASK | `collectors/deep/` | Chỉ là `reported_snapshot` |
| `tools/radar-harvester/analysis/repair_data.py` | PORT chỉ cho MIGRATION | `storage/import_legacy.py` | Không tổng hợp raw chưa tồn tại |
| `tools/radar-harvester/harvester/storage/export.py` | PORT | `reports/` | Không có trong bảng §1.1 nhưng tồn tại và cần cho UI-10 |
| `tools/radar-harvester/harvester/storage/repair.py` | ADAPT | `storage/integrity.py` | Repair không sửa âm thầm (§10.3 FAULT-04) |
| `tools/radar-harvester/harvester/config.py` | ADAPT | `platform_win/paths.py` + settings | Data root do user chọn, **không** trong Program Files (§2.2) |
| `tools/radar-harvester/harvester/cli.py` | ADAPT | `worker_main.py` | Bỏ nhánh điều khiển server |
| `tools/radar-harvester/harvester/serve_api.py` | **KHÔNG SHIP** | — | `?t=` (chính là cơ chế gây lộ token ở A1), CORS `*` dòng 253, exception lộ URL |
| `tools/radar-harvester/harvester/desktop.py` | DROP UI | — | Dòng 96 dùng `Path` không import (BUG-14) |
| `tools/radar-harvester/harvester/webui.py` | DROP UI | — | Mở control server không xác thực |
| `tools/radar-harvester/harvester/webui_page.py` | DROP UI | — | |
| `tools/radar-harvester/harvester/operations.html` | DROP UI | — | |
| `tools/radar-harvester/harvester/recent.py` | DROP | — | |
| `tools/radar-harvester/ops/backfill.sh` | DROP | `platform_win/task_scheduler.py` | |
| `tools/radar-harvester/ops/harvester-refresh.service` / `.timer` | DROP | như trên | systemd → Windows Task Scheduler |
| `server/app/*`, `web/*`, MCP, auth, billing | DROP | — | Ngoài phạm vi sản phẩm desktop |

## 3. File KHÔNG bao giờ được mang sang

| Đường dẫn | Lý do | Trạng thái G0-A |
|---|---|---|
| `tools/radar-harvester/HANDOFF_WEB_DISPLAY.md` | dòng 127 chứa token + host tunnel (A1) | không copy |
| `tools/radar-harvester/tests/fixtures/egp_home_page0.json` | **REAL captured payload** (tự khai trong README của nó) | đã sanitize → `tests/fixtures/synthetic/…__synthetic.json` |
| `tools/radar-harvester/tests/fixtures/egp_home_type_samples.json` | shape thật, **không có README provenance** | đã sanitize |
| `tools/radar-harvester/tests/fixtures/egp_search_tbmt.json` | synthetic, provenance đã xác minh | **được phép** port làm shape reference, kèm ghi chú nguồn |
| `tools/radar-harvester/examples/report*.json`, `report*.md` | bản copy của lần chạy thật 31/10/2026 | 2 file JSON đã sanitize; 4 file `.md` **chưa** — cần xử lý ở G1 |
| `tools/radar-harvester/tests/test_serving.py` (24 marker) | dữ liệu thật nhúng **trong code test** | chưa xử lý — xem S-5 |
| `tools/radar-harvester/tests/test_home_types.py` (8 marker) | như trên | chưa xử lý |
| `tools/radar-harvester/harvester.local.yaml` | config local bị commit (không chứa credential — đã quét) | không copy; chỉ là point vệ sinh |
| `tools/radar-harvester/config.example.yaml` | mẫu, không secret | tham chiếu cấu hình |
| `tools/radar-harvester/uv.lock`, `pyproject.toml` | pin `>=3.11`, range mở | **không dùng** — Data Studio có lockfile riêng (A6) |

## 4. Quy tắc provenance cho mỗi commit port

Mỗi file port phải ghi trong `docs/SOURCE_PROVENANCE.md`:

```
source_path : tools/radar-harvester/harvester/adapters/egp_home.py
source_sha  : aa65f0a6edcb98877d705c8495ca00c79303f86e
port_commit : <sha của commit port>
reason      : <vì sao đổi semantics — ví dụ rate limiter theo host>
tests       : <ID test chứng minh, ví dụ DEDUP-15, COV-17>
```

## 5. Việc mở

| ID | Hạng mục | Ghi chú |
|---|---|---|
| P-1 | `moh_quotes.py` / `mof_quotes.py` có **hai bản** (harvester + `server/app/radar/adapters/`) | spec §1.1 không chỉ rõ bản nào; cần owner chốt |
| P-2 | 4 file `examples/*.md` chứa dữ liệu thật, chưa có đường sanitize cho Markdown | sanitizer hiện chỉ nhận JSON — cần chế độ text ở G1 |
| P-3 | `storage/export.py`, `storage/repair.py`, `config.py`, `cli.py` không có trong bảng §1.1 nhưng bắt buộc phải port | đã bổ sung vào map, cần owner xác nhận phạm vi |
