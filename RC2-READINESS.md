# Săn Thầu Data Studio — RC2

Cập nhật 04/11/2026, Asia/Saigon. Repo local `D:\Harvest\santhau-data-studio`.
Checkpoint nền: `f1c6fc5971038a4cc808b91f92a4153ae17c926a` (`master`). Các sửa RC2 hiện nằm trong working tree.

## Kết luận và phạm vi

Đây là vòng rà soát/sửa toàn bộ các nhóm code của Data Studio và hoàn thiện RC2 dùng thử tại máy.
Không phải chứng nhận hoàn tất toàn bộ Master Spec v1.3 hoặc nghiệm thu phát hành thương mại.
Không push, tạo remote, publish, deploy, dùng token A1 hay gọi nguồn đấu thầu thật.

Đã rà theo các nhóm domain, collectors, storage, worker/IPC, Windows runtime, desktop, packaging,
test fixtures và công cụ phát triển. Có 49 module Python trong `src`, cùng QML và hai script packaging.
Review do agent chính thực hiện; phiên này không có công cụ reviewer subagent. Không suy kết quả
test thành bảo đảm không còn lỗi hoặc chứng minh dữ liệu live.

## Lỗi và khoảng trống đã xử lý

| Nhóm | Vấn đề thực tế | Thay đổi |
|---|---|---|
| Worker → GUI | Job xong không tự công bố serving, giao diện vẫn trống/cũ | Khởi tạo và công bố snapshot sau job; đánh dấu stale trong lúc thu thập; phục hồi raw khi worker khởi động |
| Snapshot | Publish lỗi chỉ ghi marker ở warehouse mà GUI không đọc; snapshot hỏng gây exception | Marker cạnh serving, giữ snapshot cũ; báo lỗi đọc rõ ràng; transaction đọc nhất quán và copy theo batch |
| Data Root | Nút chỉ validate, không áp dụng/lưu lựa chọn | Chọn kho thật, chuyển backend/supervisor, chuyển mutex và lưu preference; chặn đường dẫn trống/UNC/thư mục chương trình |
| Queue | JSON sai kiểu làm chết vòng worker; đổi timestamp lách duplicate job_id | Reject document hỏng, kiểm filename, định danh job độc lập timestamp; fsync trước công bố request |
| Backup | Danh sách raw đọc trước snapshot nên có thể thiếu object vừa commit | Liệt kê object từ snapshot đã chụp; backup vào thư mục mới, không ghi đè đích có sẵn |
| Restore | Chưa giữ writer lock trước ghi raw; manifest có thể trỏ ra ngoài backup | Giữ lock suốt restore, validate hash/length/đường dẫn, đóng store và đánh dấu snapshot cần rebuild |
| Segment read | Reader backup có thể chạy tail recovery của writer | Thêm chế độ read-only, không recover/truncate từ reader |
| Segment recovery | Khởi tạo thư mục rỗng trước recover làm mất kho sau process death giữa retire/promote | Recover trước mkdir/open_index; phục hồi đúng directory và bảo toàn bytes |
| Segment safety | Marker hỏng có thể khiến cleanup xóa thư mục không liên quan | Validate chính xác staging/retired path trước rename/delete, từ chối marker không hợp lệ |
| Reconcile | Scan thiếu/chạm cap vẫn kết luận record mất khỏi nguồn | Chỉ ghi missing khi lần scan mới chứng minh COMPLETE; job trả đúng PARTIAL/BLOCKED |
| Credentials | Raw/normalized chưa thực sự qua redaction | Redaction deterministic trước hashing/extraction/deep capture, policy 2; test raw và DB không giữ credential giả |
| TLS | Code hạ OpenSSL xuống SECLEVEL=1 | Giữ TLS ≥1.2, xác minh certificate/hostname và cipher policy mặc định |
| Pacing | Limiter chỉ dùng chung trong một process | Host lease/cooldown dùng chung giữa các worker/Data Root; không giả danh Chrome trong User-Agent |
| Canary | Proof adapter cũ làm adapter mới thành COLLECTION_PROVEN | Ràng buộc proof với adapter SHA; worker tôn trọng trạng thái nguồn BLOCKED/DISABLED |
| IPC | Response chậm có thể làm client chờ vô hạn | Giới hạn thời gian đọc và bỏ flush chờ peer không giới hạn |
| Maintenance UI | Settings chỉ hiển thị, chưa có thao tác sử dụng | Backup/restore/verify/export/scheduler qua collector process riêng, có kết quả; chặn đóng GUI khi tác vụ đang chạy |
| Search UI | Repeater nhiều kết quả tràn khỏi màn | ListView cuộn/virtualize và elide; xóa kết quả kho cũ khi đổi kho |
| Scheduler | Chỉ khởi động worker trống | Lần chạy định kỳ xếp INCREMENTAL cho family có rule xác minh và nguồn được phép; skip sạch nếu worker đã chạy |
| Installer | Lệnh Unix redirect trên cmd; một GUI xóa marker trước worker; worker không nhận tín hiệu | Pascal handler đúng chữ ký, marker broadcast, GUI/worker cùng giữ runtime mutex; chờ đóng mềm và từ chối nếu timeout |
| Artifact | Không nối binary với source bẩn hiện tại | Capture SHA256 từng input trước build, seal sau build; từ chối nếu input thay đổi; hash hai exe và installer |

