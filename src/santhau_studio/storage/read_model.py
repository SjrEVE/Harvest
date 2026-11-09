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


_OPEN_STATUS = {"01", "IS_PUBLISH", "OPEN_BID", "OPEN_DXTC", "OPEN_DXKT"}
_CANCELLED_STATUS = {"CANCEL_BID", "CANCELED"}
_AWARDED_STATUS = {"PUB_KQLCNT"}
_AMOUNT_FIELD = {"KQLCNT": "bidWinningPrice", "HOP_DONG": "ctPriceAfter", "KHLCNT": "bidPrice"}


def _status_projection(fields, family):
    raw = _first_scalar(fields, "statusForNotify", "status") if family == "KQLCNT" else _first_scalar(fields, "status", "statusForNotify")
    code = str(raw).strip().upper() if raw is not None else ""
    if family == "KQLCNT":
        return raw, "AWARDED" if code == "CNTTT" else "UNKNOWN"
    if family == "HOP_DONG":
        return raw, "AWARDED"
    if family in {"TBMT", "YCBG"}:
        if code in _OPEN_STATUS:
            return raw, "OPEN"
        if code in _CANCELLED_STATUS:
            return raw, "CANCELLED"
        if code in _AWARDED_STATUS:
            return raw, "AWARDED"
    return raw, "UNKNOWN"


def _codes_json(value):
    values = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    codes = list(dict.fromkeys(item.strip() for item in values if isinstance(item, str) and item.strip()))
    return json.dumps(codes, ensure_ascii=False) if codes else None


