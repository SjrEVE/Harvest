"""RC3-6: a no-op publish must not rebuild the multi-GB serving snapshot.

The guard is deliberately fail-safe: any doubt (missing table, unreadable
snapshot, mismatched counts) falls back to the existing rebuild path.
"""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from santhau_studio.workers.jobs import WorkerLoop


def _digest(conn: sqlite3.Connection) -> str:
    """Mirror of serving._content_digest for the two-table fixture."""
    hasher = hashlib.sha256()
    for table in ("source_records", "settings"):
        projection = ",".join(f"COALESCE(quote(\"{c}\"),'-')"
                              for c in ("family",) if table == "source_records")
        if table == "settings":
            projection = "COALESCE(quote(key),'-'),COALESCE(quote(value_json),'-')"
        for row in conn.execute(
            f"SELECT {projection}, COUNT(*) FROM {table} GROUP BY {projection} ORDER BY 1"):
            hasher.update(("|".join(map(str, row))).encode())
    return hasher.hexdigest()

SCHEMA = """
CREATE TABLE source_records (id INTEGER PRIMARY KEY, family TEXT);
CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT, updated_at_utc TEXT);
"""


class Ok:
    published = True
    reason = None


def _make_pair(root: Path) -> None:
    """A warehouse and a serving snapshot that describe exactly the same rows."""
    counts = {"source_records": 0, "settings": 0}  # RC3-8: no audit tables in the manifest
    for name in ("warehouse.sqlite", "serving.sqlite"):
        conn = sqlite3.connect(root / name)
        conn.executescript(SCHEMA)
        if name == "serving.sqlite":
            conn.executescript("""
                CREATE TABLE record_fields(record_id INTEGER PRIMARY KEY,family TEXT,is_latest INTEGER,sectors_json TEXT,listing_public_date,market_fields_json TEXT NOT NULL DEFAULT '{}');
                CREATE INDEX ix_rf_listing_family_date ON record_fields(family,listing_public_date DESC,record_id DESC);
                CREATE INDEX ix_rf_listing_date ON record_fields(listing_public_date DESC,record_id DESC);
                CREATE TABLE serving_listing_meta(singleton_id INTEGER PRIMARY KEY,listing_schema_version INTEGER,record_count INTEGER,snapshot_built_at_utc TEXT);
                INSERT INTO serving_listing_meta VALUES(1,1,0,'2026-11-06T12:00:00Z');
                CREATE TABLE serving_catalog_records(record_id INTEGER PRIMARY KEY,published_epoch REAL,closing_epoch REAL,
                  signed_epoch REAL,version_known INTEGER,status_display TEXT,winner_names_json TEXT,investor_name TEXT,
                  participants INTEGER,search_text TEXT);
                CREATE TABLE serving_catalog_family_summary(family TEXT PRIMARY KEY,total_records INTEGER,known_dates INTEGER,
                  missing_dates INTEGER,with_winners INTEGER,investor_count INTEGER,positive INTEGER,zero INTEGER,missing INTEGER,
                  bucket_1 INTEGER,bucket_2 INTEGER,bucket_3_5 INTEGER,bucket_6_plus INTEGER) WITHOUT ROWID;
                CREATE TABLE serving_catalog_time_buckets(family TEXT,bucket_kind TEXT,bucket_key TEXT,record_count INTEGER,
                  PRIMARY KEY(family,bucket_kind,bucket_key)) WITHOUT ROWID;
                CREATE TABLE serving_catalog_dimension_memberships(record_id INTEGER,dimension_kind TEXT,dimension_value TEXT,
                  PRIMARY KEY(record_id,dimension_kind,dimension_value)) WITHOUT ROWID;
                CREATE TABLE serving_catalog_dimension_counts(family TEXT,dimension_kind TEXT,dimension_value TEXT,record_count INTEGER,
                  PRIMARY KEY(family,dimension_kind,dimension_value)) WITHOUT ROWID;
                CREATE TABLE serving_catalog_meta(singleton_id INTEGER PRIMARY KEY,catalog_schema_version INTEGER,
                  prepared_families_json TEXT,search_index_modes_json TEXT,snapshot_built_at_utc TEXT,
                  source_projection_version INTEGER,derived_counts_json TEXT);
                INSERT INTO serving_catalog_family_summary VALUES('KHLCNT',0,0,0,0,0,0,0,0,0,0,0,0);
                INSERT INTO serving_catalog_meta VALUES(1,1,'[]','{}','2026-11-06T12:00:00Z',2,
                  '{"serving_catalog_records":0,"serving_catalog_family_summary":1,"serving_catalog_time_buckets":0,
                    "serving_catalog_dimension_memberships":0,"serving_catalog_dimension_counts":0,"serving_catalog_meta":1}');
            """)
            derived_catalog = {
                "catalog_schema_version": 1,
                "prepared_families": [],
                "search_index_modes": {},
                "family_latest_counts": dict.fromkeys(("TBMT", "YCBG", "DU_AN", "KHLCNT", "KQLCNT", "HOP_DONG"), 0),
                "snapshot_built_at_utc": "2026-11-06T12:00:00Z",
                "source_projection_version": 2,
                "table_counts": {
                    "serving_catalog_records": 0,
                    "serving_catalog_family_summary": 1,
                    "serving_catalog_time_buckets": 0,
                    "serving_catalog_dimension_memberships": 0,
                    "serving_catalog_dimension_counts": 0,
                    "serving_catalog_meta": 1,
                },
            }
            conn.execute(
                "CREATE TABLE serving_meta (id INTEGER PRIMARY KEY, snapshot_built_at_utc TEXT,"
                " source_warehouse_sha TEXT, row_counts_json TEXT, stale INTEGER DEFAULT 0,"
                " stale_reason TEXT)"
            )
            conn.execute(
                "INSERT INTO serving_meta (snapshot_built_at_utc, source_warehouse_sha, row_counts_json)"
                " VALUES (?,?,?)",
                ("2026-11-06T12:00:00Z", "sha-not-used-by-the-guard",
                 json.dumps({**counts, "content_digest": _digest(conn), "derived_catalog": derived_catalog,
                             "derived_listing": {"listing_schema_version": 1, "record_count": 0,
                                                 "snapshot_built_at_utc": "2026-11-06T12:00:00Z"}}, sort_keys=True)),
            )
            conn.execute(
                "CREATE TABLE serving_projection_meta ("
                " id INTEGER PRIMARY KEY CHECK (id = 1),"
                " projection_version INTEGER NOT NULL CHECK (projection_version >= 1),"
                " snapshot_built_at_utc TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO serving_projection_meta (id, projection_version, snapshot_built_at_utc)"
                " VALUES (1, 2, '2026-11-06T12:00:00Z')"
            )
        conn.commit()
        conn.close()
    # The UI marker "collection in progress" must be cleared when the guard skips.
    (root / "serving.stale.json").write_text(
        '{"reason":"collection in progress"}', encoding="utf-8"
    )


