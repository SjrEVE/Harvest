# Săn Thầu Data Studio

Ứng dụng Windows để thu thập có kiểm chứng, lưu kho và tra cứu source record đấu thầu.
Giao diện PySide6/QML; collector chạy process riêng; SQLite và raw store nằm trong Data Root người dùng chọn.

## Bản RC2 tại máy

Bộ cài: `build/inno/SanThau-Data-Studio-Setup-0.0.1-RC2.exe`.
Chạy trực tiếp: `build/stage/SanThauDataStudio.exe` (giữ nguyên toàn bộ thư mục `stage`).

1. Mở **Onboarding**, nhập Data Root và bấm **Dùng thư mục này**.
2. Vào **Collection Jobs → Khởi động worker** để tạo/mở kho và serving snapshot.
3. **Sources** hiển thị trạng thái nguồn và boundary rules. Nguồn chưa có bằng chứng vẫn bị chặn.
4. **Data Explorer** tìm/tra cứu snapshot; **Settings / Backup** có backup, restore, verify, CSV và lịch.
5. Đóng GUI vẫn để worker thu thập; dừng worker từ Jobs khi cần bảo trì/restore.

Không có dữ liệu demo tự chèn trong bản chạy bình thường. RC2 hiện chưa ký số và chưa nghiệm thu trên
Windows sạch; live canary/deep live, license phân phối và thu hồi token dashboard cũ là các điều kiện còn mở.
Restore nhận backup Data Studio; import định dạng harvester cũ chưa triển khai.

## Kiểm tra và build

Python 3.12 và dependency đã pin trong `uv.lock`. Trên PowerShell:

```powershell
$env:PYTHONUTF8='1'
uv run --frozen pytest -q -o addopts=''
uv run --frozen ruff check src tests tools
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/nuitka/build_standalone.ps1
& 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' /DStageDir=D:\Harvest\santhau-data-studio\build\stage packaging/inno/santhau-data-studio.iss
uv run --frozen python tools/release_manifest.py installer --installer build/inno/SanThau-Data-Studio-Setup-0.0.1-RC2.exe
```

`SANTHAU_SCHEDULER_TEST=1` bật test tạo/xóa một task Windows thật. Suite còn lại sử dụng dữ liệu giả;
không gọi VNEPS. Các công cụ benchmark/fixture trong `tools/` dành cho thư mục thử riêng, không trỏ
vào kho đang dùng.

`build/source-manifest.json`, `build/stage/build-provenance.json` và `build/release-manifest.json`
ghi SHA256 input/artifact. Build từ working tree được ghi đúng là dirty; không nhận nhầm binary đó
là nội dung nguyên vẹn của commit nền.

Tình trạng nghiệm thu: [RC2-READINESS.md](RC2-READINESS.md).
Hướng dẫn thao tác: [docs/operator-guide.md](docs/operator-guide.md).
