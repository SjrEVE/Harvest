# Canary matrix — nguồn, quyền, kỳ vọng (RC)

Mục tiêu: mỗi lane chỉ được chuyển từ **BLOCKED** sang **LIVE_VERIFIED** bằng một lần canary có
bằng chứng lưu lại. Không dòng nào trong bảng này là tuyên bố đã chạy; cột *Trạng thái* hiện tại
phản ánh đúng sự thật của repo.

| # | Nguồn | Endpoint | Family | Boundary / filter | Quyền truy cập | Bằng chứng cần lưu | Kỳ vọng | Trạng thái hiện tại |
|---|---|---|---|---|---|---|---|---|
| C1 | egp_home | `/o/egp-portal-home/services/smart/search` | TBMT | `publicDate` range, rule DAY `CLOSED_LOCAL` `to_minus=1s` | Public, không token | response sha + proof row + `filter_boundary_rules.canary_verified=1` | 1 ngày COMPLETE; total_first=total_last<10.000; terminal SHORT/EMPTY | **BLOCKED** — chưa chạy canary thật |
| C2 | egp_home | như trên | KQLCNT | như trên, filter `caseKHKQ in [1]` | Public | như trên | như C1 | **BLOCKED** |
| C3 | egp_home | như trên | HOP_DONG | như trên | Public | như trên | như C1 | **BLOCKED** |
| C4 | egp_home | như trên | KHLCNT | như trên | Public | như trên | như C1 | **BLOCKED** |
| C5 | egp_home | như trên | DU_AN | như trên | Public | như trên | như C1 | **BLOCKED** |
| C6 | egp_home | như trên | YCBG | như trên | Public | như trên | như C1 | **BLOCKED** |
| C7 | egp_v2 | `/o/egp/v2/search` | 6 family | session header auth — **chưa chứng minh** | Cần session người dùng (reCAPTCHA v3) | header auth test + canary | Không bypass; chỉ khi B7 đóng | **BLOCKED (B7)** |
| C8 | egp_v2 | detail render (`detail-v2`) | DETAIL | spec paths hiện `None` (D-3) | Session | payload mẫu + paths được duyệt + `DeepLaneSpec` có paths | parse FULL; lots/goods/members đúng; history tăng | **BLOCKED** |
| C9 | egp_v2 | HSMT | HSMT | 14 cột HSMT khoá theo canary | Session | payload HSMT + 14 cột | 14 cột verbatim | **BLOCKED** |
| C10 | moh_quotes | `/moh/quotes` | (không thuộc 6 family) | — | Không rõ | — | Đã quarantine từ source repo (API reset 2026-10-31) | **DISABLED** |
| C11 | mof_quotes | `/mof/quotes` | — | — | Không rõ | — | Quarantine tới khi có canary | **DISABLED** |

## Điều kiện để chạy một dòng C1–C6

1. Có quyền/khung giờ được duyệt cho canary (không dùng token dashboard đã lộ — A1).
2. Chạy `Runner.backfill` đúng một ngày VN với rule canary-verified do owner duyệt.
3. Lưu: response object sha của từng trang, proof row, `fetch_attempts`, kết quả `coverage.resolve`.
4. Chỉ khi đó đổi `filter_boundary_rules.canary_verified` và ghi `LIVE_VERIFIED` vào báo cáo.

## Bằng chứng offline đã có (không thay thế canary)

- 6 family qua production path với fake portal: `tests/integration/test_e2e_fake_source.py`
  (retry/429/403/timeout/MISSING-version/no-duplicate/RECONCILE/proof/serving/UI).
- Deep lanes offline: `tests/integration/test_deep_run.py` (capture/history/refresh, live BLOCKED).
- Chính sách mạng: HTTPS-only, allowlist một host, không theo redirect, scrub URL/token:
  `tests/integration/test_http_lane.py`.

## Không được làm

- Không gọi endpoint chưa có trong `SOURCES`/`filter_boundary_rules`.
- Không bypass CAPTCHA, không đổi User-Agent để né chặn, không xoay IP.
- Không ghi `canary_verified=1` khi chưa có bằng chứng.
- Không commit payload/URL có token vào repo (xem `tools/secret_scan.py`).