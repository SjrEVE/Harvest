# RC3 — trạng thái source và bản cài ngày 05/11/2026

Đây là báo cáo theo bằng chứng, không phải xác nhận toàn bộ Master Spec đã hoàn thành.
User yêu cầu hoàn thiện phần có thể thực hiện và push lên GitHub; source được chốt
trên nhánh `codex/rc3-completion`, từ base `68fa8279845575deb7221c6fad62a9bd40d9baca`.
Kho thật, phản hồi nguồn, tệp hàng đợi và installer nằm ngoài Git.

## Những phần đã hoàn thiện

- Tác vụ tuần lấy lại ngày vừa khép nếu lượt lịch sử kéo dài qua nửa đêm; chỉ chốt
  COMPLETE khi lượt lấy mới đủ bằng chứng. PARTIAL không còn hiển thị như chưa truy cập.
- Gom việc dựng bản tra cứu sau mỗi nhóm tối đa sáu lượt con và khi kết thúc tác vụ.
  Giữ publish nguyên tử, kiểm tra integrity/đối soát và dấu dữ liệu chưa cập nhật.
- Chia khoảng sớm khi tổng nguồn bão hòa hoặc hạn mức không đủ. Lượt sau chỉ bỏ qua
  khoảng có proof; bộ đếm giữ cả yêu cầu thăm dò khoảng cha, các lần retry và dòng nhận.
- Trạng thái trực tiếp phân biệt khởi động, kiểm tra kho, thu thập, dựng bản tra cứu,
  đang dừng, đã dừng và mất kết nối. Nút điều khiển phụ thuộc phản hồi mới của worker.
- Sửa polling chậm bị bỏ phản hồi và lỗi trang Kho dữ liệu giữ trạng thái/số hàng đợi
  cũ từ lúc mở app. README/hướng dẫn vận hành đã khớp thứ tự mới nhất trước và cơ chế
  cập nhật bản tra cứu theo nhóm.

## Source khác bản cài ở đâu

Bản RC3 trạng thái đã cài khoảng 09:49 ngày 05/11 gồm các sửa ngày, snapshot,
hạn mức và trạng thái thu thập. Installer:
`SanThau-Data-Studio-Setup-0.0.1-RC3.exe` trong thư mục local
`D:/Harvest/releases/SanThau-RC3-status-20261105/`.

SHA256: `a5f912ce9e1aa18c7c17143e21a3eba1eb254eaca34026b00e8daa2dfd834765`.
Manifest được tạo khi working tree còn sửa đổi, nên base SHA không tự đại diện
cho input build; dùng các hash file trong manifest để đối chiếu.

Đối chiếu sau cài ghi nhận giữ nguyên 181.733 records, 181.735 versions, 198.969
observations, 237 proofs, 199.524 raw objects và cả 18 tài liệu hàng đợi. Lần kiểm
tra 13:41 đối chiếu hai executable đang cài vẫn khớp hash manifest.

**Source lần chốt này có thêm sửa `desktop/qml/Main.qml` cho trang Kho dữ liệu.**
Đây là input runtime duy nhất khác gói lúc 09:49. Chưa build/cài đè phần bổ sung
này vào ứng dụng đang hoạt động. Không dùng kết quả nghiệm thu binary cũ để xác
nhận sửa QML mới; sửa mới được kiểm thử Qt và render ngoại tuyến riêng.

## Kiểm chứng

Kết quả toàn suite cuối, lint và hash bằng chứng được ghi tại
[`docs/evidence/rc3-20261105/verification.json`](docs/evidence/rc3-20261105/verification.json).
Log/XML đầy đủ nằm local ở `_evidence/rc3/completion-push-20261105/`.

Hồi quy toàn bộ trên source cuối: **669 passed, 1 skipped, 0 failed/errors**, 187,05s.
Bài skipped tạo Scheduled Task đã chạy riêng và đạt. Ruff trên các file Python
sửa/thêm và `git diff --check` đạt.

