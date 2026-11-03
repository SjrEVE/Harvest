"""Regression cases from public RC3 captures; these tests never open a network socket."""
from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

from test_backup_restore import commit_page

from santhau_studio.collectors.egp_home import SearchWindow, build_search_body
from santhau_studio.collectors.extraction import extract_page, verify_extraction
from santhau_studio.collectors.source_registry import binding_for, search_shape_for
from santhau_studio.desktop.backend import StudioBackend
from santhau_studio.storage.serving import build_serving_db
from santhau_studio.storage.warehouse import WarehouseWriter

FIXTURES = Path(__file__).parents[1] / "fixtures/public"


def captured_rows():
    return [json.loads(p.read_text(encoding="utf-8"))["page"]["content"][0]
            for p in sorted(FIXTURES.glob("*-listing-*.json"))]


def test_real_price_arrays_and_nested_locations_reach_search(tmp_path):
    root = tmp_path / "data"
    with closing(WarehouseWriter.open(root, create_schema=True)) as writer:
        writer.register_run("run-1", "job-1", "BACKFILL")
        commit_page(writer, captured_rows())
    assert build_serving_db(root / "warehouse.sqlite", root / "serving.sqlite").published
    backend = StudioBackend(root)
    hits = {r["natural_key"]: r for r in backend.search({"browse": True})["hits"]}
    assert hits["IB2600570777"]["bid_price"] == 1999166500
    assert hits["IB2600572157"]["bid_price"] == 1447200000
    assert hits["IB2500257256"]["bid_price"] == 1851207200
    assert hits["IB2500257256"]["award_price"] == 1678984700
    assert hits["IB2600570777"]["award_price"] is None, "five lot prices cannot become one guessed amount"
    assert all(r["currency"] is None for r in hits.values()), "listing did not declare a currency"
    assert {r["natural_key"] for r in backend.search({"province": "Hà Nội"})["hits"]} == {"IB2600570777", "IB2500257256"}
    assert [r["natural_key"] for r in backend.search({"province": "Tỉnh Khánh Hòa"})["hits"]] == ["IB2600572157"]


def test_result_query_does_not_exclude_awards_with_a_prior_notice():
    body = build_search_body(0, shape=search_shape_for("KQLCNT"),
                             window=SearchWindow(True, "2026-10-20 00:00:00", "2026-10-20 23:59:59"))
    filters = {r["fieldName"]: r for r in body[0]["query"][0]["filters"]}
    assert "caseKHKQ" not in filters
    assert "publicDate" not in filters
    assert filters["publicDateKqlcnt"]["searchType"] == "range"
    assert body[0]["sortBy"] == "publicDateKqlcnt"


def test_result_extraction_keeps_published_awards_and_excludes_pending_notices():
    payload = {"page": {"content": captured_rows(), "totalElements": 3}}
    report = extract_page(payload, binding_for("KQLCNT").spec)
    assert [r.normalized.natural_key for r in report.records] == ["IB2600570777", "IB2500257256"]
    assert report.filtered_members == 1
    assert report.records[1].locator == "/page/content/2", "retain pointer into original response"
    verify_extraction(payload, report)


def test_multiple_provinces_zero_and_multiple_amounts_are_not_flattened(tmp_path):
    root = tmp_path / "data"
    rows = [{"notifyNo": "MULTI", "notifyVersion": "00", "bidPrice": [10, 20],
             "winningCode": ["A", "B"], "locations": [{"provName": "Thành phố Hà Nội", "provCode": "01"},
                                                       {"provName": "Tỉnh Khánh Hòa", "provCode": "56"}]},
            {"notifyNo": "ZERO", "notifyVersion": "00", "bidPrice": [0], "currency": ["VND"]}]
    with closing(WarehouseWriter.open(root, create_schema=True)) as writer:
        writer.register_run("run-1", "job-1", "BACKFILL")
        commit_page(writer, rows)
    assert build_serving_db(root / "warehouse.sqlite", root / "serving.sqlite").published
    backend = StudioBackend(root)
    for province in ("Hà Nội", "Khánh Hòa", "01", "56"):
        found = backend.search({"province": province})["hits"]
        assert [r["natural_key"] for r in found] == ["MULTI"]
        assert found[0]["bid_price"] is None
        assert found[0]["tax_id"] is None
    zero = backend.search({"price_min": 0, "price_max": 0, "currency": "VND"})["hits"]
    assert [r["natural_key"] for r in zero] == ["ZERO"]


