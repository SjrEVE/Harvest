# Cài lại local và bật thu thập bổ sung — 06/11/2026

## Phạm vi và trạng thái

Theo yêu cầu tiếp theo của chủ máy, đã cài bản Windows mới vào
`C:/Users/media/AppData/Local/Programs/SanThauDataStudio`, giữ kho
`D:/Harvest/SanThauData`. Không push/merge hoặc triển khai server.

Source: nhánh `codex/rc3-completion`, base commit
`641a48a79e136b6998972ca1583338cd25feab81`, có thay đổi chưa commit.
Hash từng input và binary nằm trong release-manifest.json; base commit không
đại diện đầy đủ cho source đã đóng gói.

**Đã cài, có dữ liệu thật vào warehouse và đã kiểm chứng truy vấn đọc của app.**
Không kết luận toàn bộ lịch sử đã thu thập xong.

## Cấu hình thực tế

- Tác vụ `full-update-35382214833f`: sáu nhóm dữ liệu danh sách, theo tuần,
  từ 01/01/2022 đến hết ngày 06/11/2026 theo giờ Việt Nam.
- Giữ nguyên dữ liệu và coverage proofs; khoảng đã chứng minh đầy đủ được bỏ qua.
  Phần chưa có proof được kiểm tra lại, có thể gặp lại record đã lưu nhưng không
  nhân bản định danh dữ liệu.
- Hai yêu cầu cũ `job-e944ee5a5987` và `job-b8b9a4a10471` được giữ nguyên bytes/hash
  dưới `jobs/superseded`; không xóa. Tác vụ cũ được PAUSE qua IPC, đợi trạng thái
  PAUSED rồi yêu cầu worker thoát hợp tác sau khi xuất xong snapshot.
- Bật automatic_public_deep: HSMT, REOFFER, DETAIL chỉ cho TBMT/CGTTRG đã xác minh.
  Mỗi đợt tối đa 30 request, dùng worker/writer và host pacing hiện có, >=3 giây/request.
  Chạy xen giữa các tác vụ danh sách con và khi hàng đợi rảnh; tạm dừng theo người dùng.
- Checkpoint bỏ qua capture còn mới; đổi listing hash hoặc quá hạn capture sẽ làm mới,
  giữ lịch sử. Lỗi được lưu và có cooldown; endpoint bị từ chối truy cập dừng trong
  worker hiện tại. Không bypass CAPTCHA/WAF.
- Phạm vi lịch sử có ngày kết thúc cụ thể. Không tuyên bố đã bật lịch cập nhật vô hạn
  cho các ngày sau 06/11; automatic deep tiếp tục chạy cho các record có trong kho.

## Bảo toàn kho và bộ cài

Trước và ngay sau bảo trì: 286.047 source_records, 286.063 phiên bản, 317.418
observations, 311.956 raw_objects và 402 coverage_proofs. Các số này giữ nguyên.
Migration v2 thêm bảng checkpoint deep_tasks; không đổi/xóa dữ liệu v1.

Bản sao lưu trước nâng cấp:
`D:/Harvest/backups/before-deep-auto-20261106-0021`.
258 file, 2.842.014.668 byte; danh sách/size file khớp, 34 mẫu raw có hash khớp,
warehouse gốc và bản sao cùng SHA256
`01de5dd45d3dad367b3ffa16dacd219723c7aa1ee42f4d34b49113a2ee9d2449`.

Bộ cài: `D:/Harvest/releases/SanThau-RC3-deep-auto-20261106/SanThau-Data-Studio-Setup-0.0.1-RC3.exe`.
49.541.311 byte; SHA256
`e26cb3cf0512a4a3f1270ea5acf515788c1e0e81133b1cea35c2c0f1a1e35712`.
Installer exit 0; hashes hai exe và QML đã đối chiếu với manifest. Runtime đã trả
capabilities public-cgttrg-deep-v1 và automatic-public-deep-v1.

## Bằng chứng theo chức năng

