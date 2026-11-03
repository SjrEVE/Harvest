"""Derived, disposable serving fields for indexed filtering; canonical data stays unchanged."""
from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from santhau_studio.domain.text import fold_vietnamese


def _first(fields: dict, *keys: str):
    return next((fields[k] for k in keys if fields.get(k) is not None), None)


def _amount(value):
    value = _scalar(value)
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def _scalar(value):
    if isinstance(value, list):
        value = value[0] if len(value) == 1 else None
    return value if isinstance(value, (str, int, float)) and not isinstance(value, bool) else None


def _first_scalar(fields, *keys):
    return next((value for key in keys if (value := _scalar(fields.get(key))) is not None), None)


def province_key(value):
    folded = fold_vietnamese(str(value).strip())
    return re.sub(r"^(?:thanh pho|tinh|tp\.?)\s+", "", folded)


def _provinces(fields):
    locations = fields.get("locations")
    result = {}
    if isinstance(locations, list):
        for row in locations:
            if not isinstance(row, dict):
                continue
            name = _first_scalar(row, "provName", "provinceName", "provCode")
            if name is not None:
                code = _first_scalar(row, "provCode", "provinceCode")
                result[(province_key(name), str(code) if code is not None else "")] = str(name)
    if not result:
        name = _first_scalar(fields, "provinceName", "province", "location", "provinceCode")
        if name is not None:
            result[(province_key(name), "")] = str(name)
    return [(name, key, code or None) for (key, code), name in result.items()]


def _date(value):
    value = _scalar(value)
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo:
            stamp = stamp.astimezone(timezone(timedelta(hours=7)))
        return stamp.date().isoformat()
    except ValueError:
        try:
            return datetime.strptime(value[:10], "%d/%m/%Y").date().isoformat()
        except ValueError:
            return None


def materialise_records(conn: sqlite3.Connection, raw_root=None) -> None:
    from santhau_studio.storage.rawstore import ObjectCorruptError, RawObjectStore
    store = RawObjectStore(raw_root, read_only=True) if raw_root else None

    @lru_cache(maxsize=128)
    def source_fields(sha):
        if store is None:
            return {}
        try:
            value = json.loads(store.read(sha))
            return value if isinstance(value, dict) else {}
        except (ObjectCorruptError, OSError, ValueError):
            return {}
    conn.execute("DROP TABLE IF EXISTS record_fields")
    conn.execute("DROP TABLE IF EXISTS record_provinces")
    conn.execute("CREATE TABLE record_provinces (record_id INTEGER NOT NULL,province TEXT,province_fold TEXT NOT NULL,province_code TEXT)")
    conn.execute("""CREATE TABLE record_fields (
      record_id INTEGER PRIMARY KEY, source_key TEXT, family TEXT, natural_key TEXT,
      version_state TEXT,version_raw TEXT,title_display TEXT,investor_display TEXT,
      last_seen_at_utc TEXT,published_date TEXT,province TEXT,tax_id TEXT,procurement_type TEXT,
      bid_price REAL,award_price REAL,currency TEXT,title_sort TEXT,investor_sort TEXT,province_fold TEXT)""")
    cursor = conn.execute("SELECT r.id,r.source_key,r.family,r.natural_key,r.version_state,r.version_raw,r.last_seen_at_utc,v.normalized_json,r.current_record_object_sha"
                          " FROM source_records r LEFT JOIN source_record_versions v ON v.source_record_id=r.id"
                          " AND v.content_version_seq=r.current_content_version ORDER BY r.id")
    while rows := cursor.fetchmany(2000):
        output = []
        locations = []
        for row in rows:
            f = {**source_fields(row[8]), **json.loads(row[7] or "{}")}
            title = str(_first_scalar(f, "title", "bidName", "name", "pname", "ctName") or "")
            investor = str(_first_scalar(f, "investorName", "procuringEntityName", "buyer_name") or "")
            provinces = _provinces(f)
            locations.extend((row[0], *value) for value in provinces)
            province = "; ".join(dict.fromkeys(value[0] for value in provinces)) or None
            published = (_first(f, "publicDateKqlcnt", "published_at") if row[2] == "KQLCNT"
                         else _first(f, "publicDate", "publicDateKqlcnt", "decisionDate", "contractSignDate", "published_at"))
            output.append((*row[:6], title, investor, row[6],
                _date(published),
                str(province) if province is not None else None,
                _first_scalar(f, "investorCode", "winningCode", "taxCode", "tax_id"),
                _first_scalar(f, "bidForm", "bidMode", "planType"),
                _amount(_first(f, "bidPrice", "investTotal")),
                _amount(_first(f, "bidWinningPrice", "ctPriceAfter")),
                _first_scalar(f, "currency", "currencyCode", "bidPriceUnit", "investTotalUnit"), fold_vietnamese(title), fold_vietnamese(investor),
                provinces[0][1] if len(provinces) == 1 else None))
        conn.executemany("INSERT INTO record_fields VALUES(" + ",".join("?" * 19) + ")", output)
        conn.executemany("INSERT INTO record_provinces VALUES(?,?,?,?)", locations)
    if store:
        store.close()
    for field in ("family", "source_key", "natural_key", "published_date", "last_seen_at_utc", "title_sort",
                  "investor_sort", "tax_id", "province_fold", "procurement_type", "currency"):
        conn.execute(f"CREATE INDEX ix_rf_{field} ON record_fields({field},record_id)")
    for field in ("bid_price", "award_price"):
        conn.execute(f"CREATE INDEX ix_rf_{field} ON record_fields(COALESCE({field},-1),record_id)")
    conn.execute("CREATE INDEX ix_rf_family_seen ON record_fields(family,last_seen_at_utc,record_id)")
    conn.execute("ANALYZE record_fields")
    conn.execute("CREATE INDEX ix_rp_name ON record_provinces(province_fold,record_id)")
    conn.execute("CREATE INDEX ix_rp_code ON record_provinces(province_code,record_id)")
    conn.execute("ANALYZE record_provinces")
