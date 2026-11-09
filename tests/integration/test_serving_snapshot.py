"""Serving snapshot: build → gate → atomic publish, and the previous snapshot keeps serving (§5.5).

The suite exists because the FTS index had never actually been built anywhere: it was written,
referenced by UI tasks, and no test executed it. The first run found a malformed INSERT SELECT.
"""

from __future__ import annotations

import json
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
    commit_page(writer, [{"notifyNo": "SV001", "notifyVersion": "00", "bidName": "Gói thầu xây lắp số 1"}])
    commit_page(writer, [{"notifyNo": "SV002", "notifyVersion": "00", "bidName": "Mua sắm thiết bị y tế"}], page_no=1)
    result = serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite")
    assert result.published, result.reason
    assert result.snapshot_rows["source_records"] == 2
    for table, count in result.snapshot_rows.items():
        assert count == conn.execute(f"SELECT COUNT(*) FROM '{table}'").fetchone()[0], table


def test_fts_searches_title_across_diacritics(env, tmp_path: Path) -> None:
    """UI-06: an ASCII query must hit Vietnamese text, including the letters SQLite does not fold."""
    writer, _, _, data_root = env
    commit_page(writer, [{"notifyNo": "SV003", "notifyVersion": "00", "bidName": "Sửa chữa đường giao thông nông thôn"}])
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
    commit_page(writer, [{"notifyNo": "SV020", "notifyVersion": "00", "bidName": "Gói thầu A NEAR B \"cẩn trọng\" AND C"}])
    serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite")
    conn = sqlite3.connect(tmp_path / "serving.sqlite")
    for query in ('NEAR', 'AND', '"cẩn trọng"', 'gói thầu a near b'):
        rows = conn.execute("SELECT record_id FROM search_index WHERE search_index MATCH ?", (fold_for_query(query),)).fetchall()
        assert len(rows) == 1, f"{query!r} should be treated as text"
    conn.close()


def test_fts_covers_investor_name_and_natural_key(env, tmp_path: Path) -> None:
    writer, _, _, data_root = env
    commit_page(writer, [{"notifyNo": "SV004", "notifyVersion": "00", "bidName": "Gói 1", "investorName": "Ban QLDA xây dựng tỉnh A"}])
    serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite")
    conn = sqlite3.connect(tmp_path / "serving.sqlite")
    assert conn.execute("SELECT COUNT(*) FROM search_index WHERE investor_name MATCH 'QLDA'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM search_index WHERE natural_key MATCH 'SV004'").fetchone()[0] == 1
    conn.close()


def test_fts_indexes_a_record_whose_latest_version_has_no_title(env, tmp_path: Path) -> None:
    """A version without `title` is legal JSON and must still be findable by its key."""
    writer, conn, _, data_root = env
    commit_page(writer, [{"notifyNo": "SV005", "notifyVersion": "00", "bidName": "Có version"}])
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
    commit_page(writer, [{"notifyNo": "SV006", "notifyVersion": "00", "bidName": "một"}])
    assert serving.build_serving_db(data_root / "warehouse.sqlite", target).published
    first = serving.serving_state(target)
    commit_page(writer, [{"notifyNo": "SV007", "notifyVersion": "00", "bidName": "hai"}], page_no=1)
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
    commit_page(writer, [{"notifyNo": "SV008", "notifyVersion": "00", "bidName": "good"}])
    assert serving.build_serving_db(data_root / "warehouse.sqlite", target).published
    good_counts = serving.serving_state(target)["row_counts"]

    def reject(*_args, **_kwargs):
        raise serving.SnapshotRejected("injected gate failure")

    monkeypatch.setattr(serving, "_build_search_index", reject)
    commit_page(writer, [{"notifyNo": "SV009", "notifyVersion": "00", "bidName": "bad"}], page_no=1)
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
    commit_page(writer, [{"notifyNo": "SV010", "notifyVersion": "00", "bidName": "one"}])
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
    commit_page(writer, [{"notifyNo": "SV011", "notifyVersion": "00", "bidName": "open txn"}])
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


