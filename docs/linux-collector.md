# Bộ thu thập Săn Thầu cho Linux

Bản Linux chỉ thu thập và lưu dữ liệu: không GUI, Qt, dashboard, tìm kiếm hay tính tổng nguồn.
Kho SQLite, dữ liệu gốc, lịch sử và bằng chứng vẫn dùng cùng hợp đồng dữ liệu với Windows.
Không tạo hoặc rebuild `serving.sqlite`.

Bản cập nhật **0.0.1-RC3-11** (`.deb`: `0.0.1~rc3-11`) dành cho Linode chạy
Ubuntu 22.04/24.04 x86-64 (Intel/AMD). Tự áp dụng migration v8 và v9 để mở rộng enum role
của `money_amounts` cho parser v5 (lane M1) và enum lane/job_kind cho `M1`, giữ nguyên dữ liệu và tiến độ đã có. Bản mới bổ sung:

- Lane M1: giá trúng thầu theo từng mặt hàng trong KQLCNT (`ITEM_WINNING_PRICE`,
  `ITEM_BID_PRICE`) qua endpoint `expose/contractor-input-result/get`, 1 request
  cho 1 record theo `inputResultId`; lệnh `deep-sweep --lane M1` để quét bù
  (mặc định chỉ xem trước, `--execute` mới xếp hàng).
- Parser v4 trích giá vào `money_amounts` (REOFFER, bid, priceInit/priceStep, giá lot HSMT);
  `replay-deep` chạy lại từ raw đã lưu (0 request mạng) để backfill giá cho capture cũ.
- HSMT form 1958/1959 thành STRUCTURED sau canary toàn bộ 393 payload.
- Deep retry: cooldown leo thang 1h→6h→24h→72h, quarantine sau lỗi thứ 5; batching gom
  task theo lane (tối đa 10 record/job).

- Thu thập HSMT/chi tiết/chào giá công khai cho TBMT/CGTTRG, ghi raw, lịch sử,
  lô/hàng hóa, người chào giá, tham chiếu tài liệu và liên kết theo mã nguồn.
- Chạy bù tự động trên chính worker; checkpoint, hạn mức và tạm dừng dùng chung.

- Lấy lại ngày vừa khép nếu phần lịch sử chạy qua nửa đêm; lượt bắt đầu khi
  ngày còn mở không được tự cấp proof chỉ vì hoàn thành sau nửa đêm.
- Chia khoảng sớm theo hạn mức còn lại, tính cả yêu cầu thăm dò khoảng cha.
- Trạng thái runtime và dấu thời gian trang mới; không trả tiến độ cũ sau
  khi tác vụ kết thúc. Từ chối lệnh không có tác vụ phù hợp hoặc lệnh tiếp tục lặp.
- Gói chạy bỏ thư viện zstandard chỉ cần lúc build; raw vẫn dùng gzip.


## Cài đặt

Gói x86-64 nhắm Ubuntu 22.04 trở lên (glibc từ 2.35). Phạm vi đã kiểm thực tế nằm trong
`LINUX-READINESS.md` đi kèm bàn giao; không mặc định hỗ trợ ARM hoặc Alpine/musl.

```sh
sha256sum -c SHA256SUMS
sudo apt install ./santhau-collector_0.0.1~rc3-11_amd64.deb
santhau-collector --version
```

Gói `.deb` cài chương trình tại `/opt/santhau-collector`, lệnh tại `/usr/bin/santhau-collector`.
Cài đặt không tự chạy dịch vụ và không gọi E-GP. Không cần cài Python hoặc Qt trên máy đích.

Bản tar.gz có thể cài bằng tài khoản thường:

```sh
tar -xzf santhau-collector-0.0.1-RC3-11-linux-x86_64.tar.gz
cd santhau-collector-0.0.1-RC3-11-linux-x86_64
sh install.sh "$HOME/.local/opt/santhau-collector"
"$HOME/.local/opt/santhau-collector/santhau-collector" --version
```

