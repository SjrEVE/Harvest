import json
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo

from santhau_studio.collectors.offline_acceptance import initialize
from santhau_studio.desktop.backend import StudioBackend
from santhau_studio.storage.warehouse import connect_read_only


def test_identity_cannot_confuse_offline_root_with_real_data(tmp_path):
    initialize(tmp_path)
    result = StudioBackend(tmp_path).workspace_identity()
    assert result["synthetic"] is True
    assert "KHÔNG PHẢI DỮ LIỆU THẬT" in result["label"]
    assert result["data_root"] == str(tmp_path.resolve())


def test_copied_synthetic_root_is_still_quarantined(tmp_path):
    import shutil
    initialize(tmp_path/"original")
    shutil.copytree(tmp_path/"original", tmp_path/"copy")
    backend = StudioBackend(tmp_path/"copy")
    assert backend.workspace_identity()["synthetic"]
    assert backend._collection_blocker()


def test_source_fields_keep_every_original_member_without_inventing_null(tmp_path):
    from santhau_studio.desktop.data_quality import source_fields
    from santhau_studio.storage.rawstore import RawObjectStore
    initialize(tmp_path)
    with closing(RawObjectStore(tmp_path)) as store:
        raw = {"bidName": ["Tên gói"], "zero": 0, "empty": None,
               "locations": [{"provName": "Hà Nội"}, {"provName": "Huế"}], "newSourceField": {"nested": 3}}
        obj = store.put(json.dumps(raw).encode())
        result = source_fields(tmp_path, obj.sha256)
    assert result["verified"]
    assert result["values"] == raw
    assert len(result["fields"]) == len(raw)
    assert next(x for x in result["fields"] if x["key"] == "zero")["value"] == 0
    assert next(x for x in result["fields"] if x["key"] == "empty")["presence"] == "UNKNOWN"


def test_coverage_range_is_not_limited_to_seven_days(tmp_path):
    initialize(tmp_path)
    result = StudioBackend(tmp_path).coverage_range({"date_from":"01/01/2022","date_to":"31/10/2026"})
    assert result["ok"] and result["synthetic"]
    assert len(result["periods"]) == 58
    assert result["periods"][0]["label"] == "10/2026"
    assert result["periods"][-1]["label"] == "01/2022"
    assert all(len(p["families"]) == 6 for p in result["periods"])
    assert all(f["status"] == "NOT_ACCESSED" for p in result["periods"] for f in p["families"])


def test_coverage_drilldown_includes_last_vietnamese_day(tmp_path):
    initialize(tmp_path)
    result = StudioBackend(tmp_path).coverage_range({"date_from":"01/09/2026","date_to":"30/09/2026","resolution":"DAY"})
    assert result["ok"] and len(result["periods"]) == 30
    assert result["periods"][0]["label"] == "30/09/2026"
    assert result["periods"][-1]["label"] == "01/09/2026"
    assert not StudioBackend(tmp_path).coverage_range({"date_from":"01/01/2022","date_to":"31/10/2026","resolution":"DAY"})["ok"]


def test_default_collection_period_starts_in_2022(tmp_path):
    result = StudioBackend(tmp_path).workspace_identity()
    assert result["default_from"] == "01/01/2022"
    assert datetime.strptime(result["default_to"], "%d/%m/%Y").date() < datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()


def test_partial_month_is_explicit_in_coverage(tmp_path):
    initialize(tmp_path)
    result = StudioBackend(tmp_path).coverage_range({"date_from":"01/10/2026","date_to":"03/11/2026"})
    assert result["periods"][0]["partial_period"]
    assert result["periods"][0]["date_to"] == "03/11/2026"
    assert not result["periods"][1]["partial_period"]


def test_unknown_source_key_counts_not_data_loss(tmp_path):
    from santhau_studio.desktop.data_quality import audit_root
    initialize(tmp_path)
    result = audit_root(tmp_path)
    assert result["synthetic"]
    assert result["integrity"] == "ok"
    assert result["raw_failures"] == []
    assert result["business_completeness"] == "NOT_VERIFIED"
    with closing(connect_read_only(tmp_path/"warehouse.sqlite")) as conn:
        assert conn.execute("SELECT count(*) FROM source_records").fetchone()[0] == 0
