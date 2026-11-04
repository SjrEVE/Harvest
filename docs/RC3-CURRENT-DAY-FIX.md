# RC3 — sửa thu thập đến ngày hiện tại (04/11/2026)

Nguồn Windows đã cài: `c82c15728c3b85b2ae2668ddb87472655385375f`.
Bộ cài: `D:/Harvest/releases/SanThau-RC3-current-day-c82c157/`.
SHA256 installer: `b6b5086f9b0fc6c78ee6e13b4fee98c04d020515f1c5a2bfcd0eab05b28653a3`.

Lỗi thật: tác vụ chọn đến 04/11 dừng sau TBMT với `INTERVAL_NOT_CLOSED`,
đôi khi kèm `TOTAL_CHANGED`. Đây là ngày đang diễn ra, không phải nguồn chặn truy cập.
Bản vá lấy dữ liệu hiện có của hôm nay cho sáu loại rồi tiếp tục ngày cũ từ mới về cũ.
Hôm nay giữ PARTIAL, không tạo proof COMPLETE; lỗi mạng/CAPTCHA/hạn mức vẫn dừng.

- PASS: hồi quy 644 đạt, 1 skip của Task Scheduler opt-in; lint và secret gate sạch.
- PASS: binary chạy ngoại tuyến 54 hồ sơ/48 proof ngày đã đóng; chạy lại chỉ hỏi nguồn
  cho 6 khoảng hôm nay, không hỏi lại lịch sử đã đủ và không tăng records/versions.
  Các lần quan sát mới của hôm nay được giữ trong lịch sử.
- PASS: cài thực tế, 2.530 tệp chương trình khớp stage; 250 tệp Data Root giữ nguyên hash.
  Trước/sau cài: 70.691 records, 70.691 versions, 71.308 observations, 145 proofs.
- PASS: chạy lại đúng khoảng owner chọn 19/10–04/11 và hạn mức cũ;
  tác vụ `job-9bc0cd58456f`, worker PID 18904. Đã chuyển TBMT → KQLCNT trên nguồn thật.
  TBMT 419 dòng; KQLCNT nhận 3.329 dòng, tổng nguồn thay đổi 3.314 → 3.329 trong lượt.
  Đây là số dòng nhận từ nguồn, không phải toàn bộ đều là hồ sơ mới hoặc proof đủ ngày.
- Chưa tuyên bố toàn bộ khoảng thu thập hoàn thành; tiến trình tiếp tục độc lập.
- Quét binary: 21 cảnh báo đã đối chiếu thành chuỗi thư viện/ví dụ công khai,
  0 chưa giải thích; phạm vi chuỗi ASCII, không thay thế kiểm toán bảo mật toàn bộ.

Bằng chứng tại `_evidence/rc3/collection-error-20261104/`; thư mục bàn giao có manifest,
SHA256SUMS, kết quả kiểm thử, ảnh cửa sổ thử ngoại tuyến và biên nhận cài đặt.
Không thêm database/server, không đổi schema, không xóa dữ liệu gốc.
