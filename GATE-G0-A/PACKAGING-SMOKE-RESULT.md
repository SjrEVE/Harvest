# PACKAGING-SMOKE-RESULT — G0-A / A8

**Timebox spike.** Mục tiêu: chứng minh PySide6 + Qt Quick/QML + worker headless + Nuitka standalone
+ Inno Setup **cùng chạy được trên Windows**, trước khi đầu tư engine. Không gọi VNEPS, không dữ liệu thật.

## 1. Kết luận nhanh

| Hạng mục | Trạng thái | Bằng chứng |
|---|---|---|
| Cửa sổ Qt Quick/QML render | **PASS** | `stage-smoke.png`, `installed-smoke.png` |
| Worker headless riêng, GUI nhận status | **PASS** | `stage-smoke.log` exit 0 |
| Nuitka standalone build (2 exe) | **PASS** | `nuitka-build5.log` `BUILD_OK`/`ASSEMBLE_OK` |
| Inno Setup installer build | **PASS** | `SanThau-Data-Studio-Setup-0.0.1-spike.exe` 40.807.031 bytes |
| Cài đặt per-user không cần admin | **PASS** | cài vào `%LocalAppData%\Programs\SanThauDataStudio`, exit 0 |
| Chạy **từ vị trí cài** | **PASS** | `installed-smoke.log` exit 0 |
| Không phụ thuộc môi trường dev | **PASS** | chạy với `PATH=System32` chỉ, `python` không có trên PATH; mọi module đang load nằm trong thư mục cài |
| Uninstall sạch | **PASS** | "install dir fully removed", registry entry hết, sentinel data ngoài `{app}` còn nguyên |
| **Cài trên VM Windows sạch** | **NOT_TESTED** | xem §5 — không phải máy này, và Docker Windows container không dùng được |

Không có hạng mục nào ở trên dựa vào code review: mỗi dòng là một lệnh đã chạy, log nằm trong
`_evidence/a8/`.

## 2. Số đo thực tế

| Chỉ số | Giá trị |
|---|---|
| `SanThauDataStudio.exe` | 5.619.200 bytes |
| `SanThauCollector.exe` | 4.980.736 bytes |
| Stage folder (2 exe gộp) | 1.895 files / 171.571.401 bytes (~164 MB) |
| Installer LZMA2 | 40.807.031 bytes (~39 MB) |
| File sau cài | 1.900 |
| Thời gian build Nuitka (lệnh đầy đủ) | ~4 phút |
| Thời gian `iscc` | 68,8 s |
| Qt DLL/pyd trong deployment | 180 (phân loại 100%, 0 unclassified) |
| QML module bundle | `QtQuick`, `QtQuick.Window`, `QtQuick.Controls.Basic` |

MSVC 14.3 (VS BuildTools) + Windows Kit 10.0.26100 được Nuitka tự phát hiện; `msvcp140*.dll` và
`vcruntime140*.dll` được **copy vào folder** → không yêu cầu redist cài sẵn. Đây là lý do §2.1 chọn
standalone folder thay vì `onefile`, và nó cũng giữ `qt6*.dll` thành file rời — đúng dạng liên kết
mà điều khoản LGPL tối thiểu mong đợi.

## 3. Bốn lỗi thật tìm thấy khi chạy spike — không phải lỗi giả định

Spike này có giá trị chính ở đây. Mỗi lỗi đều **chỉ lộ ra khi chạy bản đóng gói**, không thấy bằng đọc code.

### 3.1 Nuitka không đặt `sys.frozen`
`app_main.py` ban đầu rẽ nhánh theo `sys.frozen`/`sys._compiled` → bản package tìm `desktop/qml/Main.qml`
và fail `missing QML entry point`.
**Sửa:** resolve theo **thứ tự đường dẫn** (exe-adjacent `qml/` trước, rồi source tree). Đơn giản hơn và
đúng cho cả source tree, `.dist` và thư mục cài.

### 3.2 stdout cp1258 đánh crash bản package
Bản package in dòng status tiếng Việt → `UnicodeEncodeError` trong `encodings/cp1258.py`, exit 1.
Đây là **lỗi sản phẩm**, không phải lỗi test: mọi log tiếng Việt của Data Studio sẽ sập khi chạy từ
shell hoặc Task Scheduler.
**Sửa:** `force_utf8_stdout()` ở cả hai entry point. Có test hai chiều
(`tests/unit/test_console_encoding.py`): bản có fix PASS, bản không fix phải FAIL với đúng
`UnicodeEncodeError` — nếu không có negative control thì test chỉ là trang trí.

### 3.3 Ảnh screenshot "thành công" nhưng là cửa sổ của app khác
`QScreen.grabWindow(hwnd)` khi cửa sổ **không ở foreground** trả về nội dung **vùng màn hình** tại
toạ độ đó. Hai lần smoke báo `verdict PASS` kèm ảnh 720×420 hợp lệ — nhưng ảnh là một UI khác đang
chiếm chỗ.
**Sửa:** `raise_()` + `requestActivate()` + pump events trước khi grab; và guard `winId != 0`,
`isVisible`, kích thước ảnh khớp cửa sổ.
**Bài học ghi lại cho G3/G4:** một smoke test screenshot phải **chứng minh nó chụp đúng window**,
nếu không nó tạo bằng chứng giả. Tôi đã vứt hai ảnh sai và chụp lại.