def test_crash_mid_build_leaves_the_previous_snapshot_and_no_temp(env, tmp_path: Path, monkeypatch) -> None:
    """A publish that dies while building must not touch the serving file or leave debris."""
    writer, _conn, _store, data_root = env
    commit_page(writer, [{"notifyNo": "IB-1", "notifyVersion": "00", "bidName": "Gói 1"}])
    writer.conn.commit()
    target = tmp_path / "serving.sqlite"
    assert serving.build_serving_db(data_root / "warehouse.sqlite", target).published
    good_bytes = target.read_bytes()

    def explode(_dest) -> None:
        raise sqlite3.Error("simulated crash while materialising stats")

    monkeypatch.setattr(serving, "_materialise_family_stats", explode)
    result = serving.build_serving_db(data_root / "warehouse.sqlite", target)
    assert result.published is False
    assert target.read_bytes() == good_bytes, "old snapshot must keep serving"
    assert not target.with_suffix(target.suffix + ".tmp").exists(), "no half-built temp file"
def test_publish_retries_transient_windows_reader_lock(tmp_path, monkeypatch):
    from pathlib import Path

    from santhau_studio.collectors.offline_acceptance import initialize
    from santhau_studio.storage.serving import build_serving_db
    initialize(tmp_path)
    original = Path.replace
    attempts = []
    def temporarily_locked(path, target):
        if path.name == "serving.sqlite.tmp":
            attempts.append(1)
            if len(attempts) < 3:
                raise PermissionError("Reader briefly holds the Windows snapshot")
        return original(path, target)
    monkeypatch.setattr(Path, "replace", temporarily_locked)
    result = build_serving_db(tmp_path / "warehouse.sqlite", tmp_path / "serving.sqlite")
    assert result.published, result.reason
    assert len(attempts) == 3


def test_snapshot_publishes_projection_v2_contract_without_losing_raw_arrays(env, tmp_path: Path) -> None:
    writer, _, _, data_root = env
    commit_page(writer, [{
        "notifyNo": "IB2600001", "notifyVersion": "00", "bidName": "Gói đang mở",
        "status": "OPEN_BID", "bidCloseDate": "2026-11-12T09:00:00",
    }])
    commit_page(writer, [{
        "notifyNo": "IB2600002", "notifyVersion": "00", "inputResultId": "result-2",
        "bidName": "Kết quả nhiều lô", "winningCode": ["vn0123456789", "vn9876543210"],
        "bidWinningPrice": [132000000, 220000000], "bidPrice": [-1],
        "publicDateKqlcnt": "2026-11-01T00:00:00", "statusForNotify": "CNTTT",
    }], family="KQLCNT", page_no=1)

    result = serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite")
    assert result.published, result.reason
    snapshot = sqlite3.connect(tmp_path / "serving.sqlite")
    try:
        projection = snapshot.execute(
            "SELECT projection_version, snapshot_built_at_utc FROM serving_projection_meta WHERE id=1"
        ).fetchone()
        stamp = snapshot.execute("SELECT snapshot_built_at_utc FROM serving_meta ORDER BY id DESC LIMIT 1").fetchone()[0]
        assert projection == (2, stamp)
        columns = {row[1] for row in snapshot.execute("PRAGMA table_info(record_fields)")}
        assert {"record_id", "source_key", "family", "natural_key", "bid_price", "award_price", "currency"} <= columns
        assert {"status_raw", "status_normalized", "winning_codes_json", "award_prices_json",
                "gia_trung", "price_status", "price_provenance_json"} <= columns
        raw = snapshot.execute(
            "SELECT v.normalized_json FROM source_record_versions v JOIN source_records r ON r.id=v.source_record_id"
            " AND v.content_version_seq=r.current_content_version WHERE r.family='KQLCNT'"
        ).fetchone()[0]
        fields = json.loads(raw)
        assert fields["bidWinningPrice"] == [132000000, 220000000]
        assert fields["winningCode"] == ["vn0123456789", "vn9876543210"]
        assert fields["bidPrice"] == [-1]
        assert serving.serving_state(tmp_path / "serving.sqlite")["projection_version"] == 2
    finally:
        snapshot.close()