def _price_projection(fields, family, source_key, record_id, content_hash, raw_hash):
    field = _AMOUNT_FIELD.get(family)
    raw = fields.get(field) if field else None
    currency_raw = fields.get("currency", fields.get("currencyCode"))
    if isinstance(currency_raw, list):
        currencies = {str(item).strip().upper() for item in currency_raw if isinstance(item, str) and item.strip()}
        mixed_currency = len(currencies) > 1
    else:
        mixed_currency = False
    provenance = {
        "source_key": source_key, "source_record_id": record_id, "content_hash": content_hash,
        "raw_hash": raw_hash, "source_field": field, "locator": f"$.{field}" if field else None,
        "currency_evidence": "mixed" if mixed_currency else "source" if currency_raw is not None else "missing",
    }
    if raw is None:
        provenance["calculation"] = "none"
        return None, None, "MISSING", json.dumps(provenance, ensure_ascii=False, sort_keys=True)
    if isinstance(raw, list):
        original = json.dumps(raw, ensure_ascii=False)
        numbers = [_amount(item) for item in raw]
        valid = bool(raw) and not mixed_currency and all(number is not None and number >= 0 for number in numbers)
        if not valid:
            provenance["calculation"] = "invalid"
            return original, None, "INVALID" if raw or mixed_currency else "MISSING", json.dumps(provenance, ensure_ascii=False, sort_keys=True)
        provenance["calculation"] = "scalar" if len(numbers) == 1 else "sum"
        return original, sum(numbers), "KNOWN_SINGLE" if len(numbers) == 1 else "SUMMED_ARRAY", json.dumps(provenance, ensure_ascii=False, sort_keys=True)
    number = _amount(raw)
    if number is None or number < 0 or mixed_currency:
        provenance["calculation"] = "invalid"
        provenance["raw_value"] = raw
        return None, None, "INVALID", json.dumps(provenance, ensure_ascii=False, sort_keys=True)
    provenance["calculation"] = "scalar"
    return None, number, "KNOWN_SINGLE", json.dumps(provenance, ensure_ascii=False, sort_keys=True)


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
      bid_price REAL,award_price REAL,currency TEXT,title_sort TEXT,investor_sort TEXT,province_fold TEXT,
      collection_date TEXT,
      status_raw TEXT,
      status_normalized TEXT NOT NULL DEFAULT 'UNKNOWN',
      winning_codes_json TEXT,
      award_prices_json TEXT,
      gia_trung REAL,
      price_status TEXT NOT NULL DEFAULT 'MISSING',
      price_provenance_json TEXT,
      plan_no TEXT,
      notify_no TEXT,
      notify_version TEXT)""")
    cursor = conn.execute("SELECT r.id,r.source_key,r.family,r.natural_key,r.version_state,r.version_raw,r.last_seen_at_utc,v.normalized_json,r.current_record_object_sha,r.current_content_hash"
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
            status_raw, status_normalized = _status_projection(f, row[2])
            award_prices_json, gia_trung, price_status, price_provenance_json = _price_projection(
                f, row[2], row[1], row[0], row[9], row[8])
            output.append((*row[:6], title, investor, row[6],
                _date(published),
                str(province) if province is not None else None,
                _first_scalar(f, "investorCode", "winningCode", "taxCode", "tax_id"),
                _first_scalar(f, "bidForm", "bidMode", "planType"),
                _amount(_first(f, "bidPrice", "investTotal")),
                _amount(_first(f, "bidWinningPrice", "ctPriceAfter")),
                _first_scalar(f, "currency", "currencyCode", "bidPriceUnit", "investTotalUnit"), fold_vietnamese(title), fold_vietnamese(investor),
                provinces[0][1] if len(provinces) == 1 else None,
                _date(f.get("publicDateKqlcnt") if row[2] == "KQLCNT" else f.get("publicDate")),
                status_raw, status_normalized, _codes_json(f.get("winningCode")),
                award_prices_json, gia_trung, price_status, price_provenance_json,
                # Relationship keys come from the raw payload: the display projection drops
                # planNo (TBMT/HOP_DONG) and notifyVersion (HOP_DONG), so normalized_json alone
                # cannot join plan/notice/result/contract edges.
                _first_scalar(f, "planNo"), _first_scalar(f, "notifyNo"), _first_scalar(f, "notifyVersion")))
        conn.executemany(
            "INSERT INTO record_fields (record_id,source_key,family,natural_key,version_state,version_raw,"
            "title_display,investor_display,last_seen_at_utc,published_date,province,tax_id,procurement_type,"
            "bid_price,award_price,currency,title_sort,investor_sort,province_fold,collection_date,"
            "status_raw,status_normalized,winning_codes_json,award_prices_json,gia_trung,price_status,price_provenance_json,"
            "plan_no,notify_no,notify_version)"
            " VALUES(" + ",".join("?" * 30) + ")", output)
        conn.executemany("INSERT INTO record_provinces VALUES(?,?,?,?)", locations)
    if store:
        store.close()
    for field in ("family", "source_key", "natural_key", "published_date", "last_seen_at_utc", "title_sort",
                  "investor_sort", "tax_id", "province_fold", "procurement_type", "currency"):
        conn.execute(f"CREATE INDEX ix_rf_{field} ON record_fields({field},record_id)")
    for field in ("bid_price", "award_price"):
        conn.execute(f"CREATE INDEX ix_rf_{field} ON record_fields(COALESCE({field},-1),record_id)")
    conn.execute("CREATE INDEX ix_rf_family_seen ON record_fields(family,last_seen_at_utc,record_id)")
    conn.execute("CREATE INDEX ix_rf_collection_date ON record_fields(source_key,collection_date,family)")
    # Relationship joins probe these keys across ~1.17M records; partial indexes skip the
    # many NULL rows so the plan/notice/result/contract edge build stays indexed, not a scan.
    conn.execute("CREATE INDEX ix_rf_plan_no ON record_fields(plan_no,record_id) WHERE plan_no IS NOT NULL")
    conn.execute("CREATE INDEX ix_rf_notify ON record_fields(notify_no,notify_version,record_id) WHERE notify_no IS NOT NULL")
    conn.execute("ANALYZE record_fields")
    conn.execute("CREATE INDEX ix_rp_name ON record_provinces(province_fold,record_id)")
    conn.execute("CREATE INDEX ix_rp_code ON record_provinces(province_code,record_id)")
    conn.execute("ANALYZE record_provinces")
