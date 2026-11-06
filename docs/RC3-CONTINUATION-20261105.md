# Tiếp tục RC3 — 05/11/2026

Phạm vi: bản bàn giao `D:/Harvest/HANDOFF-RC3-20261105.md`; base `68fa8279845575deb7221c6fad62a9bd40d9baca`, nhánh `codex/rc3-completion`.

- [x] 1. Chốt lại ngày đã khép bằng lượt lấy mới; trạng thái IPC và coverage trung thực.
- [x] 2. Gom nhịp dựng snapshot, đo bằng fixture độc lập, giữ publish nguyên tử và stale.
- [x] 3. Chia cửa sổ sớm theo tổng/hạn mức; đối soát counters cả khoảng cha.
- [x] Hồi quy, tự rà diff, ghi bằng chứng và giới hạn.

Quyết định: dùng repo/nhánh hiện có theo yêu cầu bàn giao; không tạo worktree/session/subagent, không commit/push/PR/merge/deploy hoặc cài lại. Giữ kho thật và bản cài nguyên trạng. Test theo skill test-driven-development.

Giao điểm: việc 1 và 2 dùng chung weekly/jobs; việc 3 dùng runner và báo cáo jobs. Coverage proof/can_skip là điều kiện duy nhất để bỏ qua khoảng; snapshot chỉ là bản đọc, không cấp proof. Tất cả sửa đổi và benchmark thực hiện ngoại tuyến.

Kiểm tra đầu phiên: đúng HEAD, working tree sạch; IPC ping OK, active=0, queued=0. Tác vụ cũ TERMINATED/PARTIAL nhưng progress COLLECTING. Bằng chứng `_evidence/rc3/continuation-20261105/initial-worker-state.json`.

Việc 1: 5 kiểm thử tái hiện thất bại đúng lỗi trước sửa; hồi quy 177 passed / 71,74s. Chặn proof cho lượt bắt đầu khi khoảng còn mở, kể cả kết thúc sau nửa đêm. Sau lượt lịch sử, chạy lại ngày vừa khép; resolver bỏ qua các loại đã có proof. PARTIAL/FAILED/INTERRUPTED được hiển thị PARTIAL, không thay đổi proven/gaps/can_skip. IPC và QML không ghép tiến độ cũ vào tác vụ đã kết thúc/tạm dừng.

Quyết định việc 2: gom mỗi sáu lượt con trên cùng thread writer, lần cuối sau khi lưu kết quả parent. Giữ nguyên bộ dựng, đối soát, kiểm tra integrity và publish nguyên tử. Đổi lại dữ liệu hiển thị có thể cũ đến hết nhóm sáu lượt; marker stale và tiến độ IPC cho biết rõ. Không thêm writer/build nền hoặc sửa cơ chế .tmp.

Việc 2: kiểm thử tái hiện 13 publish thay vì 2 trước sửa. Sau sửa 41 passed / 26,02s. Fixture 2.000 bản ghi nền + 48 mới / 12 lượt con: trước 13 build / 5,477s dựng / 6,144s tổng; sau 2 build / 1,090s dựng / 3,038s tổng. Cả hai quick_check=ok và records=FTS=record_fields=2.048. Một phép đo mỗi phiên bản, không suy rộng thành mức tăng tốc kho thật; `tools/bench_weekly_snapshots.py` tái hiện được trong thư mục tạm độc lập.

Việc 3: chia ngay sau trang đầu khi tổng bão hòa; dự báo số trang còn cần (kể cả trang rỗng kết thúc khi tổng chia hết page size) theo page/record/request/time budget. Rule chỉ DAY thì dừng sau probe khi ngân sách còn lại không đủ, để lượt tuần tiếp lấy phần thiếu bằng budget mới. Không tăng page size/nhịp hoặc đổi endpoint. Kho thật chỉ được đọc precision: sáu rule hiện lưu MILLISECOND/canary_verified=1; không xác minh nguồn mới trong phiên này.

Rerun của fixture hạn mức 8 trang chứng minh 3/24 giờ ngay lượt đầu (trước sửa 0), sau đó chỉ lấy gaps; kết thúc vẫn 240 records/240 versions. Fixture bão hòa giảm lượt khoảng cha từ 25 xuống 1; tổng báo cáo giữ cả probe: 250 observations = 240 NEW + 10 UNCHANGED, 49 requests. `rows` là dòng đã tách để lưu, `rows_received` là dòng nguồn; counters tổng cộng cả cha và con; request bị từ chối trước HTTP do budget không bị đếm thành request đã gửi.

