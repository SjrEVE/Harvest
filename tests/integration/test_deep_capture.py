"""Milestone D: deep capture machinery, proven offline through the production writer.

The live detail endpoints are behind the browser-session gate (B7) and stay BLOCKED until a
canary proves them; what is under test here is the contract the machinery enforces: capture +
history in one transaction, staleness as a decision (version change, deadline, reconcile,
quarantined member), multiplicity that never collapses, and a refresh queue the UI can trust.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from santhau_studio.collectors.deep import (
    DeepLaneSpec,
    DetailShapeError,
    detail_spec,
    hsmt_spec,
    parse_detail,
    reoffer_spec,
    source_version_json,
)
from santhau_studio.domain.identity import canonical_bytes
from santhau_studio.storage import deep
from santhau_studio.storage.warehouse import WarehouseWriter

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)

SPEC = DeepLaneSpec(
    family="TBMT",
    lane="DETAIL",
    endpoint_kind="detail-v2",
    version_fields=("notifyVersion", "decisionVersion"),
    members_path="/contractors",
    member_role="WINNER",
    member_name_fields=("contractorName",),
    member_tax_fields=("taxCode",),
    member_share_fields=("shareRatio",),
    lots_path="/lots",
    lot_no_field="lotNo",
    lot_label_fields=("lotName",),
    goods_path="/goods",
    goods_name_fields=("goodsName",),
    goods_spec_fields=("specification",),
    goods_unit_fields=("unit",),
    goods_quantity_fields=("quantity",),
    hsmt_columns=("hsmtCol1", "hsmtCol2"),
)


def detail(version: str = "00", **extra) -> dict:
    payload = {
        "notifyNo": "TBMT-2026-0001",
        "notifyVersion": version,
        "decisionVersion": "01",
        "hsmtCol1": "1.234.567 VND",
        "hsmtCol2": ["giá gói thầu", "giá dự toán"],
    }
    payload.update(extra)
    return payload


@pytest.fixture()
def writer(tmp_path: Path):
    writer = WarehouseWriter.open(tmp_path / "data", create_schema=True, role="backfill")
    writer.register_run("run-d1", "job-d1", "DETAIL")
    yield writer
    writer.close()


def _insert_record(writer: WarehouseWriter, natural_key: str, template_id: int | None = None) -> int:
    ref = writer.store.put(canonical_bytes({"natural": natural_key}))
    writer.register_object(ref)
    cur = writer.conn.execute(
        "INSERT INTO source_records (source_key, family, natural_key, version_state, version_raw,"
        " identity_key, first_seen_at_utc, last_seen_at_utc, current_content_version,"
        " current_content_hash, current_record_object_sha, integrity_status)"
        " VALUES ('egp_home','TBMT',?,'KNOWN','00',"
        " 'id1:' || lower(hex(randomblob(32))), '2026-11-01T00:00:00Z','2026-11-01T00:00:00Z',1,"
        " 'ch1:' || lower(hex(randomblob(32))),"
        + ("(SELECT current_record_object_sha FROM source_records WHERE id=?)," if template_id else "?,")
        + " 'OK')",
        (natural_key, template_id or ref.sha256),
    )
    writer.conn.commit()
    return int(cur.lastrowid)


@pytest.fixture()
def record_id(writer) -> int:
    return _insert_record(writer, "TBMT-2026-0001")


def capture(writer, record_id: int, *, version: str = "00", completeness: str = "FULL",
            extra: dict | None = None, refresh_after_days: int | None = None) -> deep.DeepCaptureResult:
    payload = detail(version, **(extra or {}))
    return deep.record_deep_capture(
        writer, run_id="run-d1", source_record_id=record_id, spec=SPEC,
        completeness=completeness, response_payload=payload, detail=payload, now=NOW,
        refresh_after_days=refresh_after_days,
    )


# ------------------------------------------------------------------ capture + history (B6)

def test_first_capture_writes_latest_and_history(writer, record_id) -> None:
    result = capture(writer, record_id)
    assert result.capture_seq == 1 and result.completeness == "FULL"
    latest = writer.conn.execute(
        "SELECT * FROM deep_capture_latest WHERE deep_capture_id=?", (result.capture_id,)
    ).fetchone()
    assert latest["completeness"] == "FULL" and latest["captured_at_utc"] == "2026-11-03T12:00:00Z"
    assert latest["source_version_json"] == json.dumps(
        {"decisionVersion": "01", "notifyVersion": "00"}, sort_keys=True, separators=(",", ":")
    )


def test_refresh_moves_latest_and_keeps_every_history_row(writer, record_id) -> None:
    first = capture(writer, record_id, version="00")
    second = capture(writer, record_id, version="01")
    assert second.capture_seq == 2 and second.capture_id == first.capture_id
    history = writer.conn.execute(
        "SELECT capture_seq, source_version_json FROM deep_capture_history"
        " WHERE deep_capture_id=? ORDER BY capture_seq",
        (first.capture_id,),
    ).fetchall()
    assert [row["capture_seq"] for row in history] == [1, 2]
    assert history[0]["source_version_json"] != history[1]["source_version_json"], "evidence is append-only"


def test_detail_and_response_objects_exist_behind_the_captures(writer, record_id) -> None:
    body = detail()
    result = deep.record_deep_capture(
        writer, run_id="run-d1", source_record_id=record_id, spec=SPEC,
        completeness="FULL", response_payload={"envelope": body}, detail=body, now=NOW,
    )
    assert result.response_object_sha != result.record_object_sha
    shas = (result.response_object_sha, result.record_object_sha)
    rows = writer.conn.execute(
        "SELECT sha256 FROM raw_objects WHERE sha256 IN (?, ?)", shas
    ).fetchall()
    assert len(rows) == 2, "FAULT-16: a capture cites objects that must exist as rows"


# ------------------------------------------------------------------ staleness decisions

def test_version_change_stales_the_capture_and_refresh_clears_it(writer, record_id) -> None:
    result = capture(writer, record_id, version="00")
    observed = json.dumps({"decisionVersion": "01", "notifyVersion": "02"}, sort_keys=True, separators=(",", ":"))
    changed = deep.mark_stale_on_version_change(writer.conn, source_record_id=record_id, observed_version_json=observed)
    assert changed == 1
    row = writer.conn.execute(
        "SELECT stale, stale_reason, lane_status FROM deep_captures WHERE id=?", (result.capture_id,)
    ).fetchone()
    assert row["stale"] == 1 and row["stale_reason"] == "SOURCE_VERSION_CHANGED" and row["lane_status"] == "STALE"
    capture(writer, record_id, version="02")
    row = writer.conn.execute(
        "SELECT stale, stale_reason, latest_capture_seq FROM deep_captures WHERE id=?", (result.capture_id,)
    ).fetchone()
    assert row["stale"] == 0 and row["stale_reason"] is None and row["latest_capture_seq"] == 2


def test_unchanged_version_does_not_stale(writer, record_id) -> None:
    capture(writer, record_id, version="00")
    observed = source_version_json(SPEC, detail(version="00"))
    assert deep.mark_stale_on_version_change(writer.conn, source_record_id=record_id, observed_version_json=observed) == 0


def test_refresh_interval_marks_stale_and_refresh_clears(writer, record_id) -> None:
    capture(writer, record_id, refresh_after_days=1)
    later = NOW + timedelta(days=2)
    assert deep.apply_refresh_interval(writer.conn, now=later) == 1
    row = writer.conn.execute("SELECT stale, stale_reason FROM deep_captures").fetchone()
    assert row["stale"] == 1 and row["stale_reason"] == "REFRESH_INTERVAL_ELAPSED"
    later_result = deep.record_deep_capture(
        writer, run_id="run-d1", source_record_id=record_id, spec=SPEC,
        completeness="FULL", response_payload=detail(), detail=detail(), now=later,
    )
    assert later_result.stale == 0
    row = writer.conn.execute("SELECT stale, stale_reason FROM deep_captures").fetchone()
    assert row["stale"] == 0 and row["stale_reason"] is None


def test_reconcile_mismatch_marks_every_lane_of_the_record(writer, record_id) -> None:
    capture(writer, record_id)
    deep.record_deep_capture(
        writer, run_id="run-d1", source_record_id=record_id,
        spec=DeepLaneSpec(family="TBMT", lane="HSMT", endpoint_kind="hsmt-v2", version_fields=("notifyVersion",)),
        completeness="FULL", response_payload=detail(), detail=detail(), now=NOW,
    )
    assert deep.mark_reconcile_mismatch(writer.conn, source_record_id=record_id) == 2
    reasons = {row["stale_reason"] for row in writer.conn.execute("SELECT stale_reason FROM deep_captures").fetchall()}
    assert reasons == {"RECONCILE_MISMATCH"}


def test_quarantined_member_stales_without_dropping_the_parsed_rest(writer, record_id) -> None:
    payload = detail(contractors=[
        {"contractorName": "Công ty A", "taxCode": "0301", "shareRatio": "60"},
        "not-an-object",
    ])
    result = deep.record_deep_capture(
        writer, run_id="run-d1", source_record_id=record_id, spec=SPEC,
        completeness="FULL", response_payload=payload, detail=payload, now=NOW,
    )
    assert result.stale == 1 and result.stale_reason == "QUARANTINED_MEMBER"
    members = writer.conn.execute("SELECT party_name, ranking FROM contractor_memberships").fetchall()
    assert [row["party_name"] for row in members] == ["Công ty A"], "the damaged member is reported, not guessed"
    assert members[0]["ranking"] == 1


def test_shape_error_writes_nothing_at_all(writer, record_id) -> None:
    with pytest.raises(DetailShapeError):
        deep.record_deep_capture(
            writer, run_id="run-d1", source_record_id=record_id, spec=SPEC,
            completeness="FULL", response_payload=[1, 2], detail=[1, 2], now=NOW,
        )
    assert writer.conn.execute("SELECT COUNT(*) FROM deep_captures").fetchone()[0] == 0
    assert writer.conn.execute("SELECT COUNT(*) FROM deep_capture_history").fetchone()[0] == 0


# ------------------------------------------------------------------ multiplicity (§10.4)

def members_payload() -> dict:
    return detail(contractors=[
        {"contractorName": "Công ty A", "taxCode": "0301234567", "shareRatio": "60"},
        {"contractorName": "Công ty B", "shareRatio": ["40", "40%"]},
        {"contractorName": "Liên danh C"},
    ], lots=[{
        "lotNo": "LOT-1", "lotName": "Gói thầu số 1",
        "goods": [
            {"goodsName": "Máy photocopy A3", "specification": "Model X", "unit": "Cái", "quantity": "10"},
            {"goodsName": "Máy in A4", "unit": "Bộ"},
            {"goodsName": "Máy scan", "quantity": "khoảng 5"},
        ],
    }])


def test_members_keep_multiplicity_and_presence(writer, record_id) -> None:
    deep.record_deep_capture(
        writer, run_id="run-d1", source_record_id=record_id, spec=SPEC,
        completeness="FULL", response_payload=members_payload(), detail=members_payload(), now=NOW,
    )
    rows = writer.conn.execute(
        "SELECT party_name, party_tax_id_presence, party_tax_id, membership_share_presence,"
        " membership_share, ranking FROM contractor_memberships ORDER BY ranking"
    ).fetchall()
    assert [row["party_name"] for row in rows] == ["Công ty A", "Công ty B", "Liên danh C"]
    assert rows[0]["party_tax_id_presence"] == "KNOWN" and rows[0]["party_tax_id"] == "0301234567"
    assert rows[1]["membership_share_presence"] == "UNPARSABLE" and rows[1]["membership_share"] is None, \
        "an ambiguous share stays unparsed, never element zero"
    assert rows[2]["party_tax_id_presence"] == "MISSING" and rows[2]["party_tax_id"] is None


def test_lots_and_goods_rows_preserve_quantity_semantics(writer, record_id) -> None:
    deep.record_deep_capture(
        writer, run_id="run-d1", source_record_id=record_id, spec=SPEC,
        completeness="FULL", response_payload=members_payload(), detail=members_payload(), now=NOW,
    )
    lot = writer.conn.execute("SELECT lot_no_raw, lot_label, goods_count FROM lots").fetchone()
    assert lot["lot_no_raw"] == "LOT-1" and lot["lot_label"] == "Gói thầu số 1" and lot["goods_count"] == 3
    rows = writer.conn.execute(
        "SELECT name, quantity_presence, quantity, quantity_raw FROM goods_rows ORDER BY row_ordinal"
    ).fetchall()
    assert rows[0]["quantity_presence"] == "KNOWN" and rows[0]["quantity"] == 10.0
    assert rows[1]["quantity_presence"] == "MISSING" and rows[1]["quantity"] is None
    assert rows[2]["quantity_presence"] == "UNPARSABLE" and rows[2]["quantity_raw"] == "khoảng 5"


def test_hsmt_columns_are_kept_verbatim_not_cropped(writer, record_id) -> None:
    deep.record_deep_capture(
        writer, run_id="run-d1", source_record_id=record_id, spec=SPEC,
        completeness="FULL", response_payload=detail(), detail=detail(), now=NOW,
    )
    parsed = parse_detail(SPEC, detail())
    assert parsed.hsmt["hsmtCol1"] == "1.234.567 VND", "multi-currency strings are never coerced"
    assert parsed.hsmt["hsmtCol2"] == ["giá gói thầu", "giá dự toán"], "a multi-value column stays a list"


def test_multi_valued_version_field_is_kept_verbatim(writer, record_id) -> None:
    payload = detail(decisionVersion=["01", "02"])
    deep.record_deep_capture(
        writer, run_id="run-d1", source_record_id=record_id, spec=SPEC,
        completeness="FULL", response_payload=payload, detail=payload, now=NOW,
    )
    stored = writer.conn.execute("SELECT source_version_json FROM deep_captures").fetchone()[0]
    assert json.loads(stored)["decisionVersion"] == ["01", "02"], "never pick the first element"


# ------------------------------------------------------------------ read models (UI-08)

def test_done_ids_report_capture_time_and_refresh_due(writer, record_id) -> None:
    capture(writer, record_id)
    state = deep.done_ids(writer.conn, [record_id], lane="DETAIL", endpoint_kind="detail-v2")[record_id]
    assert state["captured_at_utc"] == "2026-11-03T12:00:00Z"
    assert state["refresh_due_at_utc"] == "2026-12-03T12:00:00Z"
    assert state["completeness"] == "FULL" and not state["stale"]
    assert deep.done_ids(writer.conn, [], lane="DETAIL", endpoint_kind="detail-v2") == {}


def test_refresh_queue_lists_stale_before_incomplete(writer, record_id) -> None:
    fresh = capture(writer, record_id, version="00")
    partial_record = _insert_record(writer, "TBMT-2026-0002", template_id=record_id)
    capture(writer, partial_record, version="00", completeness="PARTIAL")
    deep.mark_stale(writer.conn, fresh.capture_id, "RECONCILE_MISMATCH")
    queue = deep.refresh_queue(writer.conn, now=NOW + timedelta(days=1))
    assert [row["source_record_id"] for row in queue] == [record_id, partial_record], "stale first, then incomplete"
    assert queue[0]["stale_reason"] == "RECONCILE_MISMATCH"
    assert queue[1]["completeness"] == "PARTIAL"


def test_session_gated_lanes_are_declared_blocked_until_canaried() -> None:
    """B7: built-in detail/hsmt/reoffer lanes refuse to run without a proven session."""
    for spec in (detail_spec("TBMT"), hsmt_spec("TBMT"), reoffer_spec("TBMT")):
        assert spec.requires_session is True
        assert spec.lots_path is None and spec.members_path is None, "paths unlock only after the canary (D-3)"


def test_recapture_of_the_same_payload_dedups_objects(writer, record_id) -> None:
    first = capture(writer, record_id)
    second = capture(writer, record_id)
    assert first.response_object_sha == second.response_object_sha
    count = writer.conn.execute(
        "SELECT COUNT(*) FROM raw_objects WHERE sha256=?", (first.response_object_sha,)
    ).fetchone()[0]
    assert count == 1, "the object store dedups; history cites the same bytes twice"
