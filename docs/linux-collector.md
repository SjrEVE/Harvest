# Bộ thu thập Săn Thầu cho Linux

Bản Linux chỉ thu thập và lưu dữ liệu: không GUI, Qt, dashboard, tìm kiếm hay tính tổng nguồn.
Kho SQLite, dữ liệu gốc, lịch sử và bằng chứng vẫn dùng cùng hợp đồng dữ liệu với Windows.
Không tạo hoặc rebuild `serving.sqlite`.

## Cài đặt

Gói x86-64 nhắm Ubuntu 22.04 trở lên (glibc từ 2.35). Phạm vi đã kiểm thực tế nằm trong
`LINUX-READINESS.md` đi kèm bàn giao; không mặc định hỗ trợ ARM hoặc Alpine/musl.

```sh
sha256sum -c SHA256SUMS
sudo apt install ./santhau-collector_0.0.1~rc3_amd64.deb
santhau-collector --version
```

Gói `.deb` cài chương trình tại `/opt/santhau-collector`, lệnh tại `/usr/bin/santhau-collector`.
Cài đặt không tự chạy dịch vụ và không gọi E-GP. Không cần cài Python hoặc Qt trên máy đích.

Bản tar.gz có thể cài bằng tài khoản thường:

```sh
tar -xzf santhau-collector-0.0.1-RC3-linux-x86_64.tar.gz
cd santhau-collector-0.0.1-RC3-linux-x86_64
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
santhau-collector enqueue --from 2026-01-01 --to 2026-11-03
santhau-collector run
```

Ngày cuối được tính trọn ngày theo giờ Việt Nam. Mặc định thu thập đủ sáu loại theo
từng đợt 7 ngày từ ngày bắt đầu; đủ tuần hiện tại mới chuyển tuần tiếp. Ngày đã có bằng
chứng đầy đủ sẽ được bỏ qua. Phần đang dở được giữ nhưng có thể cần đọc lại để đối chiếu.

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
`stop` hoặc SIGTERM lưu trang hiện tại rồi xếp lại tác vụ dở cho lần khởi động kế tiếp.
`cancel` kết thúc tác vụ được chọn; dữ liệu đã lưu vẫn còn. Tạm dừng có chủ đích cần
`resume`; không tự bỏ qua lựa chọn tạm dừng khi khởi động lại.

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
sudo apt install ./santhau-collector_0.0.1~rc3_amd64.deb
sudo apt remove santhau-collector
```

Với bản tar, chạy lại `install.sh` cùng thư mục để nâng cấp; gỡ bằng `uninstall.sh` ở
thư mục đã cài. Script từ chối xóa nếu phát hiện kho SQLite bên trong thư mục cài.
Không xóa Data Root hoặc job files để sửa lỗi. Dữ liệu lớn cần được sao lưu riêng trước bảo trì.
