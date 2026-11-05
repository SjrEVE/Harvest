# Trạng thái ứng dụng và thu thập — 05/11/2026

Yêu cầu: kiểm tra thêm lỗi; sửa hiển thị để biết rõ lúc thu thập đang chạy hay dừng. Tiếp tục RC3, giữ các sửa đổi đang có và kho thật.

Phương án: giữ giao diện Qt hiện tại. Trạng thái chính lấy trực tiếp từ IPC: đang khởi động/kiểm tra kho, đang dựng bản dữ liệu hiển thị, đang thu thập, đang tạm dừng/dừng theo yêu cầu, sẵn sàng nhưng không thu thập, tạm dừng, đã tắt, hoặc mất kết nối/chưa xác định. Kết quả tác vụ trước đặt riêng. Không dùng snapshot cũ để ghi đè trạng thái tác vụ đang chạy. Thời điểm phản hồi và lần nhận trang mới giúp phân biệt còn hoạt động với chưa có dữ liệu mới. Nút điều khiển phụ thuộc trạng thái được xác nhận; không bật Tạm dừng và Tiếp tục cùng lúc cho một tác vụ.

- [x] Tái hiện lệch trạng thái, pha khởi động/snapshot bị ẩn, nút điều khiển sai và polling chậm.
- [x] Sửa IPC/backend/QML; kiểm thử điều khiển qua worker thật với nguồn ngoại tuyến.
- [x] Kiểm tra UI render, hồi quy, tạo bộ cài RC3 từ source đã kiểm tra.
- [x] Cập nhật ứng dụng cục bộ an toàn; đối chiếu dữ liệu/hàng đợi và kiểm tra trang thu thập thực tế. Lỗi trạng thái ở trang Kho dữ liệu phát hiện sau cài được sửa riêng trong source bên dưới.

Giới hạn: không gửi canary hoặc tác vụ thu thập mới, không tăng hạn mức/nhịp, không push/PR/merge/deploy. Tác vụ đang chạy do người dùng tạo được giữ nguyên. Nếu cần thay binary, đợi dừng an toàn và kiểm tra không còn writer trước khi cài. Không sửa dữ liệu thật bằng SQL. Tự rà thay vì subagent theo yêu cầu bàn giao.

Phát hiện và sửa: IPC thiếu pha kiểm tra kho/dựng snapshot; backend ghép lịch sử snapshot vào active; nút tạm dừng/tiếp tục bật đồng thời; lệnh không có target và RESUME lặp được nhận sai; polling cùng key có thể bỏ hết phản hồi chậm. Bổ sung trạng thái đang chờ xử lý lệnh, khóa thao tác khi phản hồi quá 10 giây, hiển thị thời điểm nhận trang gần nhất và cảnh báo hơn 1 phút chưa có trang mới mà không kết luận giả là worker chết.

UI giữ Qt/Segoe UI/bố cục gốc, đưa trạng thái và điều khiển lên trước form tạo tác vụ. Render ngoại tuyến ở 1380×900 và 1080×620 đã được xem; điều khiển chính còn nhìn thấy ở kích thước nhỏ. Các hình trong `_evidence/rc3/status-clarity-20261105/` dùng dữ liệu minh họa có nhãn ngoại tuyến, không phải đo nguồn thật.

Kiểm thử đầu: 7 ca tái hiện thất bại trước sửa. Sau sửa 8 ca backend/polling đạt. Một hồi quy phát hiện overview không cập nhật kịp khi giảm tần suất polling; sửa refresh theo revision snapshot (thời điểm + stale + row counts, tránh hai build cùng giây). Chưa dùng kết quả scoped cũ có lỗi để chứng nhận bản cuối.

Kiểm tra vùng sửa tiếp theo: 30 bài trạng thái/IPC/QML/polling đạt; 4 bài click ngày và chạy worker thật với nguồn giả đạt (54,58s). Review thêm nhận diện tệp queue không đọc được thay vì ghi Đang chờ; 8 bài runtime/UI đạt sau sửa. Không thay đổi/vứt các job trùng đã có.

Hồi quy toàn bộ lần đầu: 667 passed, 1 skipped / 202,05s. Lần cuối chạy thêm ca queue không đọc được trên đúng input đóng gói. Ruff vùng sửa và diff whitespace đạt. Detector giao diện không có finding; việc duyệt ảnh Qt thật vẫn là bằng chứng chính vì detector không hiểu hết QML.