Tự review (không subagent theo yêu cầu): phát hiện STOP ngay sau một child đã chứng minh có thể khiến parent báo COMPLETE khi còn child chưa chạy. Đã tái hiện RED, sửa điều kiện tất cả children và dùng outcome cả cây khi xét kết quả tác vụ. Các bộ kiểm thử bổ sung kiểm tra API HTTP giả, budget DAY-only và bộ đếm run_tasks. Không tuyên bố giảm 1.133 request lặp ở kho thật từ các fixture này.

Review vòng đời tài nguyên: early split đóng generator ngay, đồng thời page source đóng HTTP client do factory tạo cho từng cửa sổ (không đóng client được caller cấp chung vì FetchLane giữ ownership). Kiểm thử client thật với HTTP giả tái hiện chưa đóng trước sửa, đạt sau sửa. QML khởi tạo trên Qt thật ở chế độ offscreen: 10 bài kiểm tra UI/counters đạt; đây không phải nghiệm thu thao tác trên bản cài hoặc DPI/Windows VM sạch.

Hồi quy toàn bộ lần đầu phát hiện `test_max_pages_cap_never_completes` còn yêu cầu MAX_PAGES_HIT dù hành vi mới dừng sau trang đầu trước khi chạm cap. Đổi tên/thông số khẳng định để kiểm tra hành vi mới: 1 trang, max_pages_hit=0, WINDOW_DEFERRED_BUDGET_EXHAUSTED, PARTIAL và 0 proof. Các bài kiểm tra chạm cap thực tế ở runner giữ nguyên. Đây là thay đổi expectation có chủ ý theo yêu cầu giảm tải, không nới điều kiện cấp proof.

## Phạm vi giao hàng và phần chưa kiểm chứng

- Source được sửa trực tiếp trên nhánh hiện có; chưa commit, push, PR, merge, build/cài lại hoặc deploy. Không sửa repo Linux. Manifest nguồn sửa trong `source-manifest.json` ghi SHA256 từng file, không dùng SHA runtime cũ để chứng nhận source mới.
- Bản cài vẫn dùng runtime c82c157; ngày 04/11 trong kho thật chưa được chạy lại bằng source mới. Không tuyên bố đã chốt ngày này trên dữ liệu thật.
- Kiểm tra chỉ đọc lúc 08:20 ngày 05/11 (giờ Việt Nam): worker ping OK, active=queued=0; 173.218 records, 173.220 versions, 190.094 observations, 230 proofs, deep_captures=documents=0. Không có job live mới trong phiên.
- NOT_TESTED: tốc độ kho thật sau nâng cấp, GUI thao tác trên bản cài mới, installer và clean Windows VM.
- BLOCKED theo bàn giao: deep live/endpoint chưa xác minh; canary mới hoặc tăng page size/nhịp cần phạm vi owner duyệt; Authenticode cần certificate, Qt licensing cần owner. Không thay đổi cấu hình AI.
- Đánh đổi còn lại: snapshot vẫn dựng toàn bộ; chỉ giảm số lần dựng. Bản hiển thị có thể cũ trong một nhóm tối đa sáu lượt con, marker stale và tiến độ IPC luôn phân biệt. Dự báo thời gian là ước lượng cho việc chia khoảng; budget thực vẫn kiểm tra từng request/retry, không bảo đảm một khoảng sẽ hoàn tất trong thời gian dự báo.

Bằng chứng nằm tại `_evidence/rc3/continuation-20261105/`: các log RED/GREEN theo phần, benchmark trước/sau, kết quả hồi quy XML/log, lint, manifest source và kiểm tra IPC/kho chỉ đọc.

## Kết quả cuối

- PASS: toàn suite trên source cuối **659 passed, 1 skipped, 0 failed/errors**, 177,77 giây. `final-regression.xml` và `final-regression.log`.
- SKIPPED: `tests/integration/test_scheduler.py::test_create_query_delete_roundtrip` yêu cầu bật riêng `SANTHAU_SCHEDULER_TEST=1` vì tạo Scheduled Task thật; không bật trong batch này.
- PASS: Ruff trên toàn bộ file Python sửa/thêm; `git diff --check` không có lỗi khoảng trắng.
- PASS: 31 bài targeted sau vòng review; kiểm thử xuyên nửa đêm, coverage, counters, snapshot, IPC và QML nằm trong lượt full cuối.
- Review là tự rà diff của tác giả, không có reviewer độc lập. Không còn lỗi Critical/Important được phát hiện trong phạm vi rà; chưa đủ bằng chứng để tuyên bố nghiệm thu bản cài hoặc nguồn thật.

Lệnh hồi quy: `.venv/Scripts/python.exe -m pytest --tb=short --junitxml=_evidence/rc3/continuation-20261105/final-regression.xml`.