## Khởi tạo và chạy

Thư mục mặc định là `$XDG_DATA_HOME/santhau-collector`, hoặc
`~/.local/share/santhau-collector` nếu biến đó chưa được đặt. Có thể chọn thư mục khác
bằng `--data-root`. Kho dữ liệu phải nằm ngoài thư mục cài; không chia sẻ cùng một kho
cho hai máy hoặc hai bộ thu thập đang chạy.

```sh
santhau-collector init
santhau-collector deep-config --enable --batch-requests 30
santhau-collector enqueue --from 2022-01-01 --to "$(TZ=Asia/Ho_Chi_Minh date +%F)"
santhau-collector run
```

Ngày cuối được tính trọn ngày theo giờ Việt Nam. Mặc định thu thập đủ sáu loại theo
từng đợt 7 ngày từ ngày bắt đầu, ưu tiên đợt mới nhất rồi lùi dần về trước; đủ sáu loại
trong tuần hiện tại mới lùi tuần. Đợt cuối có thể ngắn hơn 7 ngày. Ngày đã có bằng
chứng đầy đủ sẽ được bỏ qua. Phần đang dở được giữ nhưng có thể cần đọc lại để đối chiếu.

Nếu chọn đến hôm nay, lấy dữ liệu hiện có của hôm nay cho cả sáu loại rồi tiếp tục
các ngày đã kết thúc. Hôm nay giữ trạng thái chưa đầy đủ vì còn phát sinh hồ sơ;
chạy cập nhật hoặc chạy lại sau khi hết ngày để chốt bằng chứng. Tình trạng này
không bị nhầm thành lỗi mạng hay nguồn chặn truy cập.

Giới hạn mỗi lượt: 1.000 trang, 3.000 yêu cầu, 3.600 giây; giãn cách ít nhất 3 giây và
chia sẻ giới hạn theo host. Có thể đặt `--max-pages`, `--max-requests`, `--max-seconds`
và `--pacing-seconds` lúc enqueue. Khi chạm giới hạn và vẫn có tiến triển, tác vụ tuần
tiếp tục phần thiếu; không tiến triển hoặc bị chặn thì dừng, không ghi hoàn tất giả.

```sh
santhau-collector status
santhau-collector pause
santhau-collector resume
santhau-collector status --job linux-<ma-tac-vu>
santhau-collector cancel --job linux-<ma-tac-vu>
santhau-collector stop
```

`status` chỉ xem trạng thái vận hành và tiến độ tác vụ, không chạy thống kê toàn kho.
`running: true` nghĩa là tiến trình đang hoạt động; xem `runtime.phase` để phân biệt
STARTING, RECOVERING, COLLECTING, DEEP_COLLECTING, PAUSING, STOPPING_JOB, STOPPING_WORKER và IDLE.
`progress.updated_at` là lần nhận trang gần nhất. Khi chưa có tác vụ chạy, tiến độ rỗng.
`stop` hoặc SIGTERM lưu trang hiện tại rồi xếp lại tác vụ dở cho lần khởi động kế tiếp.
`cancel` kết thúc tác vụ được chọn; dữ liệu đã lưu vẫn còn. Tạm dừng có chủ đích cần
`resume`; không tự bỏ qua lựa chọn tạm dừng khi khởi động lại.

### Chạy bù dữ liệu chi tiết

Chạy `deep-config` khi worker đã dừng; khóa một writer từ chối thay đổi khi collector
đang ghi. Cấu hình được lưu trong kho và tồn tại qua restart/upgrade. Mặc định tắt
cho đến khi bật rõ như ví dụ trên. Xem tiến độ trong lúc worker chạy:

```sh
santhau-collector deep-status --data-root "$HOME/SanThauData"
```

