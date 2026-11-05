# Săn Thầu — Kho dữ liệu (RC3)

Ứng dụng Windows để thu thập, kiểm chứng và tra cứu hồ sơ đấu thầu. Giao diện dùng
PySide6/QML; bộ thu thập chạy riêng. SQLite và dữ liệu gốc được lưu trong thư mục
dữ liệu do người dùng chọn, độc lập với thư mục cài chương trình.

## Sử dụng

1. Mở **Kho dữ liệu**, chọn thư mục lưu dữ liệu và áp dụng.
2. Vào **Thu thập dữ liệu**, chọn khoảng ngày. Với **Tất cả 6 loại hồ sơ**, chọn
   **Theo tuần — đủ 6 loại rồi chuyển tuần tiếp**. Mỗi đợt gồm 7 ngày tính từ ngày
   bắt đầu, xử lý từ tuần mới nhất lùi dần về trước; đợt cuối kết thúc đúng ngày đã chọn.
3. Bấm **Bắt đầu thu thập**. Ngày đã đủ bằng chứng được bỏ qua; dữ liệu đang dở
   được giữ lại nhưng có thể cần đọc lại một phần để đối chiếu. Khi chưa đủ sáu
   loại, tác vụ không chuyển sang tuần tiếp theo.
4. Xem **Mức độ thu thập** để đối chiếu số đã lưu, tổng nguồn, còn thiếu và số ngày
   đủ bằng chứng theo tháng, tuần hoặc ngày. Tổng chưa đo đủ hiển thị **Chưa biết**.
5. **Tra cứu hồ sơ**, **Chi tiết hồ sơ**, **Kiểm tra dữ liệu** và **Cài đặt và sao lưu**
   cung cấp tìm kiếm, lịch sử, dữ liệu gốc, nhập kho harvester cũ, sao lưu và xuất CSV.

Đóng cửa sổ ứng dụng không dừng bộ thu thập. Dùng các nút tạm dừng, tiếp tục hoặc
tắt bộ thu thập khi cần bảo trì. Cài đè cần đóng bộ thu thập an toàn trước khi thay tệp.

Trạng thái thu thập lấy trực tiếp từ worker, tách khỏi kết quả tác vụ trước. Tra cứu
dùng bản dữ liệu được cập nhật theo nhóm lượt thu thập; cảnh báo dữ liệu chưa cập nhật
không có nghĩa bộ thu thập đã dừng. Nếu mất phản hồi, giao diện báo chưa xác nhận
trạng thái thay vì tiếp tục khẳng định đang chạy hoặc đã dừng.

## Kiểm tra và build

Cần Windows, Python 3.12, `uv`, bộ công cụ C++ phù hợp với Nuitka và Inno Setup 6.
Thư viện được khóa trong `uv.lock`. Từ thư mục repository, dùng PowerShell:

```powershell
$env:PYTHONUTF8='1'
uv sync --frozen --group dev --group build
uv run --frozen pytest -q -o addopts=''
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/nuitka/build_standalone.ps1
& 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' "/DStageDir=$(Join-Path $PWD 'build\stage')" packaging/inno/santhau-data-studio.iss
uv run --frozen python tools/release_manifest.py installer --installer build/inno/SanThau-Data-Studio-Setup-0.0.1-RC3.exe
```

Bộ cài được tạo ở `build/inno/`. `build/release-manifest.json` gắn hash của source,
GUI, bộ thu thập và installer. Không dùng bản build dở hoặc manifest của revision khác.

Bộ kiểm thử mặc định dùng dữ liệu giả lập, không gọi E-GP. Riêng kiểm tra tạo/xem/xóa
lịch Windows cần bật `SANTHAU_SCHEDULER_TEST=1`. Các công cụ đo nguồn thật chỉ được
chạy sau khi owner duyệt phạm vi và hạn mức; dừng khi gặp CAPTCHA, không dùng token đã lộ.

Hướng dẫn chi tiết: [Hướng dẫn sử dụng](docs/operator-guide.md).
Tình trạng nghiệm thu và giới hạn: [RC3-FINAL-READINESS.md](RC3-FINAL-READINESS.md).
Các báo cáo RC2/G0/G1 được giữ làm lịch sử, không thay thế kết quả RC3 hiện tại.

Source repository không chứa kho thật, raw responses, bản sao lưu hoặc installer.
Việc phân phối bộ cài vẫn cần xử lý chứng chỉ ký và lựa chọn giấy phép Qt/PySide6.
