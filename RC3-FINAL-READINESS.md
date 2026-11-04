# RC3 — trạng thái source trước khi đưa lên GitHub

Ngày đối chiếu: 04/11/2026. Đây là trạng thái có bằng chứng, không phải tuyên bố toàn bộ
Master Spec đã hoàn thành. Kho thật, bản sao lưu, log và dữ liệu phản hồi nguồn nằm ngoài Git.

## Source và bộ cài

Bộ cài RC3 đã nâng cấp thực tế được build từ commit
`35c982b4dda995c42ec3088845e267d217df28db`.
SHA256 installer: `1b964843e537559c1752d3bbc1e1be205b3370a823419db84e6dddcfe1a25e52`.
Đã đối chiếu 2.530 tệp cài với stage và giữ nguyên toàn bộ số lượng/hash dữ liệu trước/sau nâng cấp.

Source hiện tại có thêm hai sửa lỗi sau bản cài đó: đánh dấu dữ liệu hiển thị đang cũ
trước mỗi lượt của tác vụ tuần; lấy trạng thái của tác vụ chính độc lập với số lượng
lượt chạy con. Các sửa source này chưa được đóng gói/cài đè trong lần review GitHub.

## Kết quả và phần còn thiếu

| Hạng mục | Trạng thái | Phạm vi |
|---|---|---|
| Thu thập tuần đủ sáu loại, bỏ qua ngày có bằng chứng | PASS | Kiểm thử offline và thao tác trên GUI đã cài; một lượt tuần nguồn thật đã khởi động. Không khẳng định đã cào đủ cả khoảng lịch sử. |
| Tạm dừng, tiếp tục, khôi phục sau khi chết tiến trình | PASS | Có kiểm thử process thật bị ngắt và khôi phục dữ liệu đã hoàn thành. |
| Tổng đã lưu/nguồn/thiếu theo tháng, tuần, ngày | PASS trong phạm vi snapshot | Giữ ngày riêng của từng loại, không cộng trùng cửa sổ, thiếu tổng thì chưa biết. |
| Nhập legacy, sao lưu/khôi phục, CSV, integrity | PASS kiểm thử | Không đồng nghĩa mọi kho legacy ngoài tập được hỗ trợ đều nhập được. |
| Nâng cấp installer RC3 tại máy hiện có | PASS | Dữ liệu được sao lưu và đối chiếu trước/sau; hàng đợi cũ được lưu riêng khi chuyển sang tuần. |
| GUI/worker compiled và tỷ lệ Qt 100/125/150% | PASS trong phạm vi đã chạy | Hai màn thay đổi có bằng chứng; chưa phải kiểm DPI hệ điều hành cho mọi màn. |
| Thống kê toàn nguồn từ 2022 | PARTIAL | Lượt owner duyệt dùng đúng 60 yêu cầu; chưa đủ chốt tổng cuối cùng. Không đánh dấu coverage COMPLETE bằng phép đếm nguồn. |
| Hồi quy source trước push | PASS | 626 bài đạt, 1 bài lịch Windows bỏ qua trong suite mặc định và được chạy riêng. Hai lỗi phát hiện trong review có bài kiểm tra tái hiện trước khi sửa. |
| Secret scan source và lịch sử nhánh | PASS trong phạm vi scanner | Quét blob của lịch sử reachable trước push; dữ liệu thật và binary không thuộc commit. |
| Độ phủ nhỏ hơn một giây | FAIL còn tồn tại | Lưu độ phủ vẫn chuẩn hóa về giây; không tuyên bố đã sửa. |
| HSMT, hàng hóa và chi tiết tổng quát toàn kho | BLOCKED/chưa đủ bằng chứng | Chưa xác nhận thu thập đủ mọi lane và mọi trường trên nguồn thật. |
| Authenticode và lựa chọn Qt LGPL/commercial | BLOCKED | Cần chứng chỉ và quyết định của owner; không tự ký. |
| Windows 10/11 sạch, toàn bộ nghiệm thu DPI hệ thống | NOT_TESTED | Chưa có môi trường và bằng chứng tương ứng. |

Fixture hiệu năng một triệu hồ sơ dùng chung một raw object, không chứng minh một triệu
raw object độc lập. Benchmark SQL không phải UI end-to-end. Truy vấn sort ngày trên
fixture lớn từng khoảng 2,2 giây; chưa xác nhận khắc phục trong lần review này.

Bằng chứng local: `_evidence/rc3/weekly/`, `_evidence/rc3/weekly-install/` và
`_evidence/rc3/github-review/`. README và hướng dẫn sử dụng áp dụng RC3; báo cáo RC2/G0/G1
được giữ làm lịch sử.