Các ca lỗi trọng yếu được chạy đỏ trước sửa rồi xanh sau sửa. Hai test cũ cũng được sửa đúng mô
phỏng: nguồn xóa record phải cập nhật total và phân trang; test chuyển policy phải chủ động đặt 1 → 2.
Không nới điều kiện COMPLETE hoặc hạ yêu cầu kiểm thử để làm xanh suite.

## Kiểm chứng mới

| Kiểm tra | Kết quả | Bằng chứng |
|---|---|---|
| Full suite | **520 PASS, 0 FAIL, 0 SKIP**; 102.87 giây ở source cuối, khi build chạy song song | `_evidence/rc2-final/tests-final.log`, `tests-final.xml` |
| Task Scheduler thật | **PASS** tạo/query/xóa task kiểm thử; không để lịch production bật sẵn | `SANTHAU_SCHEDULER_TEST=1`, test trong full suite |
| Ruff | **PASS** | `uv run --frozen --offline ruff check src tests tools` |
| Secret scan production code/tooling/packaging | **PASS**, 0 findings ở gate mode | `_evidence/rc2-final/secret-gate.json` |
| DDL contract | **43 PASS** | `tools/ddl_contract_check.py --sql docs/ddl/g0_b_proposed.sql` với `PYTHONUTF8=1` |
| Source GUI Windows | **PASS smoke**; ảnh 1180×760, font tiếng Việt và nút maintenance | `_evidence/rc2-final/settings-source.png` |
| Native build | **PASS** `BUILD_OK`, `ASSEMBLE_OK`, `MANIFEST_OK seal` | `_evidence/rc2-final/package.stdout.log` |
| Installer compile + source/artifact identity | **PASS**, hash cả hai exe và installer khớp manifest | `build/release-manifest.json`, `_evidence/rc2-final/artifacts.json` |
| Packaged GUI trên Windows | **PASS**, exit 0, ảnh 1180×760 đã xem | `_evidence/rc2-final/settings-packaged.png`, `packaged-gui.log` |
| Packaged backup / verify / export / restore | **PASS**, 90 raw object, 78 record, integrity OK, 0 FK lỗi | `_evidence/rc2-final/installer-lifecycle.json` |
| Cài / cài đè / gỡ khi GUI và worker đang mở | **PASS_LOCAL_WINDOWS**, exit 0; cả GUI và worker thoát 0 | `_evidence/rc2-final/lifecycle-final.log`, `lifecycle-exit.txt` |
| Giữ Data Root sau gỡ | **PASS**, 78 → 78 record, sentinel còn nguyên; file chương trình còn lại = 0 | `_evidence/rc2-final/installer-lifecycle.json` |

