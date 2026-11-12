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
# The amount each family publishes, in display precedence order. Provenance names whichever field
# actually produced the value, so a note never describes a field the number did not come from.
_AMOUNT_FIELDS = {
    "KQLCNT": ("bidWinningPrice",),
    "HOP_DONG": ("ctPriceAfter",),
    "KHLCNT": ("bidPrice", "investTotal"),
}
# Only the result family publishes an awarded amount; a plan's package price and a contract's value
# belong to their own columns and must never be read back as gia_trung.
_AWARD_FAMILY = "KQLCNT"
_SECTORS = {"HH", "XL", "TV", "PTV", "HON_HOP"}


def _present_int(value):
    # Match the catalog's participant semantics: a plain non-negative int, never an array
    # or a fabricated zero. Missing/invalid stays NULL so zero and missing stay distinct.
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _sectors_json(fields):
    values = fields.get("investField", fields.get("bidField", []))
    values = values if isinstance(values, list) else [values]
    return json.dumps(sorted({v for v in values if isinstance(v, str)} & _SECTORS), ensure_ascii=False)


def _components_json(fields, family):
    if family != "KHLCNT":
        return None
    values = fields.get("bidName")
    if not isinstance(values, list):
        return None
    names = [v for v in values if isinstance(v, str) and v.strip()]
    return json.dumps(names, ensure_ascii=False) if names else None


def _text(fields, *keys):
    return next((fields[k] for k in keys if isinstance(fields.get(k), str) and fields[k]), None)


def _array_text(value):
    return json.dumps(value, ensure_ascii=False) if isinstance(value, list) else None


def _epoch(value):
    # Mirror the bridge's deadline_epoch: naive source timestamps are Vietnam time (UTC+7).
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone(timedelta(hours=7)))).timestamp()
    except ValueError:
        return None


def _public_string(fields, family):
    keys = ("publicDateKqlcnt", "published_at") if family == "KQLCNT" else ("publicDate", "published_at")
    return _text(fields, *keys)


def _winner_names(fields):
    return _array_text(fields.get("winningContractorName")) or _array_text(fields.get("winningName")) or _text(fields, "winningContractorName", "winningName", "winnerName")


