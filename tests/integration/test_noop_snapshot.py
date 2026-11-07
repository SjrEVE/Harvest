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
            conn.execute(
                "CREATE TABLE serving_meta (id INTEGER PRIMARY KEY, snapshot_built_at_utc TEXT,"
                " source_warehouse_sha TEXT, row_counts_json TEXT, stale INTEGER DEFAULT 0,"
                " stale_reason TEXT)"
            )
            conn.execute(
                "INSERT INTO serving_meta (snapshot_built_at_utc, source_warehouse_sha, row_counts_json)"
                " VALUES (?,?,?)",
                ("2026-11-06T12:00:00Z", "sha-not-used-by-the-guard",
                 json.dumps({**counts, "content_digest": _digest(conn)}, sort_keys=True)),
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
