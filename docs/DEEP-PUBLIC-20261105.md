# Thu thập chi tiết công khai CGTTRG — 05/11/2026

## Kết luận và ranh giới bàn giao

Đã nối nguồn công khai CGTTRG vào **service/queue/worker mặc định**, không chỉ inject
fake fetcher. Đã chạy source worker và binary SanThauCollector.exe mới trên kho tách
biệt, với dữ liệu trả về từ nguồn thật. Warehouse → serving → StudioBackend đều
trả đúng các dòng ghi thực tế bên dưới. Không tuyên bố hỗ trợ mọi hình thức đấu thầu.

**Chưa cài đè lên ứng dụng đang chạy; chưa ghi deep vào D:/Harvest/SanThauData.**
Lần kiểm tra cuối kho chính vẫn có 0 dòng ở sáu bảng deep, migration vẫn là v1;
worker PID 10612 tiếp tục COLLECTING tác vụ job-e944ee5a5987. Lượt này không pause,
kill hay thay binary của worker đó, không Computer Use, không push/merge/deploy.

Source nằm tại D:/Harvest/santhau-data-studio, base
641a48a79e136b6998972ca1583338cd25feab81; các sửa đổi còn chưa commit.
Bản Linux không được sửa trong lượt này.

## Chứng cứ end-to-end

Hai endpoint POST, cùng host muasamcong.mof.gov.vn, dưới
/o/egp-portal-contractor-selection-v2/services/:

- lcnt_tbmcgtt_hsmt, body id=notifyId, processApply=LDT.
- online-reoffer/detail, body id=notifyId. DETAIL và REOFFER có capture riêng;
  đây là snapshot trả về từ endpoint, không tuyên bố đầy đủ mọi vòng chào giá.

Fetcher dùng FetchLane/RequestBudget/HostRequestGate hiện có, TLS kiểm chứng,
không token/cookie; tối thiểu 3 giây/request dùng chung host. Mỗi job kiểm chứng
đặt max_requests=1, max_attempts=1. Production job vẫn có retry/hạn mức hữu hạn.
Access refusal mở circuit breaker endpoint trong worker; không thử lặp cùng đường bị chặn.

| Chức năng | Nguồn / số mẫu | Dòng ở kho kiểm chứng binary | Kết luận / giới hạn |
|---|---|---:|---|
| deep_captures / history | 3 gói, HSMT 3 + REOFFER 3 + DETAIL 1 | 7 captures, 7 history | PASS trong phạm vi mẫu; raw/hash/identity/version/time và provenance được giữ. |
| lots / goods_rows | HSMT 3 gói | 3 lô, 67 hàng | PASS: Long Đất 3/28, máy điện cơ 0/1, KTX 0/38; không nâng mã BP thành lô. |
| contractor_memberships | REOFFER 3 gói; một gói có người chào giá | 5 OFFEROR | PASS: 5 mã tổ chức, không lấy 10 dòng báo giá thành 10 nhà thầu; không tự suy mã số thuế/liên danh/người trúng. |
| documents | bidPlan.decisionFileId/decisionFileName, 3 gói | 3 tham chiếu, **0 file tải về** | PARTIAL: PLAN_DECISION, owner planNo/planVersion rõ; REFERENCE_ONLY, object_sha/media_type/byte_len chưa có. |
| relation_edges | mã planNo/planVersion từ phản hồi, 3 gói | 3 PLAN_HAS_NOTICE | PASS đối chiếu mã trong kho kiểm chứng; truy được capture/hash/path. Plan nodes được seed từ bidPlan đã lưu, không phải một lượt cào KHLCNT live độc lập. |
| Idempotency/checkpoint | source service chạy lại 6 job | 0 request mới; số dòng/history giữ nguyên | PASS; phiên bản/refresh thay đổi được kiểm thử riêng bằng fixture, không sửa phiên bản ở nguồn thật. |
| Cross-lane | DETAIL → HSMT → REOFFER trên binary và fixture refresh | vẫn 3/67/5 | PASS trong các chuỗi đã thử; projection sở hữu theo lane/endpoint, không xoá bảng của lane khác. |
| App read/status | serving mới + backend và QML đóng gói | 7 FETCHED, 2 lane chưa lấy | PASS: NOT_COLLECTED / SOURCE_EMPTY / PARSE_ERROR / BLOCKED / FAILED / STALE tách biệt; capture cũ không bị ẩn. |
| Download binary / E-HSMT attachment | chưa có hợp đồng tải đã xác minh | 0 | BLOCKED ở bước xác minh đường tải; không gán tên/mã file thành file đã tải. |
| KQLCNT_FULL / BOQ và HSMT ngoài CGTTRG | chưa có bằng chứng phù hợp | 0 | BLOCKED/UNVERIFIED; không lặp endpoint từng HTTP 400 và không suy 400 là thiếu quyền. |

Phân bố hàng hóa Long Đất: PP2600474249=8, PP2600474250=13,
PP2600474251=7; 31 dòng Table gồm 3 header và 28 hàng, parent giữ đúng lô.
Mẫu máy điện cơ giữ code Nicolet EDX, qty=1 và specification đầy đủ trong raw row.

