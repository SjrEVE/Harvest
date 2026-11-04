# Hướng dẫn sử dụng — Săn Thầu RC3

## Cài đặt và chọn kho

Chương trình cài tại `%LocalAppData%\Programs\SanThauDataStudio`. Mở **Săn Thầu — Kho dữ liệu**,
vào **Kho dữ liệu** và chọn thư mục lưu. Cấu hình được giữ cho lần mở tiếp theo;
gỡ chương trình không xóa thư mục dữ liệu. Nâng cấp sẽ yêu cầu chương trình và bộ thu thập
đóng an toàn; nếu chúng chưa dừng, installer phải từ chối thay tệp.

Kho thử nghiệm có cảnh báo rõ. Không dùng kho thử hoặc quy tắc nguồn giả lập để thu thập thật.

## Thu thập theo tuần

Trong **Thu thập dữ liệu**, chọn **Thu thập theo khoảng ngày**, **Tất cả 6 loại hồ sơ**,
hai ngày đầu/cuối và **Theo tuần — đủ 6 loại rồi chuyển tuần tiếp**. Khoảng ngày bao gồm
cả hai ngày, theo giờ Việt Nam. Tuần ở đây là 7 ngày liên tiếp từ ngày bắt đầu, xử lý
từ cũ đến mới; tuần cuối có thể ngắn hơn 7 ngày.

Một tác vụ tự xử lý sáu loại trong từng tuần. Ngày đã được xác nhận đủ bằng chứng được
bỏ qua. Chạm giới hạn thời gian hoặc số yêu cầu mà vẫn hoàn thành thêm khoảng thời gian
thì tác vụ tiếp tục phần thiếu trong cùng tuần. Nếu nguồn bị chặn, có lỗi chưa xử lý được
hoặc không tiến triển, tác vụ dừng và giữ tiến độ. Không chuyển tuần khi chưa đủ sáu loại.

Phần đã lưu không bị xóa khi chạy lại. Ngày đang dở có thể được đọc lại để đối chiếu
với nguồn thay đổi; điều này khác với cào lại ngày đã hoàn thành. Tác vụ tuần mới không
tự thay đổi các tác vụ cũ đã xếp hàng.

Chọn đúng tác vụ trong danh sách trước khi **Tạm dừng**, **Tiếp tục** hoặc **Dừng tác vụ**.
Đóng GUI vẫn để bộ thu thập chạy. **Tắt bộ thu thập** dùng khi cần bảo trì hoặc nâng cấp.
Không sửa hoặc xóa thủ công tệp hàng đợi trong khi bộ thu thập đang giữ kho.

**Cập nhật tự động** dùng mốc đã thu thập và khoảng kiểm tra chồng lấn để tìm chỉnh sửa gần đây.
**Đối chiếu dữ liệu** kiểm tra lại khoảng đã có bằng chứng. Các chế độ này khác với việc
chạy bù một khoảng ngày lịch sử.

## Thống kê và tra cứu

**Mức độ thu thập** hiển thị số đã lưu, tổng nguồn đã quan sát, số còn thiếu, tỷ lệ và số
ngày đủ bằng chứng theo tháng/tuần/ngày. Tổng nguồn chưa đo đủ hoặc bị giới hạn 10.000
không được coi là số chính xác. Bảng dùng dữ liệu hiển thị được công bố theo từng lượt,
có thời điểm cập nhật; số lượng đạt 100% không thay thế bằng chứng thu thập đầy đủ.

Ngày thống kê theo từng loại phải khớp ngày nguồn dùng để lọc. Kết quả lựa chọn nhà thầu
dùng ngày công bố kết quả, không cộng vào tháng công bố thông báo mời thầu. Tổng sáu loại
là số hồ sơ thuộc sáu tập dữ liệu, không phải số gói thầu riêng biệt.

**Tra cứu hồ sơ** có tìm kiếm tiếng Việt, bộ lọc, sắp xếp và phân trang. **Chi tiết hồ sơ**
giữ dữ liệu gốc và lịch sử; trường thiếu được giữ là chưa biết. Thông tin HSMT, hàng hóa
và tài liệu chỉ được coi là đã thu thập khi có dữ liệu và bằng chứng tương ứng.

## Đo tổng nguồn có giới hạn

`tools/measure_source_totals.py` mặc định chỉ in kế hoạch, không gửi yêu cầu mạng:

```powershell
uv run --frozen python tools/measure_source_totals.py --data-root D:\Harvest\SanThauData --date-from 01/01/2022 --date-to 03/11/2026 --output D:\Harvest\reports\source-census.json
```

Chỉ sau khi owner duyệt hạn mức mới thêm `--approved-requests N --max-seconds S`. Bộ thu thập
phải rảnh; công cụ từ chối chạy cùng một collector đang hoạt động. Nó đọc trang đầu,
chia nhỏ khoảng bị giới hạn và không cộng chồng khoảng cha/con. Dùng `--resume` để đo tiếp
báo cáo cùng phạm vi và quy tắc nguồn, với hạn mức được duyệt cho lần chạy tiếp theo.
Kết quả là số nguồn báo tại các thời điểm đo, không tạo bằng chứng COMPLETE trong kho.

## Nhập dữ liệu, sao lưu và khôi phục

Trong **Cài đặt và sao lưu**, chọn nhập kho harvester cũ, sao lưu, khôi phục hoặc xuất CSV.
Import giữ nguồn gốc và lịch sử; dữ liệu legacy thiếu bằng chứng vẫn là chưa xác minh.
Nhập lại cùng dữ liệu được kiểm tra chống trùng.

Sao lưu gồm ảnh SQLite nhất quán, đối tượng gốc và manifest SHA256. Khôi phục kiểm tra trước
khi thay kho; thiếu/hỏng đối tượng phải bị từ chối. Dừng bộ thu thập trước khi khôi phục
hoặc kiểm tra/bảo trì dữ liệu. Không tự xóa dữ liệu gốc để làm sạch lỗi.

Lịch hằng ngày được bật riêng trong Cài đặt; không tự được tạo chỉ vì mở ứng dụng.
Nhật ký khởi tạo nằm trong `logs/worker.log` và `logs/gui.log` của kho dữ liệu.
Sau khi tiến trình bị gián đoạn, lần khởi động tiếp theo dùng hàng đợi và bằng chứng
đã lưu để khôi phục; không biến một lần chết tiến trình thành trạng thái hoàn tất.

## Giới hạn

Các nguồn và luồng chi tiết chưa xác minh phải tiếp tục bị chặn. Không giải CAPTCHA,
không dùng token/session đã lộ, không coi kết quả giả lập là bằng chứng nguồn thật.
Xem [tình trạng RC3](../RC3-FINAL-READINESS.md) trước khi nghiệm thu hoặc phân phối.