| Chức năng | Nguồn/mẫu | Kết quả đã quan sát | Kết luận |
|---|---|---|---|
| Auto worker bình thường | Binary mới, 3 mẫu đã lưu định danh, 9 API requests thật | 9 captures; chạy lại 0 request mới, checkpoint và số dòng không đổi | PASS trong phạm vi mẫu |
| Raw/hash trong kho chính | Đợt đầu 10 TBMT × 3 lane | 30 phản hồi/captures FETCHED, 30 hash kiểm đúng | PASS |
| Lots/goods kho chính | 10 HSMT CGTTRG | 3 lô, 54 hàng; Long Đất 3/28, đúng 8/13/7; còn lại 26 hàng không lô | PASS cho đợt đã quan sát |
| Goods không chia lô | Mẫu binary riêng 1 hàng và 38 hàng | Không tạo lô giả; tổng ba mẫu 67 hàng | PASS mẫu độc lập |
| Tài liệu | bidPlan.decisionFileId/decisionFileName | Kho chính 10 tham chiếu PLAN_DECISION; 0 bytes/file tải | PARTIAL: REFERENCE_ONLY, chưa xác minh đường tải |
| Người chào giá | Ba mẫu binary; mẫu IB2600133960 | 5 OFFEROR dù 10 dòng báo giá; không gán thuế/liên danh. Đợt đầu kho chính 0 người chào giá | PASS parser/persistence trên binary; không bịa số người ở kho chính |
| Quan hệ | planNo/planVersion trong phản hồi kho chính | 10 PLAN_HAS_NOTICE, provenance gắn capture và mã | PASS cho đợt đã quan sát |
| Refresh/history/cross-lane | Fixture và dịch vụ/binary thường | Giữ lịch sử; không xóa hàng của lane khác; rerun không nhân bản | PASS; chưa giả lập thay đổi trên nguồn live |
| App read | Snapshot main, 10 hồ sơ/30 captures | Backend record_detail và deep_queue đọc được 3 lô/54 hàng/10 tham chiếu/30 FETCHED; Long Đất đúng 3/28 | PASS tại 00:31 ngày 06/11; worker tiếp tục đợt sau |
| Toàn bộ lịch sử | 2022–06/11/2026, sáu family | Tác vụ đang chạy, checkpoint/proofs được giữ | IN_PROGRESS, chưa đủ toàn bộ |
| HSMT ngoài CGTTRG, KQLCNT_FULL, BOQ | Chưa có hợp đồng request được xác minh | Không bật đường chưa kiểm chứng; không lặp lại endpoint HTTP 400 | UNVERIFIED/BLOCKED |

## Kiểm thử và giới hạn

690 passed, 1 skipped (test tạo/xóa Scheduled Task thật cần opt-in); Ruff và
secret scan đạt, diff whitespace đạt. Tự review, không có reviewer độc lập.
Tổng đợt kiểm chứng giới hạn trước khi bật kho chính: 23 request, trong ngưỡng 30
ban đầu. Thu thập rộng trên kho chính chỉ bật sau yêu cầu tiếp theo của chủ máy.

Snapshot phục vụ app vẫn dựng lại toàn kho, gây một khoảng chờ giữa các đợt.
Không ngắt worker trong lúc xuất snapshot. Raw JSON được canonicalize/redact theo
chính sách hiện có, không phải byte-for-byte bản HTTP wire.

Evidence local: `_evidence/deep-install-auto-20261105/`: full.xml/full.log,
compiled-auto/acceptance-0.json và acceptance-1.json, backup-verification.json,
installed.log, installed-hashes.json, collection-setup.json,
main-raw-verification.json, installed-progress.json và verification.json.
Không commit raw của kho thật.

Lúc kiểm chứng app, bảng trạng thái còn 7.257 cặp hồ sơ/lane chưa lấy; đây là
số ở snapshot đó, không phải số hồ sơ duy nhất và tiếp tục thay đổi khi cào.
App và worker đã cài đang chạy (PID quan sát: GUI 26872, worker 5224).
Không dùng ảnh chụp hoặc Computer Use để xác nhận thao tác màn hình; kiểm chứng
qua backend thật dùng cùng serving và mã nguồn khớp manifest của bản cài.

Quan sát tiếp lúc 00:32:43: warehouse tăng lên 286.054 bản ghi, 84 captures thành công,
10 lô, 78 hàng, 28 tham chiếu tài liệu và 28 quan hệ. Một HSMT của IB2600599535
trả HTTP 200 nhưng body không phải JSON (730 byte); trạng thái FAILED và receipt
được giữ, không coi là nguồn rỗng/đã lấy. Tác vụ tự động giữ cooldown ít nhất 60 phút
trước lần chọn lại; không thử nóng lặp lại ngay. Nội dung body lỗi không được lưu
bởi FetchLane hiện có nên không kết luận đó là WAF, lỗi ứng dụng hay lỗi dữ liệu.
Các hồ sơ khác vẫn được xử lý. Xem main-failures.json và final-live-state.json.