@pytest.fixture
def loop(tmp_path):
    return WorkerLoop(tmp_path, publish_serving=True)


def test_current_snapshot_is_not_rebuilt(loop, monkeypatch):
    _make_pair(loop.data_root)
    monkeypatch.setattr(
        "santhau_studio.storage.serving.build_serving_db",
        lambda *_a, **_k: pytest.fail("no-op publish must not rebuild serving"),
    )
    loop._publish_snapshot(content_unchanged=True)
    assert loop.snapshot_attempts == 0
    assert not (loop.data_root / "serving.stale.json").exists()


def test_snapshot_with_new_rows_is_rebuilt(loop, monkeypatch):
    _make_pair(loop.data_root)
    conn = sqlite3.connect(loop.data_root / "warehouse.sqlite")
    conn.execute("INSERT INTO source_records (family) VALUES ('TBMT')")
    conn.commit()
    conn.close()
    calls = []
    monkeypatch.setattr(
        "santhau_studio.storage.serving.build_serving_db",
        lambda *_a, **_k: (calls.append(1), Ok())[1],
    )
    loop._publish_snapshot()
    assert len(calls) == 1
    assert loop.snapshot_attempts == 1


def test_without_rowid_table_is_digested_not_guessed(loop, monkeypatch):
    """RC3-8 R3: the digest replaces the MAX(rowid) heuristic, so a WITHOUT ROWID
    settings table is digested exactly like any rowid table — no forced rebuild."""
    _make_pair(loop.data_root)
    for name in ("warehouse.sqlite", "serving.sqlite"):
        conn = sqlite3.connect(loop.data_root / name)
        conn.execute("DROP TABLE settings")
        conn.execute(
            "CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT, "
            "updated_at_utc TEXT) WITHOUT ROWID"
        )
        conn.commit()
        conn.close()
    # Content (rows, digest, settings timestamp) is identical across both sides —
    # the physical rowid representation is irrelevant, so no rebuild happens.
    calls = []
    monkeypatch.setattr(
        "santhau_studio.storage.serving.build_serving_db",
        lambda *_a, **_k: (calls.append(1), Ok())[1],
    )
    loop._publish_snapshot(content_unchanged=True)
    assert len(calls) == 0
    assert loop.snapshot_attempts == 0
    assert not (loop.data_root / "serving.stale.json").exists()
