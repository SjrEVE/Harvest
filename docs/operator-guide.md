# Operator guide — Săn Thầu Data Studio (RC2)

## Cài đặt và mở ứng dụng

1. Chạy `SanThau-Data-Studio-Setup-*.exe` (per-user, không cần admin). Mặc định cài vào
   `%LocalAppData%\Programs\SanThauDataStudio`.
2. Mở **Săn Thầu Data Studio** từ Start Menu.
3. Màn **01 · Onboarding**: chọn Data Root (thư mục dữ liệu), bấm **Kiểm tra** để xác nhận quyền
   ghi và dung lượng. Data Root **không nằm trong Program Files** và **không bị xoá khi uninstall**.
4. Bấm **Dùng thư mục này** để áp dụng. Các màn hình và worker sẽ dùng kho đã chọn; lựa chọn được
   lưu cho lần mở tiếp theo. Worker của kho cũ tiếp tục chạy nếu trước đó chưa được dừng.

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
  `warehouse.pre-restore-*.sqlite`. Dừng worker trước khi restore; GUI chỉ đọc snapshot nên có thể mở.
- Không tự động GC dữ liệu chưa được verify; xoá object chỉ qua thao tác compact/GC có chủ đích.

## Nhật ký và sự cố

- Worker: `<data root>\logs\worker.log` (lỗi khởi tạo, lý do dừng).
- GUI: `<data root>\logs\gui.log` (instance thứ hai bị từ chối).
- Worker bị kill đột ngột → lần chạy sau đánh dấu run INTERRUPTED và re-queue job (RECOVERABLE).
- Uninstall khi app đang mở: installer gửi tín hiệu đóng mềm và chờ cả GUI lẫn worker kết thúc;
  nếu chạy silent automation, truyền `/CLOSEAPPLICATIONS`.

## Lưu ý bảo mật

- Không nhập token/session của nguồn vào bất kỳ trường nào; ứng dụng không lưu credential.
- Không mang file dữ liệu thật ra khỏi Data Root; repo không chứa dữ liệu thật.
- A1 (token dashboard từng lộ) vẫn phải được thu hồi phía owner trước khi dùng bất kỳ dashboard nào.

## Các thao tác đã nối ở RC2

- **Settings / Backup → Tạo backup**: collector tạo bản sao SQLite nhất quán với manifest raw,
  lưu trong `<Data Root>/backups`. Tác vụ chạy ở process riêng, giao diện vẫn phản hồi.
- **Khôi phục…**: nhập đường dẫn một backup Data Studio, xác nhận, và dừng worker trước. Kho đang
  được writer giữ sẽ bị từ chối. GUI có thể mở vì chỉ đọc snapshot; bản backup cũ của warehouse được giữ.
- **Kiểm tra dữ liệu**: kiểm hash/raw, SQLite integrity và khóa ngoại; xuất lại snapshot cho GUI.
  Dừng worker trước thao tác này.
- **Xuất CSV**: xuất các source record trong snapshot đang công bố, có source/family/version và
  normalized JSON. Giá trị chưa biết để trống; ô có thể thành công thức được vô hiệu hóa.
- **Bật lịch 08:00 / 20:00**: chỉ tạo từ thao tác này. Mỗi lần chạy tạo job INCREMENTAL cho family
  có boundary rule được xác minh và nguồn được phép. Không có rule thì không tự thu thập nguồn.
- Trong khi một tác vụ bảo trì chạy, ứng dụng yêu cầu chờ trước khi đổi Data Root hoặc đóng GUI.
- Sau mỗi job, worker công bố snapshot. Trong lúc thu thập hoặc nếu công bố lỗi, GUI giữ dữ liệu
  cũ và hiển thị trạng thái stale; không cần chạy script tạo snapshot bằng tay.
- Khi cài đè/gỡ RC2, installer yêu cầu GUI và worker đóng mềm. Nếu quá thời gian chờ, installer
  từ chối thay/xóa file; dừng collector từ màn Jobs rồi thử lại. Với bản spike cũ, hãy dừng worker
  trước khi nâng cấp lần đầu.

## Giới hạn bản dùng thử

Binary RC2 dùng để kiểm tra nội bộ tại máy. Chưa có chữ ký Authenticode hoặc nghiệm thu Windows
sạch; quyết định phân phối Qt/PySide6 vẫn cần chủ sở hữu chốt. Deep live và các nguồn cần session
vẫn bị chặn. Khôi phục hỗ trợ backup Data Studio; không gọi đây là công cụ import kho harvester cũ.
