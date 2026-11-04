# Đối chiếu gói cài Windows RC3 — commit `1e65962`

Chạy lúc 2026-11-04T11:08:27.328235+00:00 bởi Hermes, thay lượt Codex bị `usage_limit_exceeded` lúc 17:26 +0700.
Codex dở ở câu: *"đang đối chiếu gói cài và dữ liệu trước khi thay bản đang dùng"*.

## Kết luận

**PASS trong phạm vi đối chiếu.** Bản vá build 17:12–17:14 đúng nguồn `1e65962`
(127/127 input khớp hash), 2 artifact khớp `SHA256SUMS`, full suite 636 passed / 1 skipped,
ruff sạch. **Chưa thay bản đang cài, chưa cào tiếp, chưa tạo PR/merge/release.**

| Hạng mục | Kết quả |
|---|---|
| `base_commit` → HEAD | ancestor ✓, working tree sạch lúc đo ✓ |
| Source inputs | 127 checked, 0 mismatch |
| `SanThauCollector.exe` | `63728d9061da0e9e…` / 18.321.920 B — OK |
| Installer RC3 | `db5757f21f424c37…` / 49.372.976 B — OK |
| Bản đang cài (cũ) | `ac20d595` / `db6a4e10…` / 49.338.148 B — **giữ nguyên** để rollback |
| Full suite | 636 passed, 1 skipped, 0 failed (196,89s) — `tests.xml` |
| `ruff check src tests tools` | All checks passed |

## Dữ liệu thật (đọc read-only, không ghi)

`D:/Harvest/SanThauData/serving.sqlite`: 70.234 records, 734 coverage intervals
(145 COMPLETE / 589 PARTIAL), 145 proofs, 7.148 fetch_requests, 71 runs.
Job `job-cef1bf50a525` vẫn `PAUSED`; `writer.lock` còn (pid 16192).
Tuần 29/10–03/11 xác nhận lời Codex: TBMT 8 PARTIAL, KHLCNT 1 PARTIAL,
KQLCNT và YCBG mỗi loại mới 1 COMPLETE. **RESUME = NOT_RUN.**

## Chạy lại

```bash
cd D:/Harvest/santhau-data-studio
.venv/Scripts/python.exe tools/verify_release_evidence.py \
  --repo . --manifest build/release-manifest.json \
  --artifacts build/stage/SanThauCollector.exe \
             build/inno/SanThau-Data-Studio-Setup-0.0.1-RC3.exe \
  --sha256sums build/RC3-final-1e65962/SHA256SUMS \
  --out build/RC3-final-1e65962/verifier-output.json
.venv/Scripts/python.exe -m pytest -ra --junitxml=build/RC3-final-1e65962/tests.xml
.venv/Scripts/python.exe -m ruff check src tests tools
```

## Vẫn BLOCKED / NOT_RUN (cần owner)

Thay bản đang cài, resume thu thập, canary E-GP thật, Authenticode,
quyết định LGPL/commercial Qt, VM Windows sạch. Xem `docs/release-checklist.md` §2–§5
và `local-verification.json` → key `blocked`.

## Cài đặt lên máy Boss (18:21 +0700)

Boss gật bước thay bản. Đã `unins000.exe /VERYSILENT` (exit 0, dọn luôn registry
orphan `Temp\SanThauRC6`), rồi cài installer `1e65962` `/VERYSILENT` (exit 0,
Inno log `Installation process succeeded`, không cần restart).

- `SanThauCollector.exe` trên máy = `63728d90…` **khớp** `build/stage` → đúng bản mới.
- Backup `D:/Harvest/_backup-before-reinstall-20261104` (655 MB / 256 file).
- **Dữ liệu không đổi**: cả 2 sqlite `MATCH` SHA256 với backup, `writer.lock` còn,
  70.234 records, `integrity ok`, job `PAUSE` còn.
- Chạy thử `--smoke`: **PASS**, screenshot 1380x900, `worker.running=false`,
  `queued_jobs=0` → không cào E-GP. Không còn process SanThau sót lại.
- Vẫn **NOT_RUN**: resume cào tuần 29/10–03/11; canary thật; ký số; Qt legal; VM sạch.

## Caveat khi đọc evidence

- `release-manifest.json` ghi `captured_at_utc` 17:02 nhưng artifact build 17:12–17:14:
  manifest tái dùng snapshot trước build. 127/127 input vẫn khớp hash nguồn `1e65962`.
- 636 passed là **pytest local**; nhánh `codex/rc3-completion` không có workflow CI.
- Evidence nằm trong `build/` (vốn bị ignore) nên đã được `git add -f`; `.gitignore`
  đã bổ sung negation cho đúng thư mục này để lần regenerate sau không bị bỏ sót.
