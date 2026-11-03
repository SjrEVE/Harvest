# Release checklist — RC2

Mỗi mục ghi rõ **ai làm** và **bằng chứng**. Không mục nào được tick nếu thiếu bằng chứng.

## 1. Build

- [ ] `uv run pytest -q` xanh tại đúng commit phát hành.
- [ ] `uv run ruff check src tests tools` sạch.
- [ ] `powershell -ExecutionPolicy Bypass -File packaging/nuitka/build_standalone.ps1` → `BUILD_OK`, `ASSEMBLE_OK`.
- [ ] `iscc /DStageDir=<repo>\build\stage packaging\inno\santhau-data-studio.iss` → installer mới.
- [ ] Ghi SHA256 của `SanThauDataStudio.exe`, `SanThauCollector.exe`, installer vào biên bản.

## 2. Ký số (owner + chứng chỉ)

- [ ] Có chứng chỉ Authenticode hợp lệ (không dùng self-signed cho bản phát hành).
- [ ] `signtool sign /fd SHA256 /tr <timestamp-url> /td SHA256 /a SanThauDataStudio.exe`,
      tương tự `SanThauCollector.exe` và installer (ký installer **sau** khi stage chứa exe đã ký).
- [ ] `signtool verify /pa /all <file>` cho từng file.
- [ ] **BLOCKED** tới khi owner cấp chứng chỉ. Không tự ký.

## 3. License / notice

- [ ] `THIRD_PARTY_NOTICES.txt` liệt kê đúng các thành phần **thực sự có trong stage**
      (PySide6, shiboken6, CPython, tzdata, httpx, certifi, h11, anyio, idna, sniffio, PyYAML, tenacity…).
- [ ] Chọn chế độ phân phối cho Qt (LGPL-3.0 kèm full text + offer source, GPL, hoặc commercial) và
      đính đúng file license vào `LICENSES/`. Wheel PySide6 chỉ kèm `LicenseRef-Qt-Commercial.txt` —
      **không đủ** cho LGPL. **BLOCKED tới khi owner chọn**.
- [ ] Kiểm tra `LICENSES/` trong installer chứa full text đã chọn.

## 4. Kiểm thử trên Windows sạch (owner/VM)

- [ ] Cài mới bằng installer → app mở, Onboarding chọn Data Root được.
- [ ] Chạy workflow fake-source end-to-end (6 family) trên VM.
- [ ] Upgrade đè bản cũ → dữ liệu và Data Root giữ nguyên.
- [ ] Uninstall khi app đang mở: installer đóng app (Restart Manager), **không** còn file bị khoá.
- [ ] Uninstall **không xoá Data Root**.
- [ ] Backup → restore trên VM thứ hai → integrity OK, search chạy.

## 5. Canary (owner)

- [ ] Duyệt khung giờ/quyền; chạy `tools/canary.py plan` để lưu biên bản.
- [ ] `probe` từng family với `SANTHAU_CANARY_APPROVED=<owner>`; kiểm tra `adapter_health`.
- [ ] Chỉ sau khi owner verify boundary rule: `collect` một ngày/family; kiểm tra
      `canary_state=COLLECTION_PROVEN`; lưu proof + response sha.
- [ ] Không chạy `collect` khi chưa có rule verified — công cụ sẽ từ chối.

## 6. Rollback

- [ ] Giữ installer bản trước + SHA256.
- [ ] Dữ liệu: raw layout mới (segments) đọc dual-layout; rollback layout = đặt
      `SANTHAU_RAW_LAYOUT=objects` hoặc xoá `raw/segments` khi legacy còn (xem `migrate_to_segments`).
- [ ] Snapshot serving cũ được giữ tự động nếu publish lỗi (atomic replace).

## 7. Biên bản phát hành

- [ ] `RC2-READINESS.md` cập nhật PASS/FAIL/BLOCKED/NOT_TESTED đúng thực tế tại thời điểm phát hành.
- [ ] Danh sách blocker còn lại + người chịu trách nhiệm.
- [ ] Không tuyên bố release PASS khi thiếu: clean-VM, Authenticode, license decision.