### 3.4 Uninstall để lại 38 file khi app đang chạy
`CloseApplications=yes` (đã thêm vào `.iss`) **không** đóng được GUI ở chế độ `/SILENT`, nên mọi DLL
đang bị tiến trình giữ bị bỏ lại trong `{app}`.
Đã kiểm chứng phân biệt: cùng installer, **không có** tiến trình nào → "install dir fully removed";
**có** một tiến trình `SanThauDataStudio.exe` (pid 18048) → 38 file còn, và `Remove-Item` báo
"Access … is denied".
**Hệ quả cho G4:** app phải tạo **named mutex** để Inno phát hiện và đóng; không có mutex thì phát hiện
của Inno chỉ là best-effort. Ghi vào G2 (worker/controller lifecycle) vì mutex gắn với vòng đời process.

## 4. IPC của spike — và vì sao nó KHÔNG phải hợp đồng §2.4

Spike dùng **pipe stdout của process con** (GUI spawn collector, đọc line-delimited JSON).
§2.4 yêu cầu **named pipe + xác thực SID**, và nói rõ socket/loopback bị bỏ vì không có danh tính peer.

Lý do spike dùng cách khác: mục tiêu A8 là chứng minh toolchain, không phải chứng minh security boundary.
**Không được coi đây là đã đáp ứng §2.4** — phần đó thuộc G2, với test SID mismatch và pipe squatting.

Dòng status của spike có chủ đích dùng từ vựng receipt §2.3 (`attempt`, `response_received`,
`page_committed`, `proof_verified`, `snapshot_published`) và kết thúc bằng `run_status: PARTIAL`
kèm lý do "không có terminal evidence" theo §4.1 — để hình dạng dữ liệu đã đúng ngay từ đầu.

## 5. Vì sao "VM Windows sạch" vẫn NOT_TESTED

Bạn chọn Docker Windows container. Tôi phải báo một trở ngại kỹ thuật **trước khi** bỏ thời gian:

- Windows container chạy nền tảng **Server Core, không có GUI subsystem**. Một cửa sổ Qt Quick cần
  desktop window manager; `qwindows.dll` platform plugin sẽ không có chỗ để tạo window.
  → container **không thể** nghiệm thu tiêu chí "cửa sổ hiện lên trên Windows sạch".
- Docker Desktop trên máy này đang ở **Linux containers mode** (`desktop-linux` context), daemon chưa chạy.
  Đổi sang Windows containers thường cần đổi engine + reboot, và vẫn vướng lỗi trên.
- Instabox (theo tôi biết) là cloud **Linux** box → không dùng cho tiêu chí Windows.

Những gì tôi đã làm thay thế, và nó chứng minh được điều gì:

| Kiểm tra | Chứng minh | **Không** chứng minh |
|---|---|---|
| Chạy với `PATH=C:\Windows\System32` chỉ, `python` không tồn tại | không phụ thuộc Python trên PATH | registry sạch, không có VS BuildTools |
| Liệt kê `Modules` của process đang chạy | 100% DLL load từ thư mục cài | hành vi trên máy chưa từng có Qt |
| Installer tự bundle `msvcp140*.dll`, `vcruntime140*.dll` | không cần VC redist cài trước | — |

**Cần để đóng A8 hoàn toàn:** một máy/VM Windows 10/11 x64 **chưa từng** cài Python, Git, Qt, VS
BuildTools. Cho tôi quyền truy cập (RDP hoặc credential) thì tôi chạy và quay bằng chứng; nếu không,
tiêu chí này chuyển thành gate của G4, nơi bắt buộc có VM sạch để test Authenticode + upgrade anyway.

## 6. Cách tái lập

```powershell
# 1. env đã pin Python 3.12.14
uv sync --all-groups

# 2. build 2 exe + gộp vào build\stage
powershell -ExecutionPolicy Bypass -File packaging\nuitka\build_standalone.ps1

# 3. smoke (cửa sổ + worker + screenshot), exit 0 là PASS
build\stage\SanThauDataStudio.exe --smoke smoke.png

# 4. installer
& 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' `
  /DStageDir=D:\Harvest\santhau-data-studio\build\stage `
  packaging\inno\santhau-data-studio.iss

# 5. license manifest sinh từ deployment thật
python tools\license_manifest.py --dist build\stage --out-dir . --json
```

## 7. Artifacts

```
_evidence/a8/
  nuitka-build.log nuitka-build2.log nuitka-build3.log nuitka-build4.log nuitka-build5.log
  stage-smoke.log stage-smoke.png          # bản .dist gộp
  installed-smoke.log installed-smoke.png  # bản đã cài
  cleanenv-smoke.png                       # chạy với PATH tối giản
  probe_grab.py probe_capture2.py          # nguồn gốc phát hiện 3.3
```

## 8. Trạng thái A8

**PASS có điều kiện.** Toolchain đã chứng minh chạy được từ đầu tới cuối trên máy này, kể cả cài và gỡ.
Tiêu chí "VM Windows sạch" = **NOT_TESTED** và cần tài nguyên tôi không có. Nghiệm thu installer đầy đủ
(ký Authenticode, upgrade path, uninstall giữ data root thật) vẫn thuộc G4 theo §9.