Kho tách biệt được seed các target/plan từ phản hồi đã lưu (đường seed ghi rõ
local-saved-source-replay, không cấp coverage proof). Hai notice Long Đất/máy điện
cơ cũng đã đối chiếu raw hiện có trong kho chính: bidForm=CGTTRG, processApply=LDT,
notifyNo/version/id phù hợp với đường fetch mới.

## Raw, lỗi và lịch sử

Raw lưu toàn bộ JSON theo canonical_bytes/redaction có sẵn của kho, gồm NFC và
chuẩn hoá JSON; **không phải bản sao byte-for-byte của HTTP wire**. Sáu response
source-run đều kiểm SHA256 thành công và khớp payload thử đã lưu sau canonicalization.
Những khác biệt trước canonicalization chỉ nằm ở Unicode dấu tổ hợp trong formValue;
notifyVersion/reofferVersion vẫn 00 và nội dung nghiệp vụ/số hàng không đổi.

Form schema/formContent, dòng hàng gốc và header gốc đi cùng provenance; mọi cột
ngoài tập chuẩn vẫn còn. Hàng lỗi giữ raw và PARSE_ERROR; lỗi shape/identity không
được ghi projection sai. Quantity không đọc được giữ raw, không đoán dấu phân cách.
Goods không chia lô dùng lot_id NULL theo schema đã có. Capture lỗi giữ raw/history;
projection cũ được bảo toàn khi parse form thất bại. Refresh không xoá lịch sử.

Migration v2 chỉ thêm deep_tasks cho checkpoint/trạng thái, không thay frozen DDL v1.
Đã kiểm nâng cấp kho v1 có record, giữ nguyên record và foreign keys, cùng rollback
và kiểm checksum migration. Bản cài đóng gói cả hai SQL migration.

## Hạn mức và thử nghiệm

- 6 request API bởi Python service mặc định; 6 job rerun không có request mới.
- 7 request API bởi binary mặc định, gồm một DETAIL để kiểm cross-lane.
- 1 GET trang contractor-selection để tìm hợp đồng download. HTTP 200 nhưng nội
  dung báo portlet contractor-selection-v2 tạm thời không có, không cung cấp route tải.
  Payload đã lưu cũng không có URL tải xác minh được. Dừng khám phá ở đó; không đoán
  endpoint/download token. Đây không phải kết luận server từ chối mọi download.
- **Tổng 14 request / giới hạn 30**, chỉ ba gói đã nêu. Không thử lại result/get 400.
- Hồi quy cuối: **679 passed, 1 skipped**, 328,15s. Skip là Scheduled Task opt-in;
  không bật lại trong lượt deep này. 62 bài targeted đạt; ba contract mẫu thật được
  bật bằng SANTHAU_DEEP_EVIDENCE trong lượt full cuối.
- Ruff toàn src/tests/tools, secret scan và diff whitespace đạt.
- GUI binary smoke exit 0. Offscreen mặc định thiếu font nên không dùng ảnh đó để
  chứng nhận hiển thị; đã render lại QML đóng gói với font hệ thống và dữ liệu kho
  compiled-v1, xem được 7 trạng thái/đếm thực. Chưa nghiệm thu thao tác tương tác
  trên bản cài chính hoặc toàn bộ DPI/responsive; dòng mô tả lane dài còn bị cắt.
- Review là tự rà, không có reviewer độc lập.

## Artifact và bằng chứng

Bộ cài local: D:/Harvest/releases/SanThau-RC3-public-deep-20261105/
SanThau-Data-Studio-Setup-0.0.1-RC3.exe.
SHA256: 87fb6d3cb7a5736a9da6c5e54c3f8e9407e7aa4274a5ea0385e0bffe32a22f25.
Không dùng base SHA để thay cho hash source đang sửa: release-manifest.json ghi
working_tree_dirty và SHA256 từng input; seal và installer đều đã đối chiếu input.

Bằng chứng local, không commit raw:

- _evidence/deep-public-20261105/saved-v1/acceptance-report.json
- _evidence/deep-public-20261105/live-v1/acceptance-report.json, rerun-report.json,
  canonical-comparison.json, payload-differences.json
- _evidence/deep-public-20261105/compiled-v1/acceptance-report.json
- final.xml/final.log, build.log, installer-final.log, gui-smoke.log,
  packaged-qml-queue.png, discovery-receipt.json, portal-page.html

Không cài đè khi worker đang ghi. Bản đang cài vẫn là runtime cũ; capability mới
public-cgttrg-deep-v1 chỉ nằm ở source/build mới đã kiểm chứng. Nút enqueue deep
trên source mới từ chối worker cũ thay vì báo đã xếp một job không thể thực thi.

Ghi chú tiếp nối 06/11: phần trạng thái bản cài phía trên là ảnh chụp trước yêu cầu
cài lại của chủ máy. Đợt cài local và bật tự động sau đó được theo dõi riêng tại
[INSTALL-DEEP-AUTO-20261106.md](INSTALL-DEEP-AUTO-20261106.md).