def test_v1_snapshot_without_projection_meta_remains_readable(tmp_path: Path) -> None:
    path = tmp_path / "v1.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE serving_meta (id INTEGER PRIMARY KEY, snapshot_built_at_utc TEXT NOT NULL,"
        " source_warehouse_sha TEXT NOT NULL, row_counts_json TEXT NOT NULL,"
        " stale INTEGER NOT NULL DEFAULT 0, stale_reason TEXT)"
    )
    conn.execute(
        "INSERT INTO serving_meta VALUES (1,'2026-11-04T00:58:22Z','warehouse-sha',"
        "'{\"source_records\":0,\"content_digest\":\"digest\"}',0,NULL)"
    )
    conn.commit()
    conn.close()

    state = serving.serving_state(path)
    assert state["present"] is True
    assert state["built_at"] == "2026-11-04T00:58:22Z"
    assert state["projection_version"] == 1
    assert state["stale"] is False


def test_projection_version_change_forces_snapshot_rebuild(env, tmp_path: Path) -> None:
    writer, _, _, data_root = env
    commit_page(writer, [{"notifyNo": "IB2600003", "notifyVersion": "00", "bidName": "Gói v2"}])
    warehouse = data_root / "warehouse.sqlite"
    target = tmp_path / "serving.sqlite"
    assert serving.build_serving_db(warehouse, target).published
    assert serving.snapshot_is_current(warehouse, target)

    conn = sqlite3.connect(target)
    conn.execute("DROP TABLE serving_projection_meta")
    conn.commit()
    conn.close()

    assert not serving.snapshot_is_current(warehouse, target)


def test_v2_projection_sums_award_array_and_keeps_winner_ids(env, tmp_path: Path) -> None:
    writer, _, _, data_root = env
    commit_page(writer, [{
        "notifyNo": "IB2600010", "notifyVersion": "00", "bidName": "Kết quả hai lô",
        "winningCode": ["vn0123456789", "vn9876543210"],
        "bidWinningPrice": [132000000, 220000000], "statusForNotify": "CNTTT",
        "publicDateKqlcnt": "2026-11-01T00:00:00",
    }], family="KQLCNT")
    assert serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite").published
    snapshot = sqlite3.connect(tmp_path / "serving.sqlite")
    try:
        row = snapshot.execute(
            "SELECT award_prices_json,gia_trung,price_status,winning_codes_json,status_normalized,price_provenance_json"
            " FROM record_fields WHERE family='KQLCNT'"
        ).fetchone()
        assert json.loads(row[0]) == [132000000, 220000000]
        assert row[1] == 352000000
        assert row[2] == "SUMMED_ARRAY"
        assert json.loads(row[3]) == ["vn0123456789", "vn9876543210"]
        assert row[4] == "AWARDED"
        provenance = json.loads(row[5])
        assert provenance["source_field"] == "bidWinningPrice"
        assert provenance["raw_hash"]
    finally:
        snapshot.close()


def test_v2_projection_marks_invalid_amount_and_unmapped_status(env, tmp_path: Path) -> None:
    writer, _, _, data_root = env
    commit_page(writer, [{
        "notifyNo": "IB2600011", "notifyVersion": "00", "bidName": "Giá âm",
        "bidWinningPrice": [-1], "publicDateKqlcnt": "2026-11-01T00:00:00",
    }], family="KQLCNT")
    commit_page(writer, [{
        "notifyNo": "IB2600012", "notifyVersion": "00", "bidName": "Trạng thái lạ",
        "status": "UNMAPPED_SOURCE_CODE", "publicDate": "2026-11-01T00:00:00",
    }], page_no=1)
    assert serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite").published
    snapshot = sqlite3.connect(tmp_path / "serving.sqlite")
    try:
        invalid = snapshot.execute(
            "SELECT award_prices_json,gia_trung,price_status FROM record_fields WHERE natural_key='IB2600011'"
        ).fetchone()
        assert json.loads(invalid[0]) == [-1]
        assert invalid[1] is None
        assert invalid[2] == "INVALID"
        unknown = snapshot.execute(
            "SELECT status_raw,status_normalized FROM record_fields WHERE natural_key='IB2600012'"
        ).fetchone()
        assert unknown == ("UNMAPPED_SOURCE_CODE", "UNKNOWN")
    finally:
        snapshot.close()


