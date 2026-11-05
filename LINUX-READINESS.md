# Săn Thầu Collector RC3 — bản Linux chỉ thu thập

## RC3-2 — nghiệm thu hoàn tất ngày 05/11/2026

Bản **0.0.1-RC3-2** đã build và kiểm chứng cho **Ubuntu 22.04/24.04 x86-64**,
phục vụ cài trên Linode. Chưa triển khai lên server của user hoặc gọi E-GP thật từ
server đó. Không biến kiểm thử nguồn giả thành bằng chứng thu thập live.

- Source build: `d33296e36d2d6a7430436f46ee876b651c90091a`, working tree sạch.
- Nhánh `codex/linux-collector`; các commit tài liệu sau build không thay input chương trình.
- [CI 41988456300](https://github.com/SjrEVE/Harvest/actions/runs/41988456300): cả ba job thành công.
- [Tải artifact linux-collector-rc3](https://github.com/SjrEVE/Harvest/actions/runs/41988456300/artifacts/13397317934).
  Cần đăng nhập GitHub; workflow giữ artifact 30 ngày. Gói cũng đã tải về local tại
  `D:/Harvest/releases/SanThau-Collector-RC3-2-Linux-d33296e/`.
- [Bằng chứng kiểm chứng JSON](docs/releases/linux-rc3-2-verification.json).

### Gói và dung lượng

| Gói | Dung lượng | SHA256 |
|---|---:|---|
| `santhau-collector_0.0.1~rc3-2_amd64.deb` | 22.293.706 byte | `99cec83bcc2381c250ea44feaa6e7b604608acc9bba65f26a9473f532456a8fe` |
| `santhau-collector-0.0.1-RC3-2-linux-x86_64.tar.gz` | 29.339.601 byte | `317da951474a892df1bbecbee446884fcd8c32972132e02c2309fe0a242d35fa` |

Tệp chương trình chiếm **75.530.009 byte (~75,5 MB)**, chưa tính metadata filesystem
và dữ liệu thu thập. Gói trước giải nén khoảng 99,95 MB; giảm khoảng 24,4%.
Không có Qt hoặc zstandard trong runtime. Bộ cài không kèm kho thật hoặc credentials.

### Những phần đã hoàn thiện

- Chốt ngày xuyên nửa đêm bằng lượt lấy mới; không cấp proof cho lượt bắt đầu khi ngày còn mở.
- Chia khoảng sớm theo hạn mức; counters gồm khoảng cha/con; dừng trước các khoảng chưa lấy
  không tạo COMPLETE giả. Đóng HTTP client do page source sở hữu.
- Unix IPC có pha chạy và thời điểm nhận trang; không trả tiến độ cũ sau kết thúc.
  Từ chối pause/stop không có target và resume lặp.
- Giữ shutdown requeue, pause/resume, một writer, gzip segments và định dạng kho RC3.
  Không tạo serving, đổi endpoint/page size/nhịp hoặc migration kho.
- Revision mới cho nâng cấp; Installed-Size và dung lượng artifact nằm trong manifest.
  Hướng dẫn Linode chọn Data Root ngoài thư mục cài và chạy sau khi thoát SSH.

### Kết quả và giới hạn

| Hạng mục | Kết quả |
|---|---|
| Collector regression trên Linux | **448 passed, 1 skipped**, 34,97s; skip hành vi khóa/rename đặc thù Windows. |
| Hồi quy portable trên Windows | 444 passed, 5 bài Linux-specific skipped; không thay kết quả Linux. |
| Lint và secret scan | PASS trên source/config/docs thuộc workflow. |
| Deb cài/chạy/cài đè/gỡ | PASS trên container Ubuntu 22.04 và 24.04 sạch, không Python/Qt, network tắt; hash Data Root trước/sau khớp. Cài đè dùng cùng RC3-2, chưa thử mọi bản cũ. |
| Tar cài/chạy/gỡ | PASS với đường dẫn có dấu cách; Data Root ngoài thư mục cài còn nguyên. |
| systemd user service | PASS enable/start/status/restart/stop trên VM Ubuntu 22.04 và 24.04; fixture ngoại tuyến, IDLE, exit 0, dữ liệu còn và không có serving. |
| Đối chiếu gói tải về | PASS hash hai artifact, 97 source input và 691 file standalone. |
| Review | Tự rà của tác giả, không có reviewer độc lập. |
| Linode thật, reboot/đăng nhập lại | NOT_TESTED trên server đích; không có quyền truy cập server trong lượt này. |
| E-GP live từ Linux/Linode | Chưa kiểm chứng; profile RULE_VERIFIED không chứng minh IP server truy cập được nguồn. |
| Deep/HSMT/hàng hóa, độ phủ dưới một giây | Không bổ sung trong RC3-2. Lane chưa xác minh vẫn chặn; persistence chuẩn hóa về giây, runner mặc định chia tới MINUTE. |
| ARM, Alpine/musl | NOT_TESTED/không phải mục tiêu gói này. |

Log/XML/manifest: `_evidence/linux-server-20261105/` và thư mục release local.
[Hướng dẫn cài và chạy trên Linode](docs/linux-collector.md).
Các mục dưới đây giữ lịch sử gói cũ, không thay thế bằng chứng RC3-2.


## Bản vá mới nhất ngày 04/11/2026

Thay thế gói ban đầu bên dưới: nguồn `fcd9a0cc33dcaebae3177d54c02c361d381fc831`,
thư mục `D:/Harvest/releases/SanThau-Collector-RC3-Linux-fcd9a0c/`.
Đã sửa ưu tiên tuần mới và lấy dữ liệu hôm nay cho đủ sáu loại mà không chặn phần lịch sử.
Hôm nay vẫn PARTIAL cho đến khi có đủ bằng chứng sau khi ngày kết thúc.

[CI 41836099431](https://github.com/SjrEVE/Harvest/actions/runs/41836099431):
430 PASS, 1 skip Win32; cài/chạy/nâng cấp/gỡ ngoại tuyến trên Ubuntu 22.04 và 24.04 PASS.
Đối chiếu tải về: 96 đầu vào nguồn và 693 tệp standalone khớp manifest.
Một lượt CI trước hết thời gian chờ 15 giây ở fixture khôi phục; bài kiểm tra đã thêm
chẩn đoán và thời gian chờ có giới hạn 45 giây cho hoàn tất 336 trang giả lập, đồng thời
kiểm tra trạng thái COMPLETED, 672 records và 84 proofs. Không tăng thời gian chờ lệnh điều khiển.
Các giới hạn live Linux/systemd reboot/ARM/Alpine của bản trước vẫn giữ nguyên.

```text
6c266a0d45a40b3591f420ffec7955a6893da66ce805ba7a20397191bd6a321e  santhau-collector_0.0.1~rc3_amd64.deb
03bbaa83d64ca38f90d5d64ed081adf0c63a35193e5601ba6f95054afcd1a599  santhau-collector-0.0.1-RC3-linux-x86_64.tar.gz
```

## Hồ sơ bản đóng gói ban đầu (lịch sử)

Ngày nghiệm thu: 04/11/2026. **Đạt nghiệm thu ngoại tuyến trên Linux x86-64. Chưa nghiệm thu gọi nguồn E-GP thật từ Linux.**

Bản này giữ thu thập sáu loại theo tuần, cập nhật và đối chiếu, dữ liệu gốc, lịch sử,
bằng chứng, tạm dừng/tiếp tục và khôi phục tác vụ. Không có giao diện Qt, dashboard,
tìm kiếm, thống kê kho hoặc tạo `serving.sqlite`. Trạng thái tác vụ vẫn có để vận hành.

## Bộ cài và nguồn

- Source đã build: `3d89ad4a3ae67504b38fbfad9740f94650ed4df4`, working tree sạch.
- Nhánh: `codex/linux-collector`, phát triển từ RC3 Windows `aeca6a5d63d2e8212df7be99e0a87125f2803911`.
- [Build và nghiệm thu GitHub Actions](https://github.com/SjrEVE/Harvest/actions/runs/41794080665): cả ba job thành công.
- Bản `.deb`: `santhau-collector_0.0.1~rc3_amd64.deb`, 26.906.306 byte.
- Bản tar: `santhau-collector-0.0.1-RC3-linux-x86_64.tar.gz`, 35.018.895 byte.
- Bản bàn giao tại `D:/Harvest/releases/SanThau-Collector-RC3-Linux-3d89ad4/`.
- `release-manifest.json` giữ nguyên manifest của CI; `SHA256SUMS` dùng kiểm tra trước cài.
- Các commit tài liệu sau commit build không thay đổi mã chương trình của bộ cài này.

```text
4c70386f38bb587effa12584b481592d01f6870631ffb13a291211a15451e3d8  santhau-collector_0.0.1~rc3_amd64.deb
ad96669a1ca0e7cc34ef12ff3afca991cc693417885135db09bdd6524b768028  santhau-collector-0.0.1-RC3-linux-x86_64.tar.gz
```

## Kết quả nghiệm thu

| Hạng mục | Trạng thái | Bằng chứng và giới hạn |
|---|---|---|
| Lõi thu thập và runtime Linux | PASS | 419 kiểm tra đạt, 0 lỗi, 1 skip; chạy trên Ubuntu 22.04, 29 giây |
| Skip duy nhất | NOT_TESTED trên Linux | Kiểm tra Windows không cho đổi tên tệp đang mở; đã có bài riêng xác minh khóa writer thật trên Linux |
| Khởi tạo profile, xếp tác vụ và ngày Việt Nam | PASS | Test CLI từ mã Python trên Ubuntu; init/enqueue không tự chạy thu thập, không thêm proof giả |
| Sáu loại, tuần, lặp không trùng | PASS | Self-test của binary: 384 records, 384 versions, 384 observations, 192 fetch requests, 48 proofs; lần hai không tăng các số này |
| Pause/resume, SIGTERM và SIGKILL | PASS | Tiến trình Linux thật dùng nguồn giả; khởi động lại tiếp tục tác vụ và dùng lại phần đã có proof |
| Khóa kho và quyền điều khiển | PASS | Khóa writer, socket riêng chủ sở hữu, từ chối listener trùng/người dùng không khớp/yêu cầu sai định dạng |
| Không Qt, không thống kê/serving | PASS | Self-test binary kiểm tra module đã nạp và không có serving; build kiểm tra không kèm thư viện Qt |
| Cài `.deb`, chạy, cài đè, gỡ | PASS | Container Ubuntu 22.04 và 24.04 sạch, không Python/Qt, tắt mạng lúc chạy; hash toàn bộ tệp Data Root trước/sau giống nhau |
| Cài/gỡ tar, đường dẫn có dấu cách | PASS | Chạy binary độc lập; sau gỡ vẫn còn warehouse ở Data Root ngoài thư mục cài |
| Đối chiếu artifact tải về | PASS | SHA256 hai gói, 96 đầu vào nguồn, 693 tệp standalone khớp manifest; đối chiếu nguồn đúng commit |
| Quét thông tin bí mật | PASS trong phạm vi quét | Source/config/logs và 696 thành viên archive, gồm chuỗi ASCII trong binary. 5 cảnh báo đều khớp ví dụ tài liệu CPython/httpx; 0 cảnh báo chưa giải thích |
| Hồi quy Windows | PASS | 636 đạt, 6 skip ở commit triển khai; sau đó chỉ sửa quyền hook và test khóa. Test backup/restore và secret gate đã chạy lại trước checkpoint build |
| Cào E-GP thật từ Linux | BLOCKED | Chưa có phê duyệt canary riêng cho Linux; chưa chạy. Metadata RULE_VERIFIED từ RC3 Windows không phải COLLECTION_PROVEN trên Linux |
| Deep lanes/chức năng cần phiên người dùng | BLOCKED | Không mở khóa lane chưa được xác minh, không giải CAPTCHA, không kèm token |
| Dịch vụ systemd qua đăng nhập lại/reboot | NOT_TESTED | Có unit của tài khoản người dùng; container nghiệm thu không chạy systemd làm PID 1 |
| Máy Linux vật lý, ARM, Alpine/musl | NOT_TESTED | Chỉ nghiệm thu Ubuntu x86-64 nêu trên; không tuyên bố hỗ trợ mọi bản Linux |

Không có FAIL còn mở trong các kiểm tra đã chạy. Các kết quả trên dùng dữ liệu giả có
nhãn `SYNTHETIC_OFFLINE`; không chứng minh độ đầy đủ của nguồn thật hay hiệu năng kho triệu bản ghi.
Quét secret không thay thế kiểm toán lỗ hổng của toàn bộ thư viện.

## Sử dụng

Xem [hướng dẫn Linux](docs/linux-collector.md), hoặc `HUONG-DAN.md` trong thư mục bàn giao.
Cài đặt không tự gọi nguồn. Chọn Data Root nằm ngoài thư mục cài rồi mới `init`,
`enqueue --from ... --to ...` và `run`. Mặc định đủ sáu loại, lần lượt từng tuần.
Dừng worker an toàn trước nâng cấp/gỡ; không cài đè khi tiến trình còn chạy.

Bản Windows và phiên thu thập đang chạy không bị thay thế. Chỉ push nhánh Linux vào
repo Harvest theo quyền đã được owner cấp; không merge, tạo PR, deploy hoặc phát hành GitHub Release.

Bằng chứng đi kèm: `tests.xml`, `local-verification.json`, `secret-scan.json`,
`ci-final.json`, `ci-final.log` và hai script kiểm tra trong thư mục `evidence`.