def _provinces_json(province):
    names = list(dict.fromkeys(re.sub(r"^(?:Thành phố|Tỉnh|TP\.?)\s+", "", name.strip(), flags=re.I) for name in (province or "").split(";") if name.strip()))
    return json.dumps(names, ensure_ascii=False)


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
    candidates = _AMOUNT_FIELDS.get(family, ())
    field = next((name for name in candidates if fields.get(name) is not None),
                 candidates[0] if candidates else None)
    raw = fields.get(field) if field else None

    def awarded(value):
        return value if family == _AWARD_FAMILY else None

    currency_raw = fields.get("currency", fields.get("currencyCode"))
    if isinstance(currency_raw, list):
        currencies = {str(item).strip().upper() for item in currency_raw if isinstance(item, str) and item.strip()}
        mixed_currency = len(currencies) > 1
    else:
        mixed_currency = False
    provenance = {
        "source_key": source_key, "source_record_id": str(record_id), "content_hash": content_hash,
        "raw_hash": raw_hash, "source_field": field, "locator": f"$.{field}" if field else None,
        "currency_evidence": "mixed" if mixed_currency else "source" if currency_raw is not None else "missing",
    }
    if raw is None:
        provenance["calculation"] = "none"
        return None, None, "MISSING", json.dumps(provenance, ensure_ascii=False, sort_keys=True)
    if isinstance(raw, list):
        original = json.dumps(raw, ensure_ascii=False)
        numbers = [_amount(item) for item in raw]
        if not raw:
            provenance["calculation"] = "none"
            return None, None, "MISSING", json.dumps(provenance, ensure_ascii=False, sort_keys=True)
        if not all(number is not None and number >= 0 for number in numbers):
            provenance["calculation"] = "invalid"
            return original, None, "INVALID", json.dumps(provenance, ensure_ascii=False, sort_keys=True)
        if mixed_currency:
            # Several valid amounts in different currencies: real values that cannot be added up,
            # so the array is published verbatim and no total is claimed.
            provenance["calculation"] = "invalid"
            return original, None, "MULTIPLE_VALUES", json.dumps(provenance, ensure_ascii=False, sort_keys=True)
        provenance["calculation"] = "scalar" if len(numbers) == 1 else "sum"
        if family != _AWARD_FAMILY and len(numbers) > 1:
            # No total column exists for this family, so advertising a sum would describe a
            # number the reader cannot see.
            provenance["calculation"] = "invalid"
            return original, None, "MULTIPLE_VALUES", json.dumps(provenance, ensure_ascii=False, sort_keys=True)
        return (original, awarded(sum(numbers)),
                "KNOWN_SINGLE" if len(numbers) == 1 else "SUMMED_ARRAY",
                json.dumps(provenance, ensure_ascii=False, sort_keys=True))
    number = _amount(raw)
    if number is None or number < 0 or mixed_currency:
        provenance["calculation"] = "invalid"
        provenance["raw_value"] = raw
        return None, None, "INVALID", json.dumps(provenance, ensure_ascii=False, sort_keys=True)
    provenance["calculation"] = "scalar"
    return None, awarded(number), "KNOWN_SINGLE", json.dumps(provenance, ensure_ascii=False, sort_keys=True)


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
      notify_version TEXT,
      sectors_json TEXT,
      participants INTEGER,
      qualified INTEGER,
      close_date TEXT,
      sign_date TEXT,
      components_json TEXT,
      investor_name TEXT,
      winner_names_json TEXT,
      search_text TEXT,
      provinces_json TEXT,
      published_epoch REAL,
      closing_epoch REAL,
      signed_epoch REAL,
      is_latest INTEGER NOT NULL DEFAULT 0,
      status_display TEXT)""")
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
            close_value = _first_scalar(f, "bidCloseDate", "closeDate", "quoteDeadline")
            sign_value = _first_scalar(f, "contractSignDate")
            collection_value = _date(f.get("publicDateKqlcnt") if row[2] == "KQLCNT" else f.get("publicDate"))
            investor_name = _text(f, "investorName")
            winner_names = _winner_names(f)
            # q-search target mirrors the catalog's project() display precedence so the bridge
            # can match in SQL without re-reading raw; fold/normalize stays in the bridge UDF.
            search_text = " ".join(str(value or "") for value in [
                title, _text(f, "notifyNoStand", "notifyNo") or row[3], _text(f, "contractNo", "contractCode"),
                _text(f, "planNo"), investor_name, winner_names])
            published_epoch = _epoch(_public_string(f, row[2]) or collection_value)
            output.append((*row[:6], title, investor, row[6],
                _date(published),
                str(province) if province is not None else None,
                _first_scalar(f, "investorCode", "winningCode", "taxCode", "tax_id"),
                _first_scalar(f, "bidForm", "bidMode", "planType"),
                _amount(_first(f, "bidPrice", "investTotal")),
                _amount(_first(f, "bidWinningPrice", "ctPriceAfter")),
                _first_scalar(f, "currency", "currencyCode", "bidPriceUnit", "investTotalUnit"), fold_vietnamese(title), fold_vietnamese(investor),
                provinces[0][1] if len(provinces) == 1 else None,
                collection_value,
                status_raw, status_normalized, _codes_json(f.get("winningCode")),
                award_prices_json, gia_trung, price_status, price_provenance_json,
                # Relationship keys come from the raw payload: the display projection drops
                # planNo (TBMT/HOP_DONG) and notifyVersion (HOP_DONG), so normalized_json alone
                # cannot join plan/notice/result/contract edges.
                _first_scalar(f, "planNo"), _first_scalar(f, "notifyNo"), _first_scalar(f, "notifyVersion"),
                # Catalog enrichment projected from raw so the serving path never re-reads raw
                # objects per request; these fields are stripped by the display keep-lists.
                _sectors_json(f), _present_int(f.get("numBidderJoin")), _present_int(f.get("numBidderTech")),
                close_value, sign_value, _components_json(f, row[2]),
                investor_name, winner_names, search_text, _provinces_json(province),
                # Epochs mirror v1 project()'s str-only text() (an array date is missing to the
                # catalog), not _first_scalar, so v2 active()/date filters cannot diverge from v1.
                published_epoch, _epoch(_text(f, "bidCloseDate", "closeDate", "quoteDeadline")),
                _epoch(_text(f, "contractSignDate")), 0, _text(f, "status")))
        conn.executemany(
            "INSERT INTO record_fields (record_id,source_key,family,natural_key,version_state,version_raw,"
            "title_display,investor_display,last_seen_at_utc,published_date,province,tax_id,procurement_type,"
            "bid_price,award_price,currency,title_sort,investor_sort,province_fold,collection_date,"
            "status_raw,status_normalized,winning_codes_json,award_prices_json,gia_trung,price_status,price_provenance_json,"
            "plan_no,notify_no,notify_version,sectors_json,participants,qualified,close_date,sign_date,components_json,"
            "investor_name,winner_names_json,search_text,provinces_json,published_epoch,closing_epoch,signed_epoch,is_latest,status_display)"
            " VALUES(" + ",".join("?" * 45) + ")", output)
        conn.executemany("INSERT INTO record_provinces VALUES(?,?,?,?)", locations)
    if store:
        store.close()
    # Catalog/discovery dedup is precomputed offline so the serving path reads a prepared
    # latest-only index instead of running a window sort over ~1.17M rows per request. The
    # partition includes family to match the catalog's family-filtered ROW_NUMBER exactly.
    conn.execute("""UPDATE record_fields SET is_latest=1 WHERE record_id IN (
        SELECT record_id FROM (SELECT record_id,ROW_NUMBER() OVER (
          PARTITION BY source_key,family,natural_key ORDER BY
          CASE WHEN version_state!='KNOWN' OR version_raw IS NULL OR version_raw='' OR version_raw GLOB '*[^0-9]*' THEN 1 ELSE 0 END DESC,
          CAST(version_raw AS INTEGER) DESC,last_seen_at_utc DESC,record_id DESC) AS rank
        FROM record_fields) WHERE rank=1)""")
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
    conn.execute("CREATE INDEX ix_rf_family_close ON record_fields(family,close_date,record_id) WHERE close_date IS NOT NULL")
    conn.execute("CREATE INDEX ix_rf_family_participants ON record_fields(family,participants,record_id) WHERE participants IS NOT NULL")
    # Catalog aggregate/page path: latest-only rows per family, ordered by the family date basis.
    conn.execute("CREATE INDEX ix_rf_catalog_latest ON record_fields(family,is_latest,record_id)")
    conn.execute("CREATE INDEX ix_rf_catalog_close ON record_fields(family,is_latest,closing_epoch,record_id) WHERE is_latest=1")
    conn.execute("CREATE INDEX ix_rf_catalog_published ON record_fields(family,is_latest,published_epoch,record_id) WHERE is_latest=1")
    # Every catalog aggregate (summary, sectors, provinces, investors, timeline, page ids) reads
    # these columns for the whole family filter. Covering them keeps each pass an index-only scan;
    # the epoch indexes above are not covering and forced a random table lookup per row.
    conn.execute("CREATE INDEX ix_rf_catalog_agg ON record_fields("
                 "family,is_latest,record_id,version_state,version_raw,status_display,"
                 "published_epoch,closing_epoch,signed_epoch,participants,investor_name,"
                 "sectors_json,provinces_json,winner_names_json) WHERE is_latest=1")
    conn.execute("ANALYZE record_fields")
    conn.execute("CREATE INDEX ix_rp_name ON record_provinces(province_fold,record_id)")
    conn.execute("CREATE INDEX ix_rp_code ON record_provinces(province_code,record_id)")
    conn.execute("ANALYZE record_provinces")