Worker xử lý tối đa 30 cặp hồ sơ/lane mỗi đợt, xen giữa các lượt danh sách và khi rảnh,
giữ nhịp tối thiểu 3 giây/request toàn host. Tác vụ được pause thì tự động cũng dừng.
Capture còn mới được bỏ qua; listing thay đổi hoặc hết hạn 30 ngày thì làm mới và giữ
lịch sử. FAILED/PARSE_ERROR có cooldown ít nhất 60 phút; endpoint bị chặn dừng trong
worker hiện tại. `deep-status` phân biệt FETCHED, SOURCE_EMPTY, PARSE_ERROR, BLOCKED,
FAILED; không coi phản hồi lỗi là không có dữ liệu.

Chỉ hỗ trợ các đường CGTTRG đã xác minh. Documents hiện là REFERENCE_ONLY, chưa tải
file đính kèm. KQLCNT_FULL, BOQ và HSMT ngoài CGTTRG vẫn chưa xác minh. Không suy
OFFEROR thành người trúng/liên danh/mã số thuế. `enqueue --to` là ngày kết thúc cố định;
không tự tạo lịch ngày mới. Sau đó có thể enqueue INCREMENTAL để cập nhật danh sách.

```sh
santhau-collector enqueue --mode INCREMENTAL --data-root "$HOME/SanThauData"
santhau-collector self-test-deep --data-root /tmp/santhau-deep-offline-check
```

Self-test chỉ dùng payload synthetic trên thư mục mới; không gọi E-GP, không phải
bằng chứng IP Linode truy cập được nguồn thật. Worker từ chối chạy kho synthetic
bằng đường live. Không dùng kho self-test làm kho production.

## Dịch vụ nền

Với gói `.deb`, dùng dịch vụ của chính tài khoản lưu dữ liệu:

```sh
systemctl --user daemon-reload
systemctl --user enable --now santhau-collector.service
journalctl --user -u santhau-collector.service -f
```

Chỉ bật sau khi đã chọn kho và xếp tác vụ muốn chạy. Nếu cần dịch vụ tiếp tục khi đăng xuất,
quản trị viên có thể bật lingering cho tài khoản đó. Dừng bằng
`systemctl --user stop santhau-collector.service` trước khi nâng cấp/gỡ.
Dùng override của service nếu cần đặt `SANTHAU_DATA_ROOT` khác mặc định.

### Thiết lập trên Linode và tiếp tục sau khi thoát SSH

Dùng cùng một tài khoản Linux cho khởi tạo, xếp tác vụ, điều khiển và service.
Ví dụ dưới chọn `~/SanThauData` trên ổ server, không dùng tài khoản root cho collector:

```sh
mkdir -p "$HOME/SanThauData" "$HOME/.config/systemd/user/santhau-collector.service.d"
santhau-collector init --data-root "$HOME/SanThauData"
santhau-collector deep-config --enable --batch-requests 30 --data-root "$HOME/SanThauData"
santhau-collector enqueue --data-root "$HOME/SanThauData" --from 2022-01-01 --to "$(TZ=Asia/Ho_Chi_Minh date +%F)"
cat > "$HOME/.config/systemd/user/santhau-collector.service.d/data-root.conf" <<EOF
[Service]
Environment="SANTHAU_DATA_ROOT=$HOME/SanThauData"
EOF
sudo loginctl enable-linger "$(id -un)"
systemctl --user daemon-reload
systemctl --user enable --now santhau-collector.service
santhau-collector status --data-root "$HOME/SanThauData"
```

Các lệnh trên xếp đầy đủ sáu loại từ 2022 đến hôm nay và bật chạy bù chi tiết;
việc gọi nguồn bắt đầu khi service chạy. Thay khoảng ngày trước khi enqueue nếu cần.
Mọi lệnh điều khiển đều truyền cùng `--data-root`. Với Linode Volume, thay bằng
thư mục trên volume đã mount ổn định, thuộc tài khoản chạy; không lưu kho trong
`/opt/santhau-collector`.

