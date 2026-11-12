"""Refresh must retire one observation without erasing another lane's evidence."""

import json
from datetime import UTC, datetime

import pytest
from test_deep_run import seed_records

from santhau_studio.collectors.cgttrg import public_spec
from santhau_studio.storage.deep import record_deep_capture


@pytest.fixture
def warehouse(tmp_path):
    writer = seed_records(tmp_path)
    yield writer
    writer.close()


def payload(*, party=True, document="decision.pdf", plan=None):
    value = {"notifyNo": "IB-0", "bidForm": "CGTTRG", "notifyVersion": "00"}
    if party:
        value["contractorsResult"] = [{"contractorCode": "org-1", "contractorName": "Nhà thầu A"}]
    if document:
        value["bidPlan"] = {"decisionFileId": document, "decisionFileName": document}
    if plan:
        value.setdefault("bidPlan", {}).update(planNo=plan, planVersion="00")
    return value


def capture(writer, value, lane="DETAIL"):
    rid = writer.conn.execute("SELECT MIN(id) FROM source_records").fetchone()[0]
    return record_deep_capture(
        writer,
        run_id="refresh-test",
        source_record_id=rid,
        spec=public_spec("TBMT", lane),
        completeness="FULL",
        response_payload=value,
        detail=value,
        now=datetime.now(UTC),
    )


def test_party_survives_until_both_confirming_lanes_retire_it(warehouse):
    capture(warehouse, payload())
    capture(warehouse, payload(), "REOFFER")
    capture(warehouse, payload(party=False))
    rows = warehouse.conn.execute("SELECT * FROM contractor_memberships").fetchall()
    assert len(rows) == 1
    assert rows[0]["party_name"] == "Nhà thầu A"
    assert json.loads(rows[0]["provenance_json"])["lane"] == "REOFFER"
    capture(warehouse, payload(party=False), "REOFFER")
    assert warehouse.conn.execute("SELECT COUNT(*) FROM contractor_memberships").fetchone()[0] == 0
    assert warehouse.conn.execute("SELECT COUNT(*) FROM deep_capture_history").fetchone()[0] == 4
    assert not warehouse.conn.execute("PRAGMA foreign_key_check").fetchall()


def test_replaced_document_reference_is_removed_but_history_remains(warehouse):
    first = capture(warehouse, payload(document="old.pdf"))
    capture(warehouse, payload(document="new.pdf"))
    assert [r[0] for r in warehouse.conn.execute("SELECT name FROM documents")] == ["new.pdf"]
    assert warehouse.store.read(first.response_object_sha)
    assert warehouse.conn.execute("SELECT COUNT(*) FROM deep_capture_history").fetchone()[0] == 2


def test_shared_document_survives_one_lane_then_disappears(warehouse):
    capture(warehouse, payload())
    capture(warehouse, payload(), "REOFFER")
    capture(warehouse, payload(document=None))
    assert warehouse.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    capture(warehouse, payload(document=None), "REOFFER")
    assert warehouse.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


def test_removing_reference_never_deletes_downloaded_bytes(warehouse):
    first = capture(warehouse, payload())
    warehouse.conn.execute("UPDATE documents SET object_sha=?", (first.response_object_sha,))
    capture(warehouse, payload(document=None))
    row = warehouse.conn.execute("SELECT * FROM documents").fetchone()
    assert row["object_sha"] == first.response_object_sha
    assert json.loads(row["provenance_json"])["reference_current"] is False


def test_changed_plan_retires_only_automatic_relation(warehouse):
    ids = [r[0] for r in warehouse.conn.execute("SELECT id FROM source_records ORDER BY id")]
    for rid, key in zip(ids[1:], ("PL-A", "PL-B"), strict=True):
        warehouse.conn.execute(
            "UPDATE source_records SET family='KHLCNT',natural_key=? WHERE id=?", (key, rid)
        )
    capture(warehouse, payload(plan="PL-A"))
    capture(warehouse, payload(plan="PL-B"))
    rows = warehouse.conn.execute("SELECT from_entity FROM relation_edges").fetchall()
    assert [r[0] for r in rows] == [f"source-record:{ids[2]}"]
    assert not warehouse.conn.execute("PRAGMA foreign_key_check").fetchall()


def test_invalid_refresh_retains_previous_projection(warehouse):
    capture(warehouse, payload())
    failed = capture(
        warehouse, {"notifyNo": "IB-0", "bidForm": "CGTTRG", "contractorsResult": "broken"}
    )
    assert failed.completeness == "PARTIAL"
    assert warehouse.conn.execute("SELECT COUNT(*) FROM contractor_memberships").fetchone()[0] == 1
    assert warehouse.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    assert warehouse.store.read(failed.response_object_sha)


def test_unchanged_refresh_repairs_ownership_from_previous_runtime(warehouse):
    capture(warehouse, payload())
    second = capture(warehouse, payload(), "REOFFER")
    # Old binaries deduplicated the shared row and retained only one lane's evidence.
    row = warehouse.conn.execute("SELECT provenance_json FROM contractor_memberships").fetchone()
    original = json.loads(row[0])["confirmations"][0]
    warehouse.conn.execute(
        "UPDATE contractor_memberships SET provenance_json=?", (json.dumps(original),)
    )
    refreshed = capture(warehouse, payload(), "REOFFER")
    assert refreshed.capture_seq == second.capture_seq
    capture(warehouse, payload(party=False))
    assert warehouse.conn.execute("SELECT COUNT(*) FROM contractor_memberships").fetchone()[0] == 1
    assert warehouse.conn.execute("SELECT COUNT(*) FROM deep_capture_history").fetchone()[0] == 3


def test_new_file_with_same_name_keeps_downloaded_old_file(warehouse):
    first = capture(warehouse, payload())
    warehouse.conn.execute("UPDATE documents SET object_sha=?", (first.response_object_sha,))
    changed = payload(party=False)
    changed["bidPlan"]["decisionFileId"] = "different-file-same-name"
    refreshed = capture(warehouse, changed)
    assert refreshed.completeness == "FULL"
    assert warehouse.conn.execute("SELECT COUNT(*) FROM contractor_memberships").fetchone()[0] == 0
    docs = warehouse.conn.execute("SELECT * FROM documents ORDER BY id").fetchall()
    assert len(docs) == 2
    doc = next(row for row in docs if row["object_sha"])
    assert doc["object_sha"] == first.response_object_sha
    assert json.loads(doc["provenance_json"])["source_file_id"] == "decision.pdf"
    assert json.loads(doc["provenance_json"])["reference_current"] is False
    assert (
        json.loads(warehouse.store.read(refreshed.response_object_sha))["bidPlan"]["decisionFileId"]
        == "different-file-same-name"
    )


@pytest.mark.parametrize(
    "field,bad",
    [
        ("contractorsResult", {}),
        ("contractorsResult", ""),
        ("contractorsResult", 0),
        ("contractorsResult", [None]),
        ("bidPlan", []),
        ("bidPlan", "bad"),
    ],
)
def test_malformed_nested_response_is_retained_without_erasing_good_projection(
    warehouse, field, bad
):
    capture(warehouse, payload())
    changed = payload()
    changed[field] = bad
    failed = capture(warehouse, changed)
    assert failed.completeness == "PARTIAL"
    assert warehouse.conn.execute("SELECT COUNT(*) FROM contractor_memberships").fetchone()[0] == 1
    assert warehouse.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    assert json.loads(warehouse.store.read(failed.response_object_sha))[field] == bad
