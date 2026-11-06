"""Explain the missing-only upgrade so the UI and CLI do not promise silent work."""

import json
import sqlite3


def _load(raw):
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def policy_notice(conn):
    """Read the saved flags. A missing setting means the missing-only default."""
    refresh_expired = False
    reconcile_enabled = False
    retained = False
    if conn is not None:
        try:
            rows = dict(
                conn.execute(
                    "SELECT key, value_json FROM settings WHERE key IN (?,?,?)",
                    ("automatic_public_deep", "collection_scope_v1", "pre_missing_only_policy"),
                )
            )
        except sqlite3.Error:
            rows = {}
        deep = _load(rows.get("automatic_public_deep"))
        scope = _load(rows.get("collection_scope_v1"))
        refresh_expired = deep.get("refresh_expired") is True
        reconcile_enabled = (scope.get("policy") or {}).get("reconcile_enabled") is True
        retained = "pre_missing_only_policy" in rows
    if refresh_expired:
        refresh_vi = "Bản chi tiết hết hạn được tự lấy lại."
        refresh_en = "Expired deep captures are refreshed automatically."
    else:
        refresh_vi = "Bản chi tiết hết hạn không tự lấy lại."
        refresh_en = "Expired deep captures are not refreshed automatically."
    if reconcile_enabled:
        reconcile_vi = "Đối chiếu tự động đang bật."
        reconcile_en = "Scheduled reconcile is on."
    else:
        reconcile_vi = "Đối chiếu tự động đang tắt; chọn Đối chiếu lại dữ liệu khi cần chạy một lượt."
        reconcile_en = "Scheduled reconcile is off; an explicit RECONCILE job still runs when requested."
    kept = (
        " Giá trị cũ trước nâng cấp nằm trong pre_missing_only_policy."
        if retained
        else ""
    )
    kept_en = " Previous values are kept in pre_missing_only_policy." if retained else ""
    return {
        "mode": "MISSING_ONLY",
        "refresh_expired": refresh_expired,
        "reconcile_enabled": reconcile_enabled,
        "refresh_recent_default": False,
        "previous_policy_retained": retained,
        "notice_vi": (
            "Chính sách đang dùng: chỉ lấy phần còn thiếu. "
            + refresh_vi
            + " "
            + reconcile_vi
            + " Cập nhật danh sách bỏ qua ngày đã đủ bằng chứng, trừ khi chọn đọc lại."
            + kept
        ),
        "notice_en": (
            "Active policy is missing-only. "
            + refresh_en
            + " "
            + reconcile_en
            + " Incremental listing skips proven days unless refresh_recent is set."
            + kept_en
        ),
    }
