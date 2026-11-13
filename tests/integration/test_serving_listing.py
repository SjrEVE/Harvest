from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from test_serving_snapshot import commit_page

from santhau_studio.storage import read_model, serving
from santhau_studio.storage.listing_read_model import materialise_listing_search

pytest_plugins = ("test_serving_snapshot",)


def test_snapshot_listing_keeps_all_versions_and_indexes_the_legacy_public_date(env, tmp_path: Path):
    writer, _, _, data_root = env
    commit_page(writer, [{"notifyNo": "SYN-LIST-1", "notifyVersion": "00", "bidName": "Synthetic old", "publicDate": "2026-11-01T09:00:00"}])
    commit_page(writer, [{"notifyNo": "SYN-LIST-1", "notifyVersion": "01", "bidName": "Synthetic new", "publicDate": "2026-11-02T09:00:00"}], page_no=1)
    commit_page(writer, [{"notifyNo": "SYN-AWARD-1", "notifyVersion": "00", "bidName": "Synthetic award", "publicDateKqlcnt": "2026-11-03T09:00:00", "decisionDate": "2030-01-01"}], family="KQLCNT")
    target = tmp_path / "serving.sqlite"
    result = serving.build_serving_db(data_root / "warehouse.sqlite", target)
    assert result.published, result.reason
    with closing(sqlite3.connect(target)) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(record_fields)")}
        assert "listing_public_date" in columns
        meta = db.execute("SELECT listing_schema_version,record_count,snapshot_built_at_utc FROM serving_listing_meta WHERE singleton_id=1").fetchone()
        count = db.execute("SELECT COUNT(*) FROM record_fields").fetchone()[0]
        built_at = db.execute("SELECT snapshot_built_at_utc FROM serving_meta").fetchone()[0]
        assert meta == (1, count, built_at)
        assert count == 3
        assert db.execute("SELECT COUNT(*) FROM record_fields WHERE family='TBMT'").fetchone()[0] == 2
        mismatches = db.execute("""SELECT COUNT(*) FROM record_fields f
            JOIN source_records r ON r.id=f.record_id
            JOIN source_record_versions v ON v.source_record_id=r.id AND v.content_version_seq=r.current_content_version
            WHERE f.listing_public_date IS NOT COALESCE(CASE WHEN f.family='KQLCNT'
            THEN COALESCE(json_extract(v.normalized_json,'$.publicDateKqlcnt'),json_extract(v.normalized_json,'$.published_at'))
            ELSE COALESCE(json_extract(v.normalized_json,'$.publicDate'),json_extract(v.normalized_json,'$.published_at')) END,f.collection_date)""").fetchone()[0]
        assert mismatches == 0
        plan = db.execute("EXPLAIN QUERY PLAN SELECT record_id FROM record_fields WHERE family='KQLCNT' ORDER BY listing_public_date DESC,record_id DESC LIMIT 1").fetchall()
        assert any("COVERING INDEX ix_rf_listing_family_date" in row[3] for row in plan)
        assert not any("TEMP B-TREE" in row[3] for row in plan)
        all_plan = db.execute("EXPLAIN QUERY PLAN SELECT record_id FROM record_fields ORDER BY listing_public_date DESC,record_id DESC LIMIT 1").fetchall()
        assert any("COVERING INDEX ix_rf_listing_date" in row[3] for row in all_plan)
        search_plan = db.execute("EXPLAIN QUERY PLAN SELECT COUNT(*) FROM record_fields "
                                 "INDEXED BY ix_rf_listing_search WHERE title_display LIKE '%Synthetic%'").fetchall()
        assert any("COVERING INDEX ix_rf_listing_search" in row[3] for row in search_plan)
        page_plan = db.execute("EXPLAIN QUERY PLAN SELECT record_id FROM record_fields "
                               "INDEXED BY ix_rf_listing_search WHERE title_display LIKE '%' "
                               "ORDER BY listing_public_date DESC,record_id DESC LIMIT 1").fetchall()
        assert not any("TEMP B-TREE" in row[3] for row in page_plan)


def test_listing_search_helper_rejects_wrong_collation():
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("CREATE TABLE record_fields(listing_public_date,record_id,family,title_display,"
                   "natural_key,version_raw,investor_display,province,sectors_json)")
        db.execute("CREATE INDEX ix_rf_listing_search ON record_fields(listing_public_date DESC,"
                   "record_id DESC,family,title_display COLLATE NOCASE,natural_key,version_raw,"
                   "investor_display,province,sectors_json)")
        with pytest.raises(ValueError, match="listing search index is invalid"):
            materialise_listing_search(db)


def test_noop_rebuilds_when_the_listing_projection_is_missing(env, tmp_path: Path):
    writer, _, _, data_root = env
    commit_page(writer, [{"notifyNo": "SYN-NOOP-1", "notifyVersion": "00", "bidName": "Synthetic listing"}])
    warehouse, target = data_root / "warehouse.sqlite", tmp_path / "serving.sqlite"
    assert serving.build_serving_db(warehouse, target).published
    with closing(sqlite3.connect(target)) as db, db:
        db.execute("DROP TABLE IF EXISTS serving_listing_meta")
    assert not serving.snapshot_is_current(warehouse, target)


def test_snapshot_retains_source_display_fields_omitted_from_normalized_core(env, tmp_path: Path):
    writer, _, _, data_root = env
    commit_page(writer, [{"notifyNo": "SYN-QUOTE-DISPLAY", "notifyVersion": "00", "bidName": "Synthetic quote",
                         "investorCode": "SYN-OWNER", "bidCloseDate": "2026-11-20T09:00:00",
                         "sourceUrl": "https://muasamcong.mof.gov.vn/web/guest/contractor-selection?notifyNo=SYN-QUOTE-DISPLAY",
                         "private_operator_note": "synthetic internal note"}], family="YCBG")
    target = tmp_path / "serving.sqlite"
    result = serving.build_serving_db(data_root / "warehouse.sqlite", target)
    assert result.published, result.reason
    with closing(sqlite3.connect(target)) as db:
        assert "market_fields_json" in {row[1] for row in db.execute("PRAGMA table_info(record_fields)")}
        fields = json.loads(db.execute("SELECT market_fields_json FROM record_fields").fetchone()[0])
        assert fields["investorCode"] == "SYN-OWNER"
        assert fields["bidCloseDate"] == "2026-11-20T09:00:00"
        assert fields["sourceUrl"].endswith("SYN-QUOTE-DISPLAY")
        assert "private_operator_note" not in fields


def test_invalid_market_projection_preserves_the_previous_snapshot(env, tmp_path: Path, monkeypatch):
    writer, _, _, data_root = env
    commit_page(writer, [{"notifyNo": "SYN-PRESERVE", "notifyVersion": "00", "bidName": "Synthetic record"}])
    target = tmp_path / "serving.sqlite"
    assert serving.build_serving_db(data_root / "warehouse.sqlite", target).published
    before = target.read_bytes()

    def invalid(_fields):
        raise ValueError("Synthetic invalid display projection")

    monkeypatch.setattr(read_model, "market_fields_json", invalid)
    result = serving.build_serving_db(data_root / "warehouse.sqlite", target)
    assert not result.published
    assert target.read_bytes() == before
