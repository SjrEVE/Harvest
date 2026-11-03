"""Serving snapshot: build → gate → atomic publish, and the previous snapshot keeps serving (§5.5).

The suite exists because the FTS index had never actually been built anywhere: it was written,
referenced by UI tasks, and no test executed it. The first run found a malformed INSERT SELECT.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from santhau_studio.collectors.extraction import extract_page, verify_extraction
from santhau_studio.collectors.source_registry import binding_for
from santhau_studio.domain.text import fold_for_query
from santhau_studio.storage import serving
from santhau_studio.storage.rawstore import RawObjectStore
from santhau_studio.storage.warehouse import WarehouseWriter, connect


@pytest.fixture()
def env(tmp_path: Path):
    data_root = tmp_path / "data"
    store = RawObjectStore(data_root)
    conn = connect(data_root / "warehouse.sqlite", create_schema=True)
    writer = WarehouseWriter(conn, store)
    writer.register_run("run-1", "job-1", "BACKFILL")
    yield writer, conn, store, data_root
    conn.close()


def commit_page(writer, records: list[dict], *, page_no: int = 0, family: str = "TBMT", attempt_no: int = 1):
    page = {"code": 200, "page": {"content": records}}
    response_ref = writer.store_response(page)
    attempt = writer.ensure_attempt(
        run_id="run-1", family=family, endpoint="smart/search",
        interval_from="2026-10-31T00:00:00Z", interval_to="2026-11-01T00:00:00Z",
        page_no=page_no, attempt_no=attempt_no, outcome="OK", response=response_ref,
    )
    report = extract_page(page, binding_for(family).spec)
    verify_extraction(page, report)
    return writer.commit_page(run_id="run-1", attempt_id=attempt, response_ref=response_ref, records=report.records)


# ------------------------------------------------------------------ happy path

def test_snapshot_publishes_and_row_counts_match(env, tmp_path: Path) -> None:
    writer, conn, _, data_root = env
    commit_page(writer, [{"notifyNo": "SV001", "notifyVersion": "00", "title": "Gói thầu xây lắp số 1"}])
    commit_page(writer, [{"notifyNo": "SV002", "notifyVersion": "00", "title": "Mua sắm thiết bị y tế"}], page_no=1)
    result = serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite")
    assert result.published, result.reason
    assert result.snapshot_rows["source_records"] == 2
    for table, count in result.snapshot_rows.items():
        assert count == conn.execute(f"SELECT COUNT(*) FROM '{table}'").fetchone()[0], table


def test_fts_searches_title_across_diacritics(env, tmp_path: Path) -> None:
    """UI-06: an ASCII query must hit Vietnamese text, including the letters SQLite does not fold."""
    writer, _, _, data_root = env
    commit_page(writer, [{"notifyNo": "SV003", "notifyVersion": "00", "title": "Sửa chữa đường giao thông nông thôn"}])
    serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite")
    conn = sqlite3.connect(tmp_path / "serving.sqlite")
    for query in ("duong giao thong", "đường giao thông", "SỬA CHỮA ĐƯỜNG", "nong thon"):
        hits = conn.execute(
            "SELECT record_id, title FROM search_index WHERE search_index MATCH ?",
            (fold_for_query(query),),
        ).fetchall()
        assert len(hits) == 1, f"{query!r} should match the Vietnamese title"
    folded = conn.execute("SELECT title FROM search_index").fetchone()[0]
    assert folded == "sua chua duong giao thong nong thon"
    # Display text is never folded; the UI reads it from the record table.
    assert conn.execute("SELECT json_extract(normalized_json,'$.title') FROM source_record_versions").fetchone()[0] == (
        "Sửa chữa đường giao thông nông thôn"
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM search_index WHERE search_index MATCH ?", (fold_for_query("khong co tu nay"),)
    ).fetchone()[0] == 0
    conn.close()


def test_query_folding_neutralises_fts_operators(env, tmp_path: Path) -> None:
    """A user typing `AND`, a quote or `NEAR` must get a match error-free, not a reparsed query."""
    writer, _, _, data_root = env
    commit_page(writer, [{"notifyNo": "SV020", "notifyVersion": "00", "title": "Gói thầu A NEAR B \"cẩn trọng\" AND C"}])
    serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite")
    conn = sqlite3.connect(tmp_path / "serving.sqlite")
    for query in ('NEAR', 'AND', '"cẩn trọng"', 'gói thầu a near b'):
        rows = conn.execute("SELECT record_id FROM search_index WHERE search_index MATCH ?", (fold_for_query(query),)).fetchall()
        assert len(rows) == 1, f"{query!r} should be treated as text"
    conn.close()


def test_fts_covers_investor_name_and_natural_key(env, tmp_path: Path) -> None:
    writer, _, _, data_root = env
    commit_page(writer, [{"notifyNo": "SV004", "notifyVersion": "00", "title": "Gói 1", "investorName": "Ban QLDA xây dựng tỉnh A"}])
    serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite")
    conn = sqlite3.connect(tmp_path / "serving.sqlite")
    assert conn.execute("SELECT COUNT(*) FROM search_index WHERE investor_name MATCH 'QLDA'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM search_index WHERE natural_key MATCH 'SV004'").fetchone()[0] == 1
    conn.close()


def test_fts_indexes_a_record_whose_latest_version_has_no_title(env, tmp_path: Path) -> None:
    """A version without `title` is legal JSON and must still be findable by its key."""
    writer, conn, _, data_root = env
    commit_page(writer, [{"notifyNo": "SV005", "notifyVersion": "00", "title": "Có version"}])
    conn.execute("UPDATE source_record_versions SET normalized_json='{\"notifyNo\":\"SV005\"}'")
    serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite")
    snapshot = sqlite3.connect(tmp_path / "serving.sqlite")
    assert snapshot.execute("SELECT COUNT(*) FROM search_index").fetchone()[0] == 1
    assert snapshot.execute("SELECT title FROM search_index").fetchone()[0] == ""
    assert snapshot.execute("SELECT COUNT(*) FROM search_index WHERE search_index MATCH 'SV005'").fetchone()[0] == 1
    snapshot.close()


def test_rebuild_replaces_the_snapshot_and_meta_records_both_builds(env, tmp_path: Path) -> None:
    writer, _, _, data_root = env
    target = tmp_path / "serving.sqlite"
    commit_page(writer, [{"notifyNo": "SV006", "notifyVersion": "00", "title": "một"}])
    assert serving.build_serving_db(data_root / "warehouse.sqlite", target).published
    first = serving.serving_state(target)
    commit_page(writer, [{"notifyNo": "SV007", "notifyVersion": "00", "title": "hai"}], page_no=1)
    assert serving.build_serving_db(data_root / "warehouse.sqlite", target).published
    second = serving.serving_state(target)
    assert second["row_counts"]["source_records"] == 2
    assert first["row_counts"]["source_records"] == 1
    assert second["built_at"] >= first["built_at"]


# ------------------------------------------------------------------ gate behaviour

def test_failed_gate_leaves_the_previous_snapshot_serving(env, tmp_path: Path, monkeypatch) -> None:
    """§5.5: a rejected build must not touch what is already published."""
    writer, conn, _, data_root = env
    target = tmp_path / "serving.sqlite"
    commit_page(writer, [{"notifyNo": "SV008", "notifyVersion": "00", "title": "good"}])
    assert serving.build_serving_db(data_root / "warehouse.sqlite", target).published
    good_counts = serving.serving_state(target)["row_counts"]

    def reject(*_args, **_kwargs):
        raise serving.SnapshotRejected("injected gate failure")

    monkeypatch.setattr(serving, "_build_search_index", reject)
    commit_page(writer, [{"notifyNo": "SV009", "notifyVersion": "00", "title": "bad"}], page_no=1)
    result = serving.build_serving_db(data_root / "warehouse.sqlite", target)
    assert not result.published and result.stale
    assert "injected gate failure" in result.reason
    assert serving.serving_state(target)["row_counts"] == good_counts, "old snapshot still serves"
    assert list(target.parent.glob("*.tmp")) == [], "candidate cleaned up after rejection"
    stale = conn.execute("SELECT value_json FROM settings WHERE key='serving_stale'").fetchone()
    assert stale is not None and "injected gate failure" in stale[0]


def test_row_count_drift_is_rejected(env, tmp_path: Path, monkeypatch) -> None:
    """The copy uses INSERT OR IGNORE, which drops rows *silently*, so counts are the only witness.

    The drift itself is injected: no reachable production path is known that makes the
    candidate lose a row (every UNIQUE in the schema also exists in the source). The guard is
    kept because the failure mode it watches for is silent by construction, and because the
    *rejection protocol* — previous snapshot intact, candidate removed, reason surfaced — has
    to be proven to run.
    """
    writer, _, _, data_root = env
    commit_page(writer, [{"notifyNo": "SV010", "notifyVersion": "00", "title": "one"}])
    real_count = serving._count
    calls = {"source_records": 0}

    def lying_count(conn, table):
        # build_serving_db counts the candidate first, then the source; lie only on the candidate.
        if table == "source_records":
            calls["source_records"] += 1
            if calls["source_records"] == 1:
                return real_count(conn, table) - 1
        return real_count(conn, table)

    monkeypatch.setattr(serving, "_count", lying_count)
    result = serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite")
    assert not result.published
    assert "row count drift" in result.reason
    assert not (tmp_path / "serving.sqlite").exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_build_never_blocks_the_writer(env, tmp_path: Path) -> None:
    """The GUI reads the snapshot so the single writer is never contended — that must be measurably true."""
    writer, conn, _, data_root = env
    commit_page(writer, [{"notifyNo": "SV011", "notifyVersion": "00", "title": "open txn"}])
    conn.execute("BEGIN")
    conn.execute("INSERT INTO settings (key, value_json, updated_at_utc) VALUES ('in-flight','{}','2026-11-03T00:00:00Z')")
    result = serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite")
    assert result.published, result.reason
    assert result.snapshot_rows["source_records"] == 1
    conn.execute("COMMIT")
    assert serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite").published
    snapshot = sqlite3.connect(tmp_path / "serving.sqlite")
    assert snapshot.execute("SELECT COUNT(*) FROM settings WHERE key='in-flight'").fetchone()[0] == 1
    snapshot.close()


def test_serving_state_reports_absent_file(tmp_path: Path) -> None:
    assert serving.serving_state(tmp_path / "none.sqlite") == {"present": False}


def test_empty_warehouse_publishes_a_valid_snapshot(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    conn = connect(data_root / "warehouse.sqlite", create_schema=True)
    conn.close()
    result = serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite")
    assert result.published, result.reason
    snapshot = sqlite3.connect(tmp_path / "serving.sqlite")
    assert snapshot.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert snapshot.execute("SELECT COUNT(*) FROM search_index").fetchone()[0] == 0
    snapshot.close()