def test_legacy_result_boundary_rule_is_not_reused(tmp_path):
    from santhau_studio.collectors.canary import PROBE_UNVERIFIED, canary_state
    from santhau_studio.storage.coverage import boundary_rule

    with closing(WarehouseWriter.open(tmp_path / "rules", create_schema=True)) as writer:
        writer.conn.execute("INSERT INTO filter_boundary_rules(rule_key,rule_version,source_key,family,endpoint,adapter_sha,"
                            "boundary_kind,precision,source_timezone,offset_minutes,conversion_params,canary_verified,verified_at_utc)"
                            " VALUES('old',1,'egp_home','KQLCNT','/search',?,'CLOSED_LOCAL','DAY','Asia/Ho_Chi_Minh',420,?,1,'2026-11-01T00:00:00Z')",
                            ("a" * 64, '{"to_minus":"1s"}'))
        assert boundary_rule(writer.conn, source_key="egp_home", family="KQLCNT", endpoint="/search", adapter_sha="a" * 64) is None
        state = canary_state(writer.conn, "egp_home", "KQLCNT", "a" * 64)
        assert state["state"] == PROBE_UNVERIFIED and not state["rule_verified"]


def test_result_date_and_existing_proofs_follow_the_current_query_contract(tmp_path, monkeypatch):
    from test_e2e_fake_source import ADAPTER_SHA, DAY, FakePortal, make_runner, make_writer

    from santhau_studio.collectors import source_registry
    from santhau_studio.collectors.canary import PROBE_UNVERIFIED, canary_state
    from santhau_studio.storage import coverage

    writer = make_writer(tmp_path / "proof")
    try:
        writer.register_run("regression", "job", "BACKFILL")
        # Create a synthetic historical proof under the old acceptance policy, then
        # restore the new policy. Never mutate a rule already cited by a proof.
        writer.conn.execute("UPDATE filter_boundary_rules SET conversion_params=? WHERE family='KQLCNT'", ('{"to_minus":"1s"}',))
        with monkeypatch.context() as legacy:
            legacy.setattr(source_registry, "query_contract_for", lambda _family: None)
            result = make_runner(writer, "KQLCNT", FakePortal()).backfill("regression", DAY)[0]
            assert result.status == "COMPLETE"
            coverage.advance_watermark(writer.conn, source_key="egp_home", family="KQLCNT",
                                        to_utc=DAY.to_utc, complete_interval_id=result.interval_id)
            assert coverage.watermark_for(writer.conn, "egp_home", "KQLCNT") == DAY.to_utc
        assert not coverage.resolve(writer.conn, "egp_home", "KQLCNT", DAY.from_utc, DAY.to_utc).can_skip
        assert coverage.watermark_for(writer.conn, "egp_home", "KQLCNT") is None
        assert canary_state(writer.conn, "egp_home", "KQLCNT", ADAPTER_SHA)["state"] == PROBE_UNVERIFIED
        assert writer.conn.execute("SELECT COUNT(*) FROM coverage_proofs").fetchone()[0] == 1
    finally:
        writer.close()


def test_result_search_uses_result_publication_date_not_notice_date(tmp_path):
    root = tmp_path / "dates"
    with closing(WarehouseWriter.open(root, create_schema=True)) as writer:
        writer.register_run("run-1", "job-1", "BACKFILL")
        commit_page(writer, captured_rows(), family="KQLCNT")
    assert build_serving_db(root / "warehouse.sqlite", root / "serving.sqlite").published
    backend = StudioBackend(root)
    matches = backend.search({"family": "KQLCNT", "date_from": "2026-10-20", "date_to": "2026-10-20"})["hits"]
    assert [r["natural_key"] for r in matches] == ["IB2600570777"]
    assert not backend.search({"family": "KQLCNT", "date_to": "2026-10-08", "date_from": "2026-10-08"})["hits"]
