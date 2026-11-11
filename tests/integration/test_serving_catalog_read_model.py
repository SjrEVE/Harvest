from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from test_serving_snapshot import commit_page

from santhau_studio.storage import serving

pytest_plugins = ("test_serving_snapshot",)


def test_snapshot_materializes_exact_khlcnt_catalog_aggregates(env, tmp_path: Path) -> None:
    writer, _, _, data_root = env
    commit_page(
        writer,
        [
            {
                "planNo": "PL-CAT-1",
                "planVersion": "00",
                "bidName": ["Gói thầu A", "Gói thầu A"],
                "publicDate": "2026-11-02T00:00:00",
                "investorName": "Chủ đầu tư A",
                "investField": ["XL"],
                "provinceName": "Thành phố Hà Nội",
                "numBidderJoin": 2,
                "status": "01",
            },
            {
                "planNo": "PL-CAT-2",
                "planVersion": "00",
                "bidName": ["Gói thầu B"],
                "numBidderJoin": 0,
            },
        ],
        family="KHLCNT",
    )
    commit_page(
        writer,
        [{"notifyNo": "TB-CAT-1", "notifyVersion": "00", "bidName": "TBMT ngoài catalog"}],
    )

    result = serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite")
    assert result.published, result.reason

    with closing(sqlite3.connect(tmp_path / "serving.sqlite")) as db:
        db.row_factory = sqlite3.Row
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "serving_catalog_meta",
            "serving_catalog_records",
            "serving_catalog_family_summary",
            "serving_catalog_time_buckets",
            "serving_catalog_dimension_memberships",
            "serving_catalog_dimension_counts",
        } <= tables

        meta = db.execute("SELECT * FROM serving_catalog_meta").fetchone()
        assert meta["singleton_id"] == 1
        assert meta["catalog_schema_version"] == 1
        assert meta["prepared_families_json"] == '[]'
        assert meta["source_projection_version"] == serving.PROJECTION_VERSION
        serving_meta = json.loads(
            db.execute("SELECT row_counts_json FROM serving_meta LIMIT 1").fetchone()[0]
        )
        assert serving_meta["derived_catalog"]["family_latest_counts"] == {
            "TBMT": 1,
            "YCBG": 0,
            "DU_AN": 0,
            "KHLCNT": 2,
            "KQLCNT": 0,
            "HOP_DONG": 0,
        }

        source_latest = db.execute(
            "SELECT COUNT(*) FROM record_fields WHERE family='KHLCNT' AND is_latest=1"
        ).fetchone()[0]
        assert db.execute("SELECT COUNT(*) FROM serving_catalog_records").fetchone()[0] == source_latest == 2

        summary = db.execute(
            "SELECT * FROM serving_catalog_family_summary WHERE family='KHLCNT'"
        ).fetchone()
        assert summary["total_records"] == 2
        assert summary["known_dates"] == 1
        assert summary["missing_dates"] == 1
        assert summary["with_winners"] == 0
        assert summary["investor_count"] == 1
        assert (summary["positive"], summary["zero"], summary["missing"]) == (1, 1, 0)
        assert (summary["bucket_1"], summary["bucket_2"], summary["bucket_3_5"], summary["bucket_6_plus"]) == (0, 1, 0, 0)

        time_buckets = db.execute(
            "SELECT bucket_key,record_count FROM serving_catalog_time_buckets"
            " WHERE family='KHLCNT' AND bucket_kind='month'"
        ).fetchall()
        assert [(row["bucket_key"], row["record_count"]) for row in time_buckets] == [("2026-11", 1)]

        dimensions = {
            (row["dimension_kind"], row["dimension_value"]): row["record_count"]
            for row in db.execute(
                "SELECT dimension_kind,dimension_value,record_count"
                " FROM serving_catalog_dimension_counts WHERE family='KHLCNT'"
            )
        }
        assert dimensions == {
            ("sector", "UNKNOWN"): 1,
            ("sector", "XL"): 1,
            ("province", "Chưa rõ địa bàn"): 1,
            ("province", "Hà Nội"): 1,
            ("investor", "Chưa rõ đơn vị"): 1,
            ("investor", "Chủ đầu tư A"): 1,
        }
        assert db.execute(
            "SELECT COUNT(*) FROM serving_catalog_records r"
            " JOIN serving_catalog_dimension_memberships d ON d.record_id=r.record_id"
            " WHERE d.dimension_kind='sector' AND d.dimension_value='XL'"
        ).fetchone()[0] == 1


def test_catalog_projection_does_not_change_source_snapshot_counts(env, tmp_path: Path) -> None:
    writer, warehouse, _, data_root = env
    commit_page(
        writer,
        [{"planNo": "PL-CAT-3", "planVersion": "00", "bidName": ["Thiết bị"]}],
        family="KHLCNT",
    )
    result = serving.build_serving_db(data_root / "warehouse.sqlite", tmp_path / "serving.sqlite")
    assert result.published, result.reason
    assert result.snapshot_rows["source_records"] == warehouse.execute("SELECT COUNT(*) FROM source_records").fetchone()[0]


def test_snapshot_is_current_rejects_derived_catalog_count_drift(env, tmp_path: Path) -> None:
    writer, _, _, data_root = env
    commit_page(
        writer,
        [{"planNo": "PL-CAT-4", "planVersion": "00", "bidName": ["Snapshot parity"]}],
        family="KHLCNT",
    )
    warehouse = data_root / "warehouse.sqlite"
    target = tmp_path / "serving.sqlite"
    result = serving.build_serving_db(warehouse, target)
    assert result.published, result.reason
    assert serving.snapshot_is_current(warehouse, target)

    with closing(sqlite3.connect(target)) as db:
        db.execute("DELETE FROM serving_catalog_records")
        db.commit()

    assert not serving.snapshot_is_current(warehouse, target)