`enable-linger` giữ user service khi thoát SSH; service được enable để tự chạy
cùng user manager. Xem `LINUX-READINESS.md` để phân biệt kiểm thử service với
thử reboot/server Linode thật.

Lịch cập nhật tự động dùng bộ lập lịch của hệ điều hành để gọi
`santhau-collector enqueue --mode INCREMENTAL`; bản này không kèm Task Scheduler hay màn cấu hình lịch.
`--mode RECONCILE --from ... --to ...` kiểm tra lại khoảng đã có bằng chứng.

## Cấu hình nguồn và kiểm chứng

`init` nạp profile sáu loại đã được xác minh ở RC3 trước đó, ràng buộc với mã adapter.
Profile chỉ chứa quy tắc nguồn và dấu thời gian, không mang hồ sơ thật hoặc proof COMPLETE.
Không coi profile đó là bằng chứng đã chạy live thành công trên máy Linux mới.
Nguồn cần phiên đăng nhập và các lane chưa xác minh vẫn bị chặn; không giải CAPTCHA
hoặc dùng token đã lộ.

`init --no-profile` tạo kho chưa xác minh, sẽ từ chối enqueue thu thập thật.
`init --source-profile <file>` chỉ nhận cấu hình đúng schema và adapter; không tự mở khóa
nguồn đã bị chặn trong kho hiện có.

Có thể tự thử gói cài bằng dữ liệu giả trên một thư mục mới:

```sh
santhau-collector self-test --data-root /tmp/santhau-offline-check
```

Lệnh này không gọi mạng và từ chối dùng kho đã tồn tại. Không dùng kho thử làm kho thật.

## Nâng cấp và gỡ

Giữ nguyên Data Root, dừng dịch vụ an toàn rồi cài đè gói mới. Với `.deb`:

```sh
systemctl --user stop santhau-collector.service
santhau-collector status --data-root "$HOME/SanThauData"
sudo apt install ./santhau-collector_0.0.1~rc3-11_amd64.deb
systemctl --user daemon-reload
santhau-collector deep-config --enable --batch-requests 30 --data-root "$HOME/SanThauData"
systemctl --user start santhau-collector.service
```

Chỉ cài đè sau khi `status` báo `running: false`. Nếu worker chạy thủ công,
dùng `santhau-collector stop --data-root ...` và chờ thoát. Khi muốn gỡ:

```sh
systemctl --user disable --now santhau-collector.service
sudo apt remove santhau-collector
```

Với bản tar, chạy lại `install.sh` cùng thư mục để nâng cấp; gỡ bằng `uninstall.sh` ở
thư mục đã cài. Script từ chối xóa nếu phát hiện kho SQLite bên trong thư mục cài.
Không xóa Data Root hoặc job files để sửa lỗi. Dữ liệu lớn cần được sao lưu riêng trước bảo trì.


## Dung lượng và chuyển kho

Collector không cần `serving.sqlite` của Windows. Giữ warehouse, raw và trạng thái
cần thiết. Không copy database đang ghi bằng thao tác copy tệp thông thường:
dừng collector an toàn hoặc dùng bản sao SQLite nhất quán cùng raw tương ứng.
Không để Windows và Linux ghi đồng thời vào cùng một kho.

Mốc 05/11: khoảng 222 nghìn bản ghi có 737 MB warehouse, 276 MB raw đã nén và
khoảng 5 MB tệp ghi tạm; tổng khoảng 1,02 GB khi không mang bản tra cứu sang.
Đây là mốc tham chiếu, không phải dung lượng cố định hoặc phép đo trên Linode.
Lịch sử, đối chiếu, backup và tài liệu đính kèm có thể làm tăng dung lượng.
Manifest mới ghi `installed_file_bytes` và `artifact_bytes` để đối chiếu gói thực tế.
