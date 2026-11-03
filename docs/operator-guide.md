# Operator guide — Săn Thầu Data Studio (RC2)

## Cài đặt và mở ứng dụng

1. Chạy `SanThau-Data-Studio-Setup-*.exe` (per-user, không cần admin). Mặc định cài vào
   `%LocalAppData%\Programs\SanThauDataStudio`.
2. Mở **Săn Thầu Data Studio** từ Start Menu.
3. Màn **01 · Onboarding**: chọn Data Root (thư mục dữ liệu), bấm **Kiểm tra** để xác nhận quyền
   ghi và dung lượng. Data Root **không nằm trong Program Files** và **không bị xoá khi uninstall**.

## Worker và job

- Worker là tiến trình riêng (`SanThauCollector.exe`). GUI **04 · Collection Jobs** → **Khởi động
  worker**; đóng GUI **không** dừng worker.
- Mở lại GUI sẽ **attach** vào worker đang chạy (cùng Data Root). Hai GUI trên cùng Data Root bị
  chặn bằng mutex; worker thứ hai cùng Data Root bị chặn bằng data-root lock.
- Nút điều khiển: **PAUSE** (dừng ở cuối page hiện hành), **RESUME** (run mới cho cùng job),
  **STOP_GRACEFUL** (kết thúc INTERRUPTED, không tự COMPLETE), **FORCE_TERMINATE** (dừng worker).
- Hàng đợi job là các file trong `<data root>\jobs\{queued,active,done}`. Xoá tay các file này khi
  worker đang chạy là thao tác không được hỗ trợ.

## Nguồn dữ liệu và canary

- 6 family công khai qua `egp_home`. `egp_v2` cần session trình duyệt — **BLOCKED**; `moh_quotes` /
  `mof_quotes` đang quarantine.
- Mọi job BACKFILL/INCREMENTAL cần một **filter boundary rule** đã canary-verified. Nếu chưa có,
  job bị chặn với `BOUNDARY_RULE_UNVERIFIED` (fail-closed, không query không giới hạn).
- Quy trình canary (owner duyệt): xem `docs/canary-matrix.md` và `tools/canary.py plan`. Công cụ
  từ chối chạm nguồn thật nếu thiếu `--execute` + `SANTHAU_CANARY_APPROVED=<owner>`.
- Deep lanes (DETAIL/HSMT/REOFFER) chỉ chạy offline với fixture; live **BLOCKED** tới canary.

## Tìm kiếm và màn hình

- **06 · Data Explorer**: FTS5 đã fold tiếng Việt; phân trang bằng **Tải thêm**. Tìm theo tiêu đề,
  mã hồ sơ, chủ đầu tư; nếu không khớp cột hiển thị, hệ thống tự mở rộng sang body (một trang đầu).
- **02 · Control Center**: số liệu family lấy từ snapshot serving (cập nhật mỗi lần publish snapshot),
  không phải đếm live.
- **05 · Coverage**: COMPLETE phải có proof; dòng thiếu proof hiển thị lý do.
- **07 · Record Detail**: timeline content version, raw revision, deep capture, lots/goods/documents.
- **08 · Deep Collection**: ma trận lane (TESTED_OFFLINE / BLOCKED) + trạng thái stale/refresh.
- **09 · Data Integrity**: object hỏng, quarantine, unresolved, reconcile gaps, marker stale.
- **10 · Settings / Backup**: settings, danh sách backup, phiên bản, pipe name.

## Backup / Restore

- Backup là thư mục chứa snapshot warehouse + object gốc + manifest sha256.
- Restore **verify trước**, không swap nếu thiếu/hỏng object; warehouse cũ được giữ dưới tên
  `warehouse.pre-restore-*.sqlite`. Dừng worker và GUI trước khi restore.
- Không tự động GC dữ liệu chưa được verify; xoá object chỉ qua thao tác compact/GC có chủ đích.

## Nhật ký và sự cố

- Worker: `<data root>\logs\worker.log` (lỗi khởi tạo, lý do dừng).
- GUI: `<data root>\logs\gui.log` (instance thứ hai bị từ chối).
- Worker bị kill đột ngột → lần chạy sau đánh dấu run INTERRUPTED và re-queue job (RECOVERABLE).
- Uninstall khi app đang mở: app đăng ký Restart Manager để installer đóng mềm; nếu chạy silent
  automation, truyền `/CLOSEAPPLICATIONS`.

## Lưu ý bảo mật

- Không nhập token/session của nguồn vào bất kỳ trường nào; ứng dụng không lưu credential.
- Không mang file dữ liệu thật ra khỏi Data Root; repo không chứa dữ liệu thật.
- A1 (token dashboard từng lộ) vẫn phải được thu hồi phía owner trước khi dùng bất kỳ dashboard nào.