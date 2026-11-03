"""Bounded history and verified raw previews for the read-only desktop."""
from __future__ import annotations

import re
from contextlib import closing

from santhau_studio.storage.rawstore import ObjectCorruptError, RawObjectStore

HISTORY = {
    "versions": ("source_record_versions", "content_version_seq"),
    "raw_revisions": ("raw_revisions", "revision_no"),
    "lots": ("lots", "id"), "goods_rows": ("goods_rows", "id"),
    "documents": ("documents", "id"), "contractor_memberships": ("contractor_memberships", "id"),
    "deep_history": ("(SELECT h.*,d.source_record_id,d.lane,d.endpoint_kind FROM deep_capture_history h JOIN deep_captures d ON d.id=h.deep_capture_id)", "id"),
}


def history(conn, payload: dict) -> dict:
    kind = payload.get("kind", "versions")
    if kind not in HISTORY:
        return {"rows": [], "error_code": "INVALID_HISTORY", "error": "Loại lịch sử không hợp lệ."}
    table, sequence = HISTORY[kind]
    try:
        record_id = int(payload.get("record_id", 0))
        limit = min(100, max(1, int(payload.get("limit", 50))))
        before = int(payload.get("before") or 2**63 - 1)
    except (ValueError, TypeError):
        return {"rows": [], "error_code": "INVALID_HISTORY", "error": "Trang lịch sử không hợp lệ."}
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM {table} WHERE source_record_id=? AND {sequence}<? ORDER BY {sequence} DESC LIMIT ?",
        (record_id, before, limit + 1))]
    more = len(rows) > limit
    rows = rows[:limit]
    return {"rows": rows, "has_more": more, "before": rows[-1][sequence] if more else None, "kind": kind}


def raw_preview(root, conn, sha: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        return {"error_code": "INVALID_HASH", "error": "SHA256 không hợp lệ."}
    row = conn.execute("SELECT byte_len,integrity_status FROM raw_objects WHERE sha256=?", (sha,)).fetchone()
    if row is None:
        return {"error_code": "NOT_FOUND", "error": "Object không thuộc snapshot này."}
    # Avoid allocating an arbitrarily large response on a UI preview request.
    if row[0] > 16 * 1024 * 1024:
        return {"error_code": "PREVIEW_TOO_LARGE", "error": "Object vượt 16 MiB; dùng bản xuất để xem toàn bộ.", "byte_len": row[0]}
    try:
        with closing(RawObjectStore(root, read_only=True)) as store:
            data = store.read(sha)
        return {"sha256": sha, "verified": True, "byte_len": len(data), "truncated": len(data) > 256_000,
                "text": data[:256_000].decode("utf-8", errors="replace")}
    except (ObjectCorruptError, OSError) as exc:
        return {"error_code": "CORRUPT", "verified": False, "error": str(exc)}