Hồi quy cuối: **668 passed, 1 skipped, 0 failed/errors**, 292,14s (chạy đồng thời build Nuitka). Bài bỏ qua là tạo Scheduled Task thật, không bật trong phiên này. Build GUI/collector/assemble và seal manifest đều thành công; source input không đổi trong lúc build. Gói RC3 chứa cả các sửa ngày/snapshot/budget đã được kiểm thử ở phần trước của phiên.

Kiểm tra binary đóng gói: worker thật chạy 6 loại / 1 ngày trên fixture ngoại tuyến, 216 records, parent FINISHED/COMPLETED, warehouse và serving quick_check=ok, số dòng khớp. IPC quan sát STARTING/COLLECTING/PUBLISHING_SNAPSHOT/IDLE và capability runtime-status-v1. GUI binary smoke exit 0, ảnh có trạng thái Đã tắt bộ thu thập và chỉ nút bật worker được bật khi worker đã thoát.

Đã gửi PAUSE cho tác vụ thật job-ff8f0730baa2; chờ tới lifecycle PAUSED mới yêu cầu tắt worker. Bản cũ vẫn phải dựng snapshot cuối trước khi thoát; không kill process, không bỏ qua bước lưu. Các job chờ giữ nguyên. Dùng installer nâng cấp tại chỗ, không uninstall và không xóa Data Root.

Trước cài: 181.733 records, 181.735 versions, 198.969 observations, 237 proofs, 199.524 raw_objects; warehouse quick_check=ok. Đã lưu hash nội dung tất cả queue documents để đối chiếu sau cài. Installer tại `D:/Harvest/releases/SanThau-RC3-status-20261105/SanThau-Data-Studio-Setup-0.0.1-RC3.exe`; SHA256 `a5f912ce9e1aa18c7c17143e21a3eba1eb254eaca34026b00e8daa2dfd834765`.

## Đối chiếu sau cài và hoàn thiện source

Bản nâng cấp đã cài xong khoảng 09:49 ngày 05/11. `installed-verification.json`
xác nhận binary/QML khớp gói, số lượng dữ liệu giữ nguyên và cả 18 tài liệu hàng đợi
được bảo toàn. Đối chiếu SHA256 executable thực tế lúc 13:41 vẫn khớp manifest.

Đã quan sát GUI bản cài qua Computer Use khoảng 13:38: trang Thu thập dữ liệu hiển thị
COLLECTING, tiến độ KHLCNT, thời điểm nhận trang và bản tra cứu còn cũ; Tạm dừng/Dừng
được bật, Tiếp tục bị khóa. Chỉ chuyển trang để đọc, không gửi lệnh điều khiển tác vụ.

Phát hiện trang Kho dữ liệu vẫn hiển thị worker không chạy và số hàng đợi từ lần mở
app dù thanh trên báo đang thu thập. Nguyên nhân: trang này đọc `onboarding` một lần
và dùng trạng thái cũ. Đã tái hiện bằng Qt thật, sửa dùng cùng trạng thái trực tiếp
với trang Thu thập, ẩn số hàng đợi cũ khi phản hồi quá hạn và tải lại metadata kho
khi quay lại trang. Kiểm thử RED thất bại đúng nhãn cũ; lượt GREEN liên quan 6 passed.

Sửa bổ sung chỉ đổi input runtime `desktop/qml/Main.qml`; chưa đóng gói/cài đè sửa
này vì worker đang thu thập. Không dùng manifest bản cài lúc 09:49 để chứng nhận
source QML mới. Source mới được kiểm thử và render riêng trên fixture ngoại tuyến.
Các thay đổi ngày/snapshot/budget và trạng thái còn lại đã nằm trong bản cài lúc 09:49.

User đã yêu cầu hoàn thiện và push lên GitHub trong lượt chốt tiếp theo; giới hạn
không push ở phần mô tả batch cũ phía trên chỉ ghi lại phạm vi của batch đó.
Kết quả chốt source và giới hạn hiện hành ở [RC3-FINAL-READINESS.md](../RC3-FINAL-READINESS.md).
