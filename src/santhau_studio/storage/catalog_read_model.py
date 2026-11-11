"""Snapshot-time, exact catalog aggregates and narrow KHLCNT rows for Radar."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

CATALOG_SCHEMA_VERSION = 1
PREPARED_FAMILIES: tuple[str, ...] = ()
CATALOG_FAMILIES = ("TBMT", "YCBG", "DU_AN", "KHLCNT", "KQLCNT", "HOP_DONG")
VALID_SECTORS = ("HH", "XL", "TV", "PTV", "HON_HOP")
DEFAULT_INVESTOR = "Chưa rõ đơn vị"
DEFAULT_PROVINCE = "Chưa rõ địa bàn"


@dataclass(frozen=True)
class CatalogBuild:
    metadata: dict
    family_latest_counts: dict[str, int]


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _dimension_values(conn: sqlite3.Connection, kind: str) -> set[tuple[int, str]]:
    return {
        (int(record_id), str(value))
        for record_id, value in conn.execute(
            "SELECT record_id,dimension_value FROM serving_catalog_dimension_memberships"
            " WHERE dimension_kind=? ORDER BY record_id,dimension_value",
            (kind,),
        )
    }


def _expected_dimensions(conn: sqlite3.Connection, kind: str) -> set[tuple[int, str]]:
    if kind == "sector":
        source_values = {
            (int(record_id), str(value))
            for record_id, sectors_json in conn.execute(
                "SELECT record_id,sectors_json FROM record_fields"
                " WHERE family='KHLCNT' AND is_latest=1 ORDER BY record_id"
            )
            for value in json.loads(sectors_json or "[]")
            if value in VALID_SECTORS
        }
    elif kind == "province":
        source_values = {
            (int(record_id), str(value))
            for record_id, provinces_json in conn.execute(
                "SELECT record_id,provinces_json FROM record_fields"
                " WHERE family='KHLCNT' AND is_latest=1 ORDER BY record_id"
            )
            for value in json.loads(provinces_json or "[]")
            if isinstance(value, str) and value.strip()
        }
    elif kind == "investor":
        source_values = {
            (int(record_id), str(investor or DEFAULT_INVESTOR))
            for record_id, investor in conn.execute(
                "SELECT record_id,investor_name FROM record_fields"
                " WHERE family='KHLCNT' AND is_latest=1 ORDER BY record_id"
            )
        }
    else:
        raise ValueError(f"unsupported catalog dimension: {kind}")

    if kind in {"sector", "province"}:
        with_dimensions = {record_id for record_id, _ in source_values}
        defaults = {
            (int(record_id), "UNKNOWN" if kind == "sector" else DEFAULT_PROVINCE)
            for (record_id,) in conn.execute(
                "SELECT record_id FROM record_fields WHERE family='KHLCNT' AND is_latest=1"
            )
            if int(record_id) not in with_dimensions
        }
        source_values |= defaults
    return source_values


def _insert_dimensions(conn: sqlite3.Connection) -> None:
    placeholders = ",".join("?" for _ in VALID_SECTORS)
    conn.execute(
        "INSERT INTO serving_catalog_dimension_memberships(record_id,dimension_kind,dimension_value)"
        " SELECT DISTINCT f.record_id,'sector',je.value"
        " FROM record_fields f,json_each(COALESCE(f.sectors_json,'[]')) je"
        f" WHERE f.family='KHLCNT' AND f.is_latest=1 AND je.type='text' AND je.value IN ({placeholders})",
        VALID_SECTORS,
    )
    conn.execute(
        "INSERT INTO serving_catalog_dimension_memberships(record_id,dimension_kind,dimension_value)"
        " SELECT DISTINCT f.record_id,'sector','UNKNOWN' FROM record_fields f"
        " WHERE f.family='KHLCNT' AND f.is_latest=1 AND NOT EXISTS ("
        " SELECT 1 FROM json_each(COALESCE(f.sectors_json,'[]')) je"
        f" WHERE je.type='text' AND je.value IN ({placeholders}))",
        VALID_SECTORS,
    )
    conn.execute(
        "INSERT INTO serving_catalog_dimension_memberships(record_id,dimension_kind,dimension_value)"
        " SELECT DISTINCT f.record_id,'province',je.value"
        " FROM record_fields f,json_each(COALESCE(f.provinces_json,'[]')) je"
        " WHERE f.family='KHLCNT' AND f.is_latest=1 AND je.type='text' AND trim(je.value)<>''"
    )
    conn.execute(
        "INSERT INTO serving_catalog_dimension_memberships(record_id,dimension_kind,dimension_value)"
        " SELECT f.record_id,'province',? FROM record_fields f"
        " WHERE f.family='KHLCNT' AND f.is_latest=1 AND NOT EXISTS ("
        " SELECT 1 FROM json_each(COALESCE(f.provinces_json,'[]')) je"
        " WHERE je.type='text' AND trim(je.value)<>'')",
        (DEFAULT_PROVINCE,),
    )
    conn.execute(
        "INSERT INTO serving_catalog_dimension_memberships(record_id,dimension_kind,dimension_value)"
        " SELECT record_id,'investor',CASE WHEN investor_name IS NULL OR investor_name='' THEN ?"
        " ELSE investor_name END FROM serving_catalog_records",
        (DEFAULT_INVESTOR,),
    )
    conn.execute(
        "INSERT INTO serving_catalog_dimension_counts(family,dimension_kind,dimension_value,record_count)"
        " SELECT 'KHLCNT',dimension_kind,dimension_value,COUNT(*)"
        " FROM serving_catalog_dimension_memberships GROUP BY dimension_kind,dimension_value"
    )


def _insert_summary_and_timeline(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO serving_catalog_family_summary (family,total_records,known_dates,missing_dates,"
        "with_winners,investor_count,positive,zero,missing,bucket_1,bucket_2,bucket_3_5,bucket_6_plus)"
        " SELECT 'KHLCNT',COUNT(*),"
        " COALESCE(SUM(published_epoch IS NOT NULL),0),COALESCE(SUM(published_epoch IS NULL),0),"
        " COALESCE(SUM(winner_names_json IS NOT NULL AND winner_names_json NOT IN ('[]','')),0),"
        " COUNT(DISTINCT CASE WHEN COALESCE(NULLIF(investor_name,''),?)<>?"
        " THEN COALESCE(NULLIF(investor_name,''),?) END),"
        " COALESCE(SUM(participants>0),0),COALESCE(SUM(participants=0),0),"
        " COALESCE(SUM(participants IS NULL),0),COALESCE(SUM(participants=1),0),"
        " COALESCE(SUM(participants=2),0),COALESCE(SUM(participants BETWEEN 3 AND 5),0),"
        " COALESCE(SUM(participants>=6),0) FROM serving_catalog_records",
        (DEFAULT_INVESTOR, DEFAULT_INVESTOR, DEFAULT_INVESTOR),
    )
    conn.execute(
        "INSERT INTO serving_catalog_time_buckets(family,bucket_kind,bucket_key,record_count)"
        " SELECT 'KHLCNT','month',strftime('%Y-%m',published_epoch,'unixepoch','+7 hours'),COUNT(*)"
        " FROM serving_catalog_records WHERE published_epoch IS NOT NULL"
        " GROUP BY strftime('%Y-%m',published_epoch,'unixepoch','+7 hours') ORDER BY 3"
    )


def _verify_parity(conn: sqlite3.Connection, family_latest_counts: dict[str, int]) -> None:
    expected = family_latest_counts.get("KHLCNT", 0)
    actual_records = _count(conn, "serving_catalog_records")
    summary = conn.execute(
        "SELECT total_records,known_dates,missing_dates FROM serving_catalog_family_summary WHERE family='KHLCNT'"
    ).fetchone()
    if actual_records != expected or summary is None or summary["total_records"] != expected:
        raise ValueError(f"KHLCNT latest row-count mismatch: expected {expected}, projection {actual_records}")
    if summary["known_dates"] + summary["missing_dates"] != expected:
        raise ValueError("KHLCNT date aggregate does not cover the prepared family")
    bucket_total = conn.execute(
        "SELECT COALESCE(SUM(record_count),0) FROM serving_catalog_time_buckets WHERE family='KHLCNT'"
    ).fetchone()[0]
    if int(bucket_total) != int(summary["known_dates"]):
        raise ValueError("KHLCNT monthly buckets do not match known-date rows")
    for kind in ("sector", "province", "investor"):
        if _dimension_values(conn, kind) != _expected_dimensions(conn, kind):
            raise ValueError(f"KHLCNT {kind} dimension membership drift")
    dimension_counts = {
        (row["dimension_kind"], row["dimension_value"]): int(row["record_count"])
        for row in conn.execute(
            "SELECT dimension_kind,dimension_value,record_count"
            " FROM serving_catalog_dimension_counts WHERE family='KHLCNT'"
        )
    }
    calculated_counts: dict[tuple[str, str], int] = {}
    for kind, value in conn.execute(
        "SELECT dimension_kind,dimension_value FROM serving_catalog_dimension_memberships"
    ):
        key = (kind, value)
        calculated_counts[key] = calculated_counts.get(key, 0) + 1
    if dimension_counts != calculated_counts:
        raise ValueError("KHLCNT dimension aggregate drift")


def materialise_catalog(
    conn: sqlite3.Connection,
    *,
    snapshot_built_at_utc: str,
    source_projection_version: int,
) -> CatalogBuild:
    """Build exact KHLCNT snapshot aggregates; the family remains on fallback until search is ready."""
    for table in (
        "serving_catalog_meta",
        "serving_catalog_family_summary",
        "serving_catalog_time_buckets",
        "serving_catalog_dimension_counts",
        "serving_catalog_dimension_memberships",
        "serving_catalog_records",
    ):
        conn.execute(f'DROP TABLE IF EXISTS "{table}"')

    conn.execute(
        "CREATE TABLE serving_catalog_records ("
        " record_id INTEGER PRIMARY KEY,"
        " published_epoch REAL,closing_epoch REAL,signed_epoch REAL,"
        " version_known INTEGER NOT NULL CHECK(version_known IN (0,1)),"
        " status_display TEXT,title_display TEXT,winner_names_json TEXT,investor_name TEXT,participants INTEGER,search_text TEXT,"
        " search_text_folded TEXT NOT NULL DEFAULT '',"
        " CHECK(participants IS NULL OR participants>=0))"
    )
    conn.execute(
        "INSERT INTO serving_catalog_records(record_id,published_epoch,closing_epoch,signed_epoch,version_known,"
        "status_display,title_display,winner_names_json,investor_name,participants,search_text)"
        " SELECT record_id,published_epoch,closing_epoch,signed_epoch,"
        " CASE WHEN version_state='KNOWN' AND version_raw IS NOT NULL AND version_raw<>''"
        " AND version_raw NOT GLOB '*[^0-9]*' THEN 1 ELSE 0 END,"
        " status_display,title_display,winner_names_json,investor_name,participants,search_text"
        " FROM record_fields WHERE family='KHLCNT' AND is_latest=1 ORDER BY record_id"
    )
    conn.execute(
        "CREATE INDEX ix_scr_published ON serving_catalog_records(published_epoch DESC,record_id DESC)"
    )
    conn.execute("CREATE INDEX ix_scr_closing ON serving_catalog_records(closing_epoch,record_id)")
    conn.execute("CREATE INDEX ix_scr_investor ON serving_catalog_records(investor_name,record_id)")
    conn.execute("CREATE INDEX ix_scr_participants ON serving_catalog_records(participants,record_id)")
    conn.execute("ANALYZE serving_catalog_records")

    conn.execute(
        "CREATE TABLE serving_catalog_family_summary ("
        " family TEXT PRIMARY KEY,total_records INTEGER NOT NULL,known_dates INTEGER NOT NULL,missing_dates INTEGER NOT NULL,"
        " with_winners INTEGER NOT NULL,investor_count INTEGER NOT NULL,positive INTEGER NOT NULL,zero INTEGER NOT NULL,"
        " missing INTEGER NOT NULL,bucket_1 INTEGER NOT NULL,bucket_2 INTEGER NOT NULL,bucket_3_5 INTEGER NOT NULL,"
        " bucket_6_plus INTEGER NOT NULL) WITHOUT ROWID"
    )
    conn.execute(
        "CREATE TABLE serving_catalog_time_buckets ("
        " family TEXT NOT NULL,bucket_kind TEXT NOT NULL,bucket_key TEXT NOT NULL,record_count INTEGER NOT NULL,"
        " PRIMARY KEY(family,bucket_kind,bucket_key)) WITHOUT ROWID"
    )
    conn.execute(
        "CREATE TABLE serving_catalog_dimension_memberships ("
        " record_id INTEGER NOT NULL,dimension_kind TEXT NOT NULL CHECK(dimension_kind IN ('sector','province','investor')),"
        " dimension_value TEXT NOT NULL,PRIMARY KEY(record_id,dimension_kind,dimension_value)) WITHOUT ROWID"
    )
    conn.execute(
        "CREATE TABLE serving_catalog_dimension_counts ("
        " family TEXT NOT NULL,dimension_kind TEXT NOT NULL,dimension_value TEXT NOT NULL,record_count INTEGER NOT NULL,"
        " PRIMARY KEY(family,dimension_kind,dimension_value)) WITHOUT ROWID"
    )
    _insert_dimensions(conn)
    _insert_summary_and_timeline(conn)
    conn.execute(
        "CREATE INDEX ix_scd_lookup ON serving_catalog_dimension_memberships"
        "(dimension_kind,dimension_value,record_id)"
    )
    _verify_parity(
        conn,
        {
            family: int(count)
            for family, count in conn.execute(
                "SELECT family,COUNT(*) FROM record_fields WHERE is_latest=1 GROUP BY family"
            )
        },
    )

    family_latest_counts = dict.fromkeys(CATALOG_FAMILIES, 0)
    family_latest_counts.update(
        {
            family: int(count)
            for family, count in conn.execute(
                "SELECT family,COUNT(*) FROM record_fields WHERE is_latest=1 GROUP BY family"
            )
        }
    )
    table_counts = {
        table: _count(conn, table)
        for table in (
            "serving_catalog_records",
            "serving_catalog_family_summary",
            "serving_catalog_time_buckets",
            "serving_catalog_dimension_memberships",
            "serving_catalog_dimension_counts",
        )
    }
    conn.execute(
        "CREATE TABLE serving_catalog_meta ("
        " singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),"
        " catalog_schema_version INTEGER NOT NULL CHECK(catalog_schema_version=1),"
        " prepared_families_json TEXT NOT NULL CHECK(json_valid(prepared_families_json)),"
        " search_index_modes_json TEXT NOT NULL CHECK(json_valid(search_index_modes_json)),"
        " snapshot_built_at_utc TEXT NOT NULL,source_projection_version INTEGER NOT NULL,"
        " derived_counts_json TEXT NOT NULL CHECK(json_valid(derived_counts_json)))"
    )
    conn.execute(
        "INSERT INTO serving_catalog_meta VALUES(1,?,?,?,?,?,?)",
        (
            CATALOG_SCHEMA_VERSION,
            json.dumps(list(PREPARED_FAMILIES), separators=(",", ":")),
            "{}",
            snapshot_built_at_utc,
            int(source_projection_version),
            json.dumps({**table_counts, "serving_catalog_meta": 1}, sort_keys=True, separators=(",", ":")),
        ),
    )
    table_counts["serving_catalog_meta"] = 1
    return CatalogBuild(
        metadata={
            "catalog_schema_version": CATALOG_SCHEMA_VERSION,
            "prepared_families": list(PREPARED_FAMILIES),
            "search_index_modes": {},
            "snapshot_built_at_utc": snapshot_built_at_utc,
            "source_projection_version": int(source_projection_version),
            "family_latest_counts": family_latest_counts,
            "table_counts": table_counts,
        },
        family_latest_counts=family_latest_counts,
    )