def test_v2_relationship_edges_use_code_keys_and_hashes(env, tmp_path: Path) -> None:
    from santhau_studio.domain.identity import canonical_bytes

    writer, conn, _, data_root = env
    commit_page(writer, [{"planNo": "PL2600001", "planVersion": "00", "name": "Kế hoạch gốc"}], family="KHLCNT")
    commit_page(writer, [{
        "notifyNo": "IB2600020", "notifyVersion": "00", "bidName": "Thông báo gốc",
        "planNo": "PL2600001", "status": "OPEN_BID", "publicDate": "2026-11-01T00:00:00",
    }], page_no=1)
    commit_page(writer, [{
        "notifyNo": "IB2600020", "notifyVersion": "00", "bidName": "Kết quả gốc",
        "planNo": "PL2600001", "winningCode": ["vn0123456789", "vn9876543210"],
        "bidWinningPrice": [132000000, 220000000], "publicDateKqlcnt": "2026-11-02T00:00:00",
    }], family="KQLCNT", page_no=2)
    commit_page(writer, [{
        "contractCode": "CT2600001", "contractVersion": "00", "ctName": "Hợp đồng gốc",
        "notifyNo": "IB2600020", "notifyVersion": "00", "planNo": "PL2600001",
        "winningCode": ["vn0123456789", "vn9876543210"], "ctPriceAfter": 352000000,
    }], family="HOP_DONG", page_no=3)
    result_id = conn.execute("SELECT id FROM source_records WHERE family='KQLCNT'").fetchone()[0]
    member_raw = {"contractorCode": "vn0000000001", "contractorName": "Thành viên liên danh"}
    ref = writer.store.put(canonical_bytes(member_raw))
    writer.register_object(ref)
    conn.execute(
        "INSERT INTO contractor_memberships(source_record_id,party_object_sha,party_name,party_tax_id_presence,"
        "party_tax_id,role,membership_share_presence,membership_share,ranking,provenance_json)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (result_id, ref.sha256, "Thành viên liên danh", "KNOWN", "vn0000000001", "WINNER",
         "MISSING", None, None, json.dumps({"contractorCode": "vn0000000001"})),
    )
    conn.commit()
    assert serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite").published
    snapshot = sqlite3.connect(tmp_path / "serving.sqlite")
    try:
        ids = {family: snapshot.execute("SELECT record_id FROM record_fields WHERE family=?", (family,)).fetchone()[0]
               for family in ("KHLCNT", "TBMT", "KQLCNT", "HOP_DONG")}
        hashes = {row[0]: (row[1], row[2]) for row in snapshot.execute(
            "SELECT id,current_content_hash,current_record_object_sha FROM source_records"
        )}
        edges = snapshot.execute(
            "SELECT from_record_id,to_record_id,source_record_id,relation,basis,join_key,party_tax_id,content_hash,raw_hash"
            " FROM market_relation_edges"
        ).fetchall()
        plan_edge = next(e for e in edges if e[3] == "PLAN_HAS_NOTICE")
        assert plan_edge[:6] == (ids["KHLCNT"], ids["TBMT"], ids["TBMT"], "PLAN_HAS_NOTICE", "same_planNo", "PL2600001")
        assert plan_edge[7:] == hashes[ids["TBMT"]]
        notice_edge = next(e for e in edges if e[3] == "NOTICE_HAS_RESULT")
        assert notice_edge[:6] == (ids["TBMT"], ids["KQLCNT"], ids["KQLCNT"], "NOTICE_HAS_RESULT", "same_notifyNo_version", "IB2600020:00")
        assert notice_edge[7:] == hashes[ids["KQLCNT"]]
        plan_contract = next(e for e in edges if e[3] == "PLAN_HAS_CONTRACT")
        assert plan_contract[:6] == (ids["KHLCNT"], ids["HOP_DONG"], ids["HOP_DONG"], "PLAN_HAS_CONTRACT", "same_planNo", "PL2600001")
        notice_contract = next(e for e in edges if e[3] == "NOTICE_HAS_CONTRACT")
        assert notice_contract[:6] == (ids["TBMT"], ids["HOP_DONG"], ids["HOP_DONG"], "NOTICE_HAS_CONTRACT", "same_notifyNo_version", "IB2600020:00")
        result_contract = next(e for e in edges if e[3] == "RESULT_HAS_CONTRACT")
        assert result_contract[:6] == (ids["KQLCNT"], ids["HOP_DONG"], ids["HOP_DONG"], "RESULT_HAS_CONTRACT", "same_notifyNo_version", "IB2600020:00")
        winner_codes = {e[6] for e in edges if e[3] == "EXECUTED_BY" and e[0] == ids["KQLCNT"] and e[4] == "winningCode"}
        assert winner_codes == {"vn0123456789", "vn9876543210"}
        membership = next(e for e in edges if e[4] == "deep_contractorCode")
        assert membership[6] == "vn0000000001"
        assert membership[7:] == hashes[ids["KQLCNT"]]
        contract_winners = [e for e in edges if e[0] == ids["HOP_DONG"] and e[3] == "EXECUTED_BY"]
        assert len(contract_winners) == 2
    finally:
        snapshot.close()


