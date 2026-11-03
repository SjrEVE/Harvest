from __future__ import annotations

from contextlib import closing
from pathlib import Path

from test_backup_restore import commit_page

from santhau_studio.desktop.backend import StudioBackend
from santhau_studio.storage.serving import build_serving_db
from santhau_studio.storage.warehouse import WarehouseWriter


def database(tmp_path: Path) -> StudioBackend:
    root = tmp_path / "data"
    with closing(WarehouseWriter.open(root, create_schema=True)) as writer:
        writer.register_run("run-1", "job-1", "BACKFILL")
        rows = [
            {"notifyNo": "A", "notifyVersion": "00", "bidName": "Thiết bị y tế", "investorName": "Bệnh viện A", "bidPrice": 100, "currency": "VND", "province": "Hà Nội", "investorCode": "0101", "publicDate": "2026-10-01T02:00:00Z"},
            {"notifyNo": "B", "notifyVersion": "00", "bidName": "Thiết bị trường học", "bidPrice": 0, "currency": "VND", "province": "Hà Nội", "publicDate": "2026-10-02T02:00:00Z"},
            {"notifyNo": "C", "notifyVersion": "00", "bidName": "Thiết bị văn phòng", "publicDate": "2026-10-03T02:00:00Z"},
        ]
        # Preserve extra filter fields in the stored normalized projection as a real legacy
        # or extended adapter would; no fake query handler is injected.
        commit_page(writer, rows)
        import json
        for n, row in enumerate(rows, 1):
            writer.conn.execute("UPDATE source_record_versions SET normalized_json=? WHERE source_record_id=?",
                                (json.dumps({**row, "title": row["bidName"]}, ensure_ascii=False), n))
    assert build_serving_db(root / "warehouse.sqlite", root / "serving.sqlite").published
    return StudioBackend(root)


def test_price_unknown_is_distinct_from_zero_and_range(tmp_path: Path) -> None:
    backend = database(tmp_path)
    unknown = backend.search({"browse": True, "price_state": "UNKNOWN"})
    assert [r["natural_key"] for r in unknown["hits"]] == ["C"]
    zero = backend.search({"browse": True, "price_min": 0, "price_max": 0, "currency": "VND"})
    assert [r["natural_key"] for r in zero["hits"]] == ["B"]


def test_combined_filters_and_stable_cursor(tmp_path: Path) -> None:
    backend = database(tmp_path)
    result = backend.search({"text": "thiet bi", "province": "Hà Nội", "date_to": "2026-10-01", "tax_id": "0101"})
    assert [r["natural_key"] for r in result["hits"]] == ["A"]
    query = {"text": "thiet bi", "sort": "natural_key", "direction": "asc", "limit": 1}
    first = backend.search(query)
    second = backend.search({**query, "cursor": first["next_cursor"]})
    assert first["hits"][0]["natural_key"] == "A"
    assert second["hits"][0]["natural_key"] == "B"
    invalid = backend.search({**query, "text": "other", "cursor": first["next_cursor"]})
    assert invalid["error_code"] == "INVALID_CURSOR"


def test_cursor_is_invalidated_after_snapshot_publish(tmp_path: Path) -> None:
    backend = database(tmp_path)
    query = {"browse": True, "limit": 1}
    first = backend.search(query)
    assert build_serving_db(backend.data_root / "warehouse.sqlite", backend.serving_path).published
    result = backend.search({**query, "cursor": first["next_cursor"]})
    assert result["error_code"] == "CURSOR_EXPIRED"
