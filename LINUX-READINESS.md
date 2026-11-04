# Săn Thầu Collector RC3 — bản Linux chỉ thu thập

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