def test_v2_enrichment_columns_project_raw_only_fields(env, tmp_path: Path) -> None:
    """Catalog enrichment (sectors/participants/deadline/components) must be projected at
    snapshot time from the raw payload, because the display keep-lists strip investField,
    numBidderJoin (non-KQLCNT), bidCloseDate (YCBG) and the KHLCNT bidName component array."""
    writer, _, _, data_root = env
    commit_page(writer, [{
        "notifyNo": "IB2600030", "notifyVersion": "00", "bidName": "Mua sắm",
        "investField": ["HH", "XL"], "bidCloseDate": "2026-12-01T09:00:00",
        "publicDate": "2026-11-01T00:00:00", "status": "OPEN_BID",
    }], page_no=1)
    commit_page(writer, [{
        "notifyNo": "IB2600031", "notifyVersion": "00", "bidName": "Kết quả",
        "numBidderJoin": 3, "numBidderTech": 2, "investField": ["TV"],
        "bidWinningPrice": [100], "publicDateKqlcnt": "2026-11-02T00:00:00",
    }], family="KQLCNT", page_no=2)
    commit_page(writer, [{
        "notifyNo": "YCBG2600001", "notifyVersion": "00", "bidName": "Báo giá",
        "quoteDeadline": "2026-12-05T17:00:00", "publicDate": "2026-11-01T00:00:00",
    }], family="YCBG", page_no=3)
    commit_page(writer, [{
        "planNo": "PL2600002", "planVersion": "00", "name": "Kế hoạch",
        "bidName": ["Gói thầu A", "Gói thầu B"],
    }], family="KHLCNT", page_no=4)
    assert serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite").published
    snapshot = sqlite3.connect(tmp_path / "serving.sqlite")
    snapshot.row_factory = sqlite3.Row
    try:
        tbmt = snapshot.execute("SELECT * FROM record_fields WHERE family='TBMT'").fetchone()
        assert json.loads(tbmt["sectors_json"]) == ["HH", "XL"]
        assert tbmt["close_date"] == "2026-12-01T09:00:00"
        kqlcnt = snapshot.execute("SELECT * FROM record_fields WHERE family='KQLCNT'").fetchone()
        assert kqlcnt["participants"] == 3 and kqlcnt["qualified"] == 2
        assert json.loads(kqlcnt["sectors_json"]) == ["TV"]
        ycbg = snapshot.execute("SELECT * FROM record_fields WHERE family='YCBG'").fetchone()
        assert ycbg["close_date"] == "2026-12-05T17:00:00"
        khlc = snapshot.execute("SELECT * FROM record_fields WHERE family='KHLCNT'").fetchone()
        assert json.loads(khlc["components_json"]) == ["Gói thầu A", "Gói thầu B"]
        # numBidderJoin absent/invalid stays NULL, never fabricated as zero.
        assert tbmt["participants"] is None
    finally:
        snapshot.close()


