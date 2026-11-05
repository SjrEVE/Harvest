import json
import os
import sys
from pathlib import Path

import pytest

from santhau_studio.collectors.cgttrg import public_spec
from santhau_studio.collectors.deep import parse_detail


def hsmt(rows):
    return {
        "bidoInvBiddingDTO": [
            {
                "notifyNo": "IB-TEST",
                "notifyId": "notice-id",
                "notifyVersion": "00",
                "bidForm": "CGTTRG",
                "formCode": "goods",
                "formContent": '[{"columnName":"extra"}]',
                "formValue": json.dumps({"Table": rows}),
            }
        ]
    }


def test_parent_lots_and_unallotted_rows_preserve_extra_fields():
    value = hsmt(
        [
            {"id": 7, "lotNo": "PP-1", "lotName": "Lot", "name": None},
            {"id": 8, "parent": 7, "name": "Same", "qty": 2, "uom": "box", "extra": 42},
            {"id": 9, "parent": 7, "name": "Same", "qty": "bad"},
            {"id": 10, "parent": 0, "name": "Standalone", "qty": 1},
        ]
    )
    result = parse_detail(public_spec("TBMT", "HSMT"), value)
    assert len(result.lots) == 1 and len(result.lots[0].goods) == 2
    assert result.lots[0].goods[0].raw["extra"] == 42
    assert result.lots[0].goods[1].quantity_presence == "UNPARSABLE"
    assert len(result.unallotted_goods) == 1 and result.problems
    assert result.hsmt["forms"][0]["formContent"] == value["bidoInvBiddingDTO"][0]["formContent"]


def test_offerors_not_members_or_tax_ids_and_plan_file_not_hsmt():
    value = {
        "bidForm": "CGTTRG",
        "notifyNo": "IB-TEST",
        "notifyVersion": "00",
        "isMultiLot": 0,
        "planNo": "PL-TEST",
        "bidPlan": {
            "planNo": "PL-TEST",
            "planVersion": "00",
            "decisionFileId": "file-1",
            "decisionFileName": "decision.pdf",
        },
        "contractorsResult": [{"contractorCode": "org-1", "contractorName": "A"}] * 2,
        "contractorsResultAll": [{"contractorCode": "org-1", "lotNo": "BP-1"}] * 10,
    }
    result = parse_detail(public_spec("TBMT", "REOFFER"), value)
    assert len(result.members) == 1 and result.members[0].role == "OFFEROR"
    assert result.members[0].party_tax_id is None and not result.lots
    assert result.documents[0]["document_kind"] == "PLAN_DECISION"
    assert result.documents[0]["download_status"] == "REFERENCE_ONLY"
    assert result.relations[0]["key"] == "PL-TEST"


@pytest.mark.parametrize(
    "file,lots,goods",
    [("hsmt-probe-6.json", 3, 28), ("hsmt-probe-3.json", 0, 1), ("hsmt-probe-1.json", 0, 38)],
)
def test_saved_real_payload_contract(file, lots, goods):
    root = os.environ.get("SANTHAU_DEEP_EVIDENCE")
    if not root:
        pytest.skip("local source evidence is kept outside Git")
    value = json.loads((Path(root) / file).read_text(encoding="utf-8"))
    result = parse_detail(public_spec("TBMT", "HSMT"), value)
    assert len(result.lots) == lots
    assert sum(len(lot.goods) for lot in result.lots) + len(result.unallotted_goods) == goods
    assert not result.problems
    if lots:
        assert [len(lot.goods) for lot in result.lots] == [8, 13, 7]