Không chạy lại benchmark ingest 1M hoặc benchmark nhiều giờ. Số 1M ở báo cáo RC cũ vẫn là bằng
chứng lịch sử, không được ghi như số đo mới của vòng này.

## Điều kiện ngoài phạm vi kiểm chứng local

- **BLOCKED_EXTERNAL**: chữ ký Authenticode và quyết định/full license texts cho phân phối Qt/PySide6.
- **NOT_TESTED**: Windows 10/11 VM sạch, máy khác, restore sang máy thứ hai.
- **NOT_TESTED / giữ gate**: live canary sáu family, deep live và nguồn cần session. Không tự bật nguồn hoặc dùng credential.
- **NOT_VERIFIED**: đã thu hồi token dashboard A1 hay chưa; không có bằng chứng mới từ owner.
- **NOT_IMPLEMENTED trong RC2**: import định dạng kho harvester cũ, toàn bộ filter nâng cao/heatmap,
  thao tác canonical merge/split thủ công. Restore hiện nhận backup Data Studio. Schema hoặc unit test
  không được dùng để gọi những thao tác này là đã triển khai UI đầy đủ.
- Redaction mới áp dụng cho ingest mới; không tự sửa/xóa raw lịch sử đã có trong một kho cũ.
- Lần nâng cấp từ bản spike cũ nên dừng worker trước. Bản cũ không có runtime shutdown protocol mới.

## Nguồn và artifact

`build/source-manifest.json` lưu checkpoint nền, trạng thái working tree và SHA256 từng input.
`build/stage/build-provenance.json` ràng buộc hai executable với snapshot source đó.
`build/release-manifest.json` bổ sung SHA256 installer khi đóng gói hoàn tất. Đây là cách định danh
source thực tế của bản build; không gắn nhầm binary mới vào commit nền còn thiếu các sửa working tree.

Operator guide: `docs/operator-guide.md`. Báo cáo trước vòng sửa: `_evidence/recheck-2026-11-03/STATUS-RECHECK.md`.

## Artifact bàn giao

- Installer: `build/inno/SanThau-Data-Studio-Setup-0.0.1-RC2.exe` — **49,111,461 bytes**.
- SHA256 installer: `3a7c0a6f04969ac7ddf1e878308a3f7501adbe2ed0141a0958770fd270bc5af6`.
- GUI: `build/stage/SanThauDataStudio.exe` — 18,108,416 bytes.
- Collector: `build/stage/SanThauCollector.exe` — 17,924,096 bytes.
- Cả ba file xác nhận **NotSigned**. Không gọi bản này là release thương mại đã nghiệm thu.
- Có thể chạy GUI trực tiếp trong `build/stage`, giữ nguyên cả thư mục. Bộ cài thử nghiệm được gỡ
  sau bài test; không để worker hoặc lịch thu thập production chạy ngầm.

Lần kiểm tra lifecycle đầu bắt trạng thái `unins000.exe` quá sớm: uninstaller tự xóa file đó sau
khoảng nửa giây, log Windows đã ghi `Removed all? Yes` và `Need to restart Windows? No`.
Đã sửa harness để chờ tất cả file được dọn và chạy lại đầy đủ cùng installer: **PASS**, không thay
đổi kỳ vọng phải còn 0 file. Giữ nguyên observation/log cũ để không xóa bằng chứng; `package-exit.txt`
của pipeline đầu vì vậy vẫn là 1. Kết quả cuối nằm ở `lifecycle-exit.txt` (=0),
`installer-lifecycle.json` (`PASS_LOCAL_WINDOWS`) và `FINAL-STATUS.json`.