def test_v2_catalog_columns_project_display_epochs_and_latest(env, tmp_path: Path) -> None:
    """The catalog aggregate/page path must be raw-free: display values, VN epochs and the
    ranked latest flag are projected at snapshot time so the bridge can filter/aggregate in
    indexed SQL without re-reading 300k+ raw objects on the first request."""
    from datetime import datetime, timedelta, timezone

    vn = timezone(timedelta(hours=7))

    def epoch(value):
        return datetime.fromisoformat(value).replace(tzinfo=vn).timestamp()

    writer, _, _, data_root = env
    commit_page(writer, [{
        "notifyNo": "IB2600040", "notifyVersion": "00", "bidName": "Mua sắm thiết bị",
        "investorName": "Công ty ABC", "provinceName": "Thành phố Hà Nội",
        "publicDate": "2026-11-01T00:00:00", "bidCloseDate": "2026-12-01T09:00:00",
        "status": "01",
    }], page_no=1)
    commit_page(writer, [{
        "contractCode": "CT2600041", "contractVersion": "00", "ctName": "Hợp đồng xây lắp",
        "notifyNo": "IB2600041", "investorName": "Công ty ABC", "contractNo": "HD-001",
        "winningContractorName": ["Nhà thầu X"], "contractSignDate": "2027-01-15",
        "provinceName": "Tỉnh Nghệ An",
    }], family="HOP_DONG", page_no=2)
    assert serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite").published
    snapshot = sqlite3.connect(tmp_path / "serving.sqlite")
    snapshot.row_factory = sqlite3.Row
    try:
        tbmt = snapshot.execute("SELECT * FROM record_fields WHERE family='TBMT'").fetchone()
        assert tbmt["is_latest"] == 1
        assert tbmt["published_epoch"] == epoch("2026-11-01T00:00:00")
        assert tbmt["closing_epoch"] == epoch("2026-12-01T09:00:00")
        assert tbmt["investor_name"] == "Công ty ABC"
        assert json.loads(tbmt["provinces_json"]) == ["Hà Nội"]
        contract = snapshot.execute("SELECT * FROM record_fields WHERE family='HOP_DONG'").fetchone()
        assert contract["signed_epoch"] == epoch("2027-01-15")
        assert contract["winner_names_json"] == json.dumps(["Nhà thầu X"], ensure_ascii=False)
        assert json.loads(contract["provinces_json"]) == ["Nghệ An"]
        assert "HD-001" in contract["search_text"]
        assert "Nhà thầu X" in contract["search_text"]
        assert "Hợp đồng xây lắp" in contract["search_text"]
    finally:
        snapshot.close()


def test_v2_status_display_mirrors_raw_status_not_normalized_source(env, tmp_path: Path) -> None:
    """The catalog active() predicate reads the raw display `status`, which for KQLCNT differs
    from status_raw (statusForNotify wins there). Project it verbatim so the v2 SQL aggregate
    reproduces v1's active count even for a result that still carries an open notice status."""
    writer, _, _, data_root = env
    commit_page(writer, [{
        "notifyNo": "IB2600050", "notifyVersion": "00", "bidName": "Kết quả bất thường",
        "publicDateKqlcnt": "2026-11-02T08:00:00", "statusForNotify": "CNTTT", "status": "01",
        "bidCloseDate": "2026-11-25T09:00:00", "numBidderJoin": 2,
    }], family="KQLCNT", page_no=1)
    assert serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite").published
    snapshot = sqlite3.connect(tmp_path / "serving.sqlite")
    snapshot.row_factory = sqlite3.Row
    try:
        row = snapshot.execute("SELECT * FROM record_fields WHERE family='KQLCNT'").fetchone()
        assert row["status_display"] == "01"
        assert row["status_raw"] == "CNTTT"
        assert row["status_normalized"] == "AWARDED"
    finally:
        snapshot.close()


