"""Indexed all-version listing dates derived from the current source payloads."""
from __future__ import annotations

import json
import sqlite3

_MARKET_FIELDS = (
    "source_url", "sourceUrl", "notifyNoStand", "notifyNo", "investorName", "investorCode",
    "procuringEntityName", "winningContractorName", "winningName", "winnerName", "winningCode",
    "bidWinningPrice", "ctPriceAfter", "contractNo", "contractCode", "contractSignDate",
    "publicDate", "publicDateKqlcnt", "published_at", "bidCloseDate", "closeDate", "quoteDeadline",
    "decisionDate", "status", "bidForm", "planNo", "bidId", "id",
)


def market_fields_json(fields: dict) -> str:
    return json.dumps({key: fields[key] for key in _MARKET_FIELDS if key in fields},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

_DATE = """COALESCE(CASE WHEN f.family='KQLCNT'
    THEN COALESCE(json_extract(v.normalized_json,'$.publicDateKqlcnt'),json_extract(v.normalized_json,'$.published_at'))
    ELSE COALESCE(json_extract(v.normalized_json,'$.publicDate'),json_extract(v.normalized_json,'$.published_at')) END,f.collection_date)"""
_INDEXES = {
    "ix_rf_listing_family_date": [("family", 0), ("listing_public_date", 1), ("record_id", 1)],
    "ix_rf_listing_date": [("listing_public_date", 1), ("record_id", 1)],
}
SEARCH_COLUMNS = (("listing_public_date", 1), ("record_id", 1), ("family", 0),
                  ("title_display", 0), ("natural_key", 0), ("version_raw", 0),
                  ("investor_display", 0), ("province", 0), ("sectors_json", 0))


def materialise_listing_search(conn: sqlite3.Connection) -> None:
    """Add a covering index without changing source or derived row values."""
    conn.execute("CREATE INDEX IF NOT EXISTS ix_rf_listing_search ON record_fields(" +
                 ",".join(column + (" DESC" if descending else "")
                          for column, descending in SEARCH_COLUMNS) + ")")
    shape = [(row[2], row[3], row[4]) for row in conn.execute("PRAGMA index_xinfo(ix_rf_listing_search)") if row[5]]
    partial = next(row[4] for row in conn.execute("PRAGMA index_list(record_fields)")
                   if row[1] == "ix_rf_listing_search")
    if shape != [(column, descending, "BINARY") for column, descending in SEARCH_COLUMNS] or partial:
        raise ValueError("listing search index is invalid")


def validate_listing(conn: sqlite3.Connection, built_at: str, *, check_values: bool = False) -> dict:
    row = conn.execute("SELECT listing_schema_version,record_count,snapshot_built_at_utc FROM serving_listing_meta WHERE singleton_id=1").fetchone()
    columns = {c[1] for c in conn.execute("PRAGMA table_info(record_fields)")}
    count = conn.execute("SELECT COUNT(*) FROM record_fields").fetchone()[0]
    if row is None or tuple(row) != (1, count, built_at) or not {"listing_public_date", "sectors_json", "market_fields_json"} <= columns:
        raise ValueError("listing projection metadata is invalid")
    for name, expected in _INDEXES.items():
        actual = [(r[2], r[3]) for r in conn.execute(f"PRAGMA index_xinfo({name})") if r[5]]
        if actual != expected:
            raise ValueError("listing projection index is invalid")
    if check_values:
        invalid = conn.execute("SELECT 1 FROM record_fields WHERE "
            "CASE WHEN json_valid(market_fields_json) THEN json_type(market_fields_json)<>'object' ELSE 1 END OR "
            "CASE WHEN json_valid(sectors_json) THEN json_type(sectors_json)<>'array' ELSE 1 END LIMIT 1").fetchone()
        if invalid:
            raise ValueError("listing display projection is invalid")
        mismatch = conn.execute("SELECT COUNT(*) FROM record_fields f JOIN source_records r ON r.id=f.record_id "
            "JOIN source_record_versions v ON v.source_record_id=r.id AND v.content_version_seq=r.current_content_version "
            "WHERE f.listing_public_date IS NOT (" + _DATE + ")").fetchone()[0]
        if mismatch:
            raise ValueError("listing public date parity failed")
    return {"listing_schema_version": 1, "record_count": count, "snapshot_built_at_utc": built_at}


def materialise_listing(conn: sqlite3.Connection, *, snapshot_built_at_utc: str) -> dict:
    # No type affinity: retain the legacy JSON expression's exact SQL value and ordering.
    conn.execute("ALTER TABLE record_fields ADD COLUMN listing_public_date")
    conn.execute("UPDATE record_fields AS f SET listing_public_date=(SELECT " + _DATE +
        " FROM source_records r JOIN source_record_versions v ON v.source_record_id=r.id "
        "AND v.content_version_seq=r.current_content_version WHERE r.id=f.record_id)")
    conn.execute("CREATE INDEX ix_rf_listing_family_date ON record_fields(family,listing_public_date DESC,record_id DESC)")
    conn.execute("CREATE INDEX ix_rf_listing_date ON record_fields(listing_public_date DESC,record_id DESC)")
    materialise_listing_search(conn)
    conn.execute("ANALYZE record_fields")
    conn.execute("CREATE TABLE serving_listing_meta(singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),"
        "listing_schema_version INTEGER NOT NULL,record_count INTEGER NOT NULL,snapshot_built_at_utc TEXT NOT NULL)")
    conn.execute("INSERT INTO serving_listing_meta VALUES(1,1,(SELECT COUNT(*) FROM record_fields),?)", (snapshot_built_at_utc,))
    return validate_listing(conn, snapshot_built_at_utc, check_values=True)