def test_capture_rerun_refresh_cross_lane_and_unallotted_storage(tmp_path):
    from datetime import UTC, datetime

    from test_deep_run import seed_records

    from santhau_studio.storage.deep import record_deep_capture
    from santhau_studio.storage.serving import build_serving_db

    writer = seed_records(tmp_path)
    try:
        rid = writer.conn.execute("SELECT id FROM source_records ORDER BY id LIMIT 1").fetchone()[0]
        payload = hsmt([{"name": "Same", "qty": 1, "extra": "first"}, {"name": "Same", "qty": 2}])

        def capture(value, lane="HSMT"):
            return record_deep_capture(
                writer,
                run_id="run-deep",
                source_record_id=rid,
                spec=public_spec("TBMT", lane),
                completeness="FULL",
                response_payload=value,
                detail=value,
                now=datetime.now(UTC),
            )

        first = capture(payload)
        assert capture(payload).capture_seq == first.capture_seq
        assert writer.conn.execute("SELECT COUNT(*) FROM goods_rows").fetchone()[0] == 2
        assert writer.conn.execute("SELECT COUNT(*) FROM lots").fetchone()[0] == 0
        assert (
            writer.conn.execute("SELECT COUNT(*) FROM goods_rows WHERE lot_id IS NULL").fetchone()[
                0
            ]
            == 2
        )
        capture(
            {
                "notifyNo": "IB-TEST",
                "bidForm": "CGTTRG",
                "contractorsResult": [{"contractorCode": "org-1", "contractorName": "A"}],
            },
            "REOFFER",
        )
        assert writer.conn.execute("SELECT COUNT(*) FROM goods_rows").fetchone()[0] == 2
        payload["bidoInvBiddingDTO"][0]["notifyVersion"] = "01"
        assert capture(payload).capture_seq == 2
        assert writer.conn.execute("SELECT COUNT(*) FROM contractor_memberships").fetchone()[0] == 1
        assert writer.conn.execute("SELECT COUNT(*) FROM deep_capture_history").fetchone()[0] == 3
        bad = {"bidoInvBiddingDTO": [{"bidForm": "CGTTRG", "formValue": "invalid"}]}
        failed = capture(bad)
        assert failed.completeness == "PARTIAL"
        assert writer.store.read(failed.response_object_sha)
        assert writer.conn.execute("SELECT COUNT(*) FROM goods_rows").fetchone()[0] == 2
        assert build_serving_db(
            tmp_path / "warehouse.sqlite", tmp_path / "serving.sqlite"
        ).published
        if sys.platform != "win32":
            return  # Desktop IPC is Windows-only; persistence above is portable.
        from santhau_studio.desktop.backend import StudioBackend
        detail = StudioBackend(tmp_path).record_detail(rid)
        assert len(detail["goods_rows"]) == 2
    finally:
        writer.close()


def test_public_batch_checkpoints_and_reports_parse_failure(tmp_path):
    from test_deep_run import seed_records

    from santhau_studio.workers.deep_run import run_deep_batch

    writer = seed_records(tmp_path)
    calls = []
    rid = writer.conn.execute("SELECT id FROM source_records ORDER BY id LIMIT 1").fetchone()[0]
    payload = hsmt([{"name": "A", "qty": 1}])
    payload["bidoInvBiddingDTO"][0]["notifyNo"] = "IB-0"

    def fetch(_f, _k):
        calls.append(1)
        return payload

    def collect(force=False):
        return run_deep_batch(
            writer,
            run_id="deep",
            family="TBMT",
            lane="HSMT",
            scope={"record_ids": [rid], "force_refresh": force},
            fetcher=fetch,
            spec_provider=public_spec,
        )

    try:
        assert collect().status == "COMPLETED"
        assert collect().detail["skipped"] == 1 and len(calls) == 1
        payload["bidoInvBiddingDTO"][0]["formValue"] = "bad json"
        assert collect(True).status == "PARTIAL"
        row = writer.conn.execute("SELECT state,response_object_sha FROM deep_tasks").fetchone()
        assert row["state"] == "PARSE_ERROR" and row["response_object_sha"]
        from santhau_studio.desktop.deep_status import states

        assert (
            next(s for s in states(writer.conn, rid) if s["lane"] == "HSMT")["state"]
            == "PARSE_ERROR"
        )
    finally:
        writer.close()


def test_identity_mismatch_preserves_raw_but_never_projects_wrong_goods(tmp_path):
    from test_deep_run import seed_records

    from santhau_studio.workers.deep_run import run_deep_batch

    writer = seed_records(tmp_path)
    try:
        rid = writer.conn.execute("SELECT id FROM source_records ORDER BY id LIMIT 1").fetchone()[0]
        payload = hsmt([{"name": "wrong notice", "qty": 1}])
        result = run_deep_batch(
            writer,
            run_id="wrong-id",
            family="TBMT",
            lane="HSMT",
            scope={"record_ids": [rid]},
            fetcher=lambda _f, _k: payload,
            spec_provider=public_spec,
        )
        assert result.status == "FAILED"
        assert writer.conn.execute("SELECT COUNT(*) FROM goods_rows").fetchone()[0] == 0
        row = writer.conn.execute(
            "SELECT completeness,lane_status,response_object_sha FROM deep_captures"
        ).fetchone()
        assert row["completeness"] == "PARTIAL" and row["lane_status"] == "FAILED"
        assert json.loads(writer.store.read(row["response_object_sha"])) == payload
    finally:
        writer.close()


def test_unknown_parent_and_ambiguous_quantity_remain_visible():
    value = hsmt([{"name": "A", "parent": 999, "qty": "1.000"}, "broken-row"])
    parsed = parse_detail(public_spec("TBMT", "HSMT"), value)
    assert len(parsed.unallotted_goods) == 2 and parsed.problems
    assert parsed.unallotted_goods[0].quantity is None
    assert parsed.unallotted_goods[0].quantity_raw == "1.000"
    assert parsed.unallotted_goods[1].raw == "broken-row"