def test_v2_catalog_epochs_mirror_str_only_display_dates(env, tmp_path: Path) -> None:
    """closing_epoch/signed_epoch feed the v2 catalog active()/date filters and must mirror v1
    project()'s str-only text() of bidCloseDate/contractSignDate. text() ignores a 1-element-list
    date (v1 treats it as missing), so the epoch stays NULL rather than being unwrapped by
    _first_scalar; otherwise v2 counts a row active/dated that v1 excludes. Production E-GP dates
    are text today, but parity must hold by construction for any array-valued source date."""
    writer, _, _, data_root = env
    commit_page(writer, [{
        "notifyNo": "YCBG2600002", "notifyVersion": "00", "bidName": "Báo giá mảng",
        "bidCloseDate": ["2026-12-05T17:00:00"], "publicDate": "2026-11-01T00:00:00", "status": "01",
    }], family="YCBG", page_no=1)
    commit_page(writer, [{
        "contractCode": "CT2600042", "contractVersion": "00", "ctName": "Hợp đồng mảng",
        "notifyNo": "IB2600042", "investorName": "Công ty ABC", "contractNo": "HD-002",
        "winningContractorName": ["Nhà thầu Y"], "contractSignDate": ["2027-02-01"],
        "provinceName": "Tỉnh Nghệ An",
    }], family="HOP_DONG", page_no=2)
    assert serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite").published
    snapshot = sqlite3.connect(tmp_path / "serving.sqlite")
    snapshot.row_factory = sqlite3.Row
    try:
        ycbg = snapshot.execute("SELECT * FROM record_fields WHERE family='YCBG'").fetchone()
        assert ycbg["closing_epoch"] is None
        contract = snapshot.execute("SELECT * FROM record_fields WHERE family='HOP_DONG'").fetchone()
        assert contract["signed_epoch"] is None
    finally:
        snapshot.close()


def _seed_measured_coverage(writer, conn):
    """Two measured TBMT windows: day0 COMPLETE (verified proof), day1 PARTIAL (no proof).

    Two records publish on VN day0, one on day1, so the held-record count per window is 2 and 1.
    Mirrors production: every PARTIAL window lacks a proof, so volume cannot come from the proof.
    """
    import hashlib
    from datetime import UTC, datetime, timedelta

    from santhau_studio.domain.coverage import Interval
    from santhau_studio.storage import coverage as coverage_store

    rule_sha = hashlib.sha256(b"adapter").hexdigest()
    conn.execute(
        "INSERT INTO filter_boundary_rules (rule_key, rule_version, source_key, family, endpoint, adapter_sha,"
        " boundary_kind, precision, source_timezone, offset_minutes, conversion_params, canary_verified,"
        " verified_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("egp-tbmt-day", 1, "egp_home", "TBMT", "/o/egp-portal-home/services/smart/search", rule_sha,
         "CLOSED_LOCAL", "DAY", "Asia/Ho_Chi_Minh", 420, json.dumps({"to_minus": "1ms"}), 1, "2026-10-31T00:00:00Z"),
    )
    conn.commit()
    rule_id = int(conn.execute("SELECT id FROM filter_boundary_rules WHERE rule_key='egp-tbmt-day'").fetchone()[0])
    day0 = Interval(datetime(2026, 10, 31, 17, 0, tzinfo=UTC), datetime(2026, 11, 1, 17, 0, tzinfo=UTC))
    day1 = Interval(day0.to_utc, day0.to_utc + timedelta(days=1))
    commit_page(writer, [
        {"notifyNo": "COV001", "notifyVersion": "00", "bidName": "Gói ngày 1a", "publicDate": "2026-11-01T00:00:00"},
        {"notifyNo": "COV002", "notifyVersion": "00", "bidName": "Gói ngày 1b", "publicDate": "2026-11-01T08:00:00"},
    ], page_no=1)
    commit_page(writer, [
        {"notifyNo": "COV003", "notifyVersion": "00", "bidName": "Gói ngày 2", "publicDate": "2026-11-02T08:00:00"},
    ], page_no=2)

    def proof(interval):
        return coverage_store.ProofRecord(
            proof_version=1, source_key="egp_home", family="TBMT", interval=interval, granularity="DAY",
            boundary_rule_id=rule_id, source_query_fingerprint=hashlib.sha256(b"q").hexdigest(),
            adapter_sha=rule_sha, request_count=2, attempt_count=2, attempt_error_count=0,
            unrecovered_request_count=0, malformed_count=0, distinct_ids=2,
            ids_union_hash=hashlib.sha256(b"ids").hexdigest(), total_first=2, total_last=2, total_trusted=1,
            terminal_evidence={"kind": "SHORT_PAGE", "last_page_rows": 2}, page_size=10,
            max_pages_hit=0, max_records_hit=0, verified_at_utc="2026-11-02T00:00:00Z",
        )

    coverage_store.record_interval(
        conn, run_id="run-1", status="COMPLETE", proof=proof(day0),
        source_key="egp_home", family="TBMT", interval=day0, granularity="DAY",
    )
    coverage_store.record_interval(
        conn, run_id="run-1", status="PARTIAL", proof=None,
        source_key="egp_home", family="TBMT", interval=day1, granularity="DAY",
    )
    return day0, day1