| Hạng mục | Kết quả và phạm vi |
|---|---|
| Lỗi trạng thái trang Kho dữ liệu | RED tái hiện lỗi trước sửa; 6 bài Qt/polling liên quan đạt sau sửa. Đã render và xem ảnh 1080×620 và 1380×900. |
| Scheduled Task thật trên Windows | Bài tạo–đọc–xóa chạy riêng với cờ opt-in: 1 passed; kiểm tra xác nhận tác vụ thử đã bị xóa. Suite mặc định vẫn bỏ qua bài này. |
| GUI đã cài | Quan sát khoảng 13:38: trang Thu thập có tiến độ trực tiếp, thời điểm nhận trang, cảnh báo bản tra cứu cũ và nút điều khiển phù hợp. Không bấm dừng/tạm dừng tác vụ thật. |
| Binary đã đóng gói lúc 09:49 | Bằng chứng trước đó: worker thật chạy nguồn giả 6 loại/1 ngày, 216 records, FINISHED/COMPLETED, warehouse/serving quick_check=ok. Không phải nguồn thật. |
| Snapshot theo nhóm | Fixture 2.000 bản ghi nền + 48 mới: 13 build xuống 2, 5,477s xuống 1,090s thời gian dựng; mỗi phía một phép đo. Không suy rộng thành tăng tốc tương ứng trên kho thật. |
| Review | Tự rà source/diff và kiểm thử; không có reviewer độc lập trong lượt chốt này. |

## Kho đang chạy — chỉ đọc, không thay đổi dữ liệu

Lúc 13:41 ngày 05/11 theo giờ Việt Nam: worker trả ping, COLLECTING; 1 tác vụ active,
2 queued, 15 done. Kho có 219.465 records, 219.467 versions, 241.879 observations,
281 proofs. Tác vụ 01–18/10 đang xử lý KHLCNT trong tuần 08–14/10. Các số này là
ảnh chụp tại thời điểm kiểm tra, sẽ tiếp tục thay đổi.

Ngày 04/11 vẫn PARTIAL cho sáu loại. `deep_captures`, `documents`, `lots`, `goods_rows`
đều bằng 0. Hai tác vụ chờ có cùng phạm vi 01/01/2022–04/11/2026; giữ nguyên các
tác vụ do người dùng tạo. Lượt chốt này không phát sinh canary, job live, tăng page
size/nhịp, thay kho hay khởi động lại worker.

## Giới hạn còn lại

- **Chưa sửa:** persistence độ phủ chuẩn hóa thời gian về giây; chưa hỗ trợ an toàn
  các khoảng nhỏ hơn một giây. Runner mặc định dừng chia ở MINUTE, không phải MILLISECOND.
- **BLOCKED/chưa có bằng chứng:** deep live, HSMT, hàng hóa và dữ liệu chi tiết
  tổng quát; thử endpoint/canary mới cần phạm vi và hạn mức riêng.
- **BLOCKED:** chứng chỉ Authenticode và quyết định giấy phép Qt/PySide6 của owner.
- **NOT_TESTED:** Windows sạch, mọi màn ở DPI hệ thống, tốc độ toàn kho sau tối ưu,
  bản cài chứa sửa QML bổ sung của lượt chốt này.
- Snapshot vẫn dựng toàn bộ kho. Gom nhóm giảm số lần dựng; dữ liệu tra cứu có thể
  cũ trong lúc một nhóm đang chạy. Benchmark backend không chứng minh UI end-to-end.

Chi tiết hai đợt sửa: [tiếp tục RC3](docs/RC3-CONTINUATION-20261105.md),
[trạng thái và nâng cấp cục bộ](docs/RC3-STATUS-CLARITY-20261105.md).
Báo cáo RC2/G0/G1 và các kết quả ngày 04/11 là lịch sử, không thay thế báo cáo này.
