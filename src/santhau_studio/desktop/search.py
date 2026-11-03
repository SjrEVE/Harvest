"""Parameterized indexed search with explicit unknown semantics and snapshot-bound cursors."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import sqlite3
from datetime import date
from pathlib import Path

from santhau_studio.domain.text import fold_vietnamese

SORTS = {"record_id": "r.record_id", "natural_key": "r.natural_key", "title": "r.title_sort",
         "published_date": "COALESCE(r.published_date,'')", "updated": "r.last_seen_at_utc",
         "price": "COALESCE(r.bid_price,-1)", "award_price": "COALESCE(r.award_price,-1)"}


def _bound(value, default, low, high):
    try:
        return min(high, max(low, int(value)))
    except (ValueError, TypeError):
        return default


def search(conn: sqlite3.Connection, path: Path, payload: dict) -> dict:
    text = str(payload.get("text") or "").strip()
    filters = ("family", "source", "province", "tax_id", "investor", "date_from", "date_to", "type", "price_state", "price_min", "price_max", "currency")
    if not text and not payload.get("browse") and not any(payload.get(k) is not None and payload.get(k) != "" for k in filters):
        return {"hits": [], "note": "EMPTY_QUERY", "next_cursor": None}
    sort = str(payload.get("sort") or "record_id")
    if sort not in SORTS:
        return {"hits": [], "error_code": "INVALID_SORT", "error": "Kiểu sắp xếp không hợp lệ."}
    direction = "ASC" if payload.get("direction") == "asc" else "DESC"
    expression = SORTS[sort]
    if (sort in ('price','award_price') or any(payload.get(k) not in (None,'') for k in ('price_min','price_max'))) and not payload.get('currency'):
        return {'hits':[], 'error_code':'CURRENCY_REQUIRED', 'error':'Chọn tiền tệ trước khi lọc hoặc sắp xếp theo giá.'}
    limit = _bound(payload.get("limit", 50), 50, 1, 200)
    offset = _bound(payload.get("offset", 0), 0, 0, 100_000)
    fingerprint = hashlib.sha256(json.dumps({k: v for k, v in payload.items() if k not in ("cursor", "offset")},
                                           sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    stat = path.stat()
    snapshot = f"{stat.st_mtime_ns}:{stat.st_size}"
    clauses, params = [], []
    for field, column in (("family", "family"), ("source", "source_key"), ("tax_id", "tax_id"), ("type", "procurement_type"), ("currency", "currency")):
        if payload.get(field) not in (None, ""):
            clauses.append(f"r.{column}=?")
            params.append(str(payload[field]).strip())
    if payload.get("province"):
        clauses.append("r.province_fold=?")
        params.append(fold_vietnamese(str(payload["province"])))
    if payload.get("investor"):
        clauses.append("r.investor_sort LIKE ? ESCAPE '\\'")
        value = fold_vietnamese(str(payload["investor"]))
        params.append("%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    for field, operator in (("date_from", ">="), ("date_to", "<=")):
        if payload.get(field):
            clauses.append(f"r.published_date {operator} ?")
            params.append(str(payload[field])[:10])
    try:
        for field in ('date_from','date_to'):
            if payload.get(field):
                date.fromisoformat(str(payload[field]))
        if payload.get('date_from') and payload.get('date_to') and payload['date_from'] > payload['date_to']:
            raise ValueError()
    except (ValueError,TypeError):
        return {'hits':[], 'error_code':'INVALID_DATE','error':'Ngày không hợp lệ hoặc khoảng ngày bị đảo.'}
    price = "award_price" if payload.get("price_kind") == "award" else "bid_price"
    presence = str(payload.get("price_state") or "").upper()
    if presence in ("KNOWN", "UNKNOWN"):
        clauses.append(f"r.{price} IS " + ("NOT NULL" if presence == "KNOWN" else "NULL"))
    for field, operator in (("price_min", ">="), ("price_max", "<=")):
        if payload.get(field) not in (None, ""):
            try:
                value = float(payload[field])
                if not math.isfinite(value):
                    raise ValueError
            except (ValueError, TypeError):
                return {"hits": [], "error_code": "INVALID_AMOUNT", "error": "Khoảng giá không hợp lệ."}
            clauses.append(f"r.{price}{operator}?")
            params.append(value)
    tokens = re.findall(r"[a-z0-9]+", fold_vietnamese(text))
    if text and not tokens:
        return {"hits":[], "note":"EMPTY_QUERY", "next_cursor":None}
    match = " AND ".join(token + "*" for token in tokens)
    source = "record_fields r"
    order_expression = expression
    if match:
        source = "search_index CROSS JOIN record_fields r ON r.record_id=search_index.rowid"
        if sort == "record_id":
            order_expression = "search_index.rowid"
        clauses.append("search_index MATCH ?")
        params.append(match)
    if payload.get("cursor"):
        try:
            cursor = json.loads(base64.urlsafe_b64decode(str(payload["cursor"])))
            if cursor["query"] != fingerprint:
                raise ValueError
            if cursor["snapshot"] != snapshot:
                return {"hits": [], "error_code": "CURSOR_EXPIRED", "error": "Dữ liệu vừa cập nhật; tìm lại từ trang đầu."}
            comparison = ">" if direction == "ASC" else "<"
            clauses.append(f"({expression},r.record_id) {comparison} (?,?)")
            params.extend(cursor["after"])
            offset = 0
        except (ValueError, TypeError, KeyError):
            return {"hits": [], "error_code": "INVALID_CURSOR", "error": "Trang kết quả không khớp bộ lọc hiện tại."}
    where = " AND ".join(clauses) or "1"
    order = f"{order_expression} {direction}" + (f",r.record_id {direction}" if sort != "record_id" else "")
    sql = f"SELECT r.*, {expression} AS _sort FROM {source} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?"
    rows = [dict(row) for row in conn.execute(sql, (*params, limit + 1, offset))]
    more = len(rows) > limit
    rows = rows[:limit]
    cursor = None
    if more and rows:
        cursor = base64.urlsafe_b64encode(json.dumps({"query": fingerprint, "snapshot": snapshot,
            "after": [rows[-1]["_sort"], rows[-1]["record_id"]]}).encode()).decode()
    for row in rows:
        row.pop("_sort", None)
        row["price_state"] = "KNOWN" if row[price] is not None else "UNKNOWN"
    return {"query": text, "hits": rows, "limit": limit, "offset": offset, "next_cursor": cursor,
            "has_more": more, "snapshot": snapshot, "note": None if rows else "NO_MATCH"}