def test_v2_coverage_windows_materialised_offline(env, tmp_path: Path) -> None:
    """Measured coverage windows are resolved OFFLINE through the §4.5 resolver and shipped as a
    prepared serving table, so the bridge reads PARTIAL/COMPLETE windows without importing the
    producer or re-resolving per request. `records` is held latest volume dated in the window
    (published_epoch), never interpolated; the gap around the measured windows is not invented."""
    writer, conn, _, data_root = env
    day0, day1 = _seed_measured_coverage(writer, conn)
    assert serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite").published
    snapshot = sqlite3.connect(tmp_path / "serving.sqlite")
    snapshot.row_factory = sqlite3.Row
    try:
        windows = snapshot.execute("SELECT * FROM serving_coverage_windows ORDER BY interval_from_utc").fetchall()
        assert len(windows) == 2, "only measured windows; gaps between them must not be emitted"
        assert windows[0]["interval_from_utc"] == day0.from_utc.isoformat().replace("+00:00", "Z")
        assert windows[0]["status"] == "COMPLETE" and windows[0]["records"] == 2
        assert windows[1]["status"] == "PARTIAL" and windows[1]["records"] == 1
        state = snapshot.execute("SELECT * FROM serving_coverage_state WHERE family='TBMT'").fetchone()
        assert state["coverage_state"] == "PARTIAL", "day1 unproven → the family is not complete"
        assert state["measured_from_utc"] == day0.from_utc.isoformat().replace("+00:00", "Z")
        assert state["measured_to_utc"] == day1.to_utc.isoformat().replace("+00:00", "Z")
    finally:
        snapshot.close()


def test_v2_coverage_window_stale_when_proof_drifts(env, tmp_path: Path) -> None:
    """Status comes from the resolver, not raw coverage_intervals.status: an unacked HASH_DRIFT
    over a proven COMPLETE window makes it NEEDS_RECONCILE → STALE, so Radar cannot present data
    that moved under its proof as current-and-complete (§5.5)."""
    writer, conn, _, data_root = env
    day0, _day1 = _seed_measured_coverage(writer, conn)
    conn.execute(
        "INSERT INTO reconcile_gaps (run_id, source_key, family, interval_from_utc, interval_to_utc, kind,"
        " evidence_json, detected_at_utc) VALUES (?,?,?,?,?,?,?,?)",
        ("run-2", "egp_home", "TBMT", day0.from_utc.isoformat(), day0.to_utc.isoformat(), "HASH_DRIFT",
         json.dumps({"identity_key": "COV001"}), "2026-11-03T00:00:00Z"),
    )
    conn.commit()
    assert serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite").published
    snapshot = sqlite3.connect(tmp_path / "serving.sqlite")
    snapshot.row_factory = sqlite3.Row
    try:
        windows = snapshot.execute("SELECT * FROM serving_coverage_windows ORDER BY interval_from_utc").fetchall()
        assert windows[0]["status"] == "STALE", "raw status is COMPLETE but the proof drifted"
        state = snapshot.execute("SELECT * FROM serving_coverage_state WHERE family='TBMT'").fetchone()
        assert state["coverage_state"] == "STALE"
    finally:
        snapshot.close()

