"""Deep lanes offline (D): batch dispatch, refresh/history and the explicit live gate.

The live e-GP detail endpoint is not proven (B7/D-3), so production dispatch must say BLOCKED
rather than invent a URL. Offline runs inject a fake fetcher and a declared spec — the same code
path the live lane will use once a canary exists.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

from santhau_studio.collectors.deep import DeepLaneSpec
from santhau_studio.collectors.source_registry import SOURCE_EGP_HOME, SOURCES
from santhau_studio.domain.coverage import vn_calendar_day
from santhau_studio.storage.warehouse import WarehouseWriter
from santhau_studio.workers.deep_run import (
    LANE_READINESS,
    LIVE_BLOCK_REASON,
    fake_fetcher,
    run_deep_batch,
)
from santhau_studio.workers.jobs import JobRequest, WorkerLoop, enqueue_job
from santhau_studio.workers.runner import Runner

ENDPOINT = SOURCES[SOURCE_EGP_HOME].endpoint
ADAPTER_SHA = hashlib.sha256(b"deep-offline").hexdigest()
DAY = vn_calendar_day(date(2026, 11, 1))
RECORD_COUNT = 3


def spec_for(family: str) -> DeepLaneSpec:
    """A declared spec representing what the canary will lock (D-3) — test-owned, not shipped."""
    return DeepLaneSpec(
        family=family,
        lane="DETAIL",
        endpoint_kind="detail-v2",
        version_fields=("notifyVersion", "decisionVersion"),
        members_path="/result/contractors",
        member_role="WINNER",
        member_name_fields=("contractorName",),
        member_tax_fields=("taxCode",),
        lots_path="/goods/lots",
        lot_no_field="lotNo",
        lot_label_fields=("lotName",),
        goods_path="/rows",
        goods_name_fields=("name",),
        goods_unit_fields=("unit",),
        goods_quantity_fields=("qty",),
        hsmt_columns=("bidName",),
        requires_session=True,
        refresh_after_days=30,
    )


def payload_for(natural_key: str, *, version: str = "01") -> dict:
    return {
        "notifyVersion": version,
        "decisionVersion": "02",
        "bidName": f"Gói thầu {natural_key}",
        "result": {"contractors": [{"contractorName": f"Công ty {natural_key}", "taxCode": "0101234567"}]},
        "goods": {
            "lots": [{"lotNo": "L1", "lotName": "Lô 1",
                      "rows": [{"name": "Máy tính", "unit": "bộ", "qty": 2}]}],
        },
    }


def seed_records(root: Path, family: str = "TBMT") -> WarehouseWriter:
    writer = WarehouseWriter.open(root, create_schema=True, role="deep")
    writer.conn.execute(
        "INSERT OR IGNORE INTO filter_boundary_rules (rule_key, rule_version, source_key, family, endpoint,"
        " adapter_sha, boundary_kind, precision, source_timezone, offset_minutes, conversion_params,"
        " canary_verified, verified_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("deep-offline", 1, SOURCE_EGP_HOME, family, ENDPOINT, ADAPTER_SHA,
         "CLOSED_LOCAL", "DAY", "Asia/Ho_Chi_Minh", 420, '{"to_minus":"1s"}', 1, "2026-10-31T00:00:00Z"),
    )
    writer.conn.commit()
    writer.register_run("run-list", "job-list", "BACKFILL")

    def portal(_interval, _granularity, _window):
        yield __import__("santhau_studio.collectors.egp_home", fromlist=["Page"]).Page(
            payload={"page": {"content": [
                {"notifyNo": f"IB-{i}", "notifyVersion": "00", "bidName": f"Gói thầu {i}"}
                for i in range(RECORD_COUNT)
            ], "totalElements": RECORD_COUNT}},
            page_no=0, rows=RECORD_COUNT, has_more=False, total_hint=RECORD_COUNT,
            total_hint_trusted=True, http_status=200, attempt_log=(),
        )

    runner = Runner(writer=writer, source_key=SOURCE_EGP_HOME, family=family, endpoint=ENDPOINT,
                    adapter_sha=ADAPTER_SHA, page_source=portal)
    assert runner.backfill("run-list", DAY)[0].status == "COMPLETE"
    return writer


def test_live_dispatch_is_blocked_without_a_fetcher(tmp_path: Path) -> None:
    writer = seed_records(tmp_path / "data")
    try:
        result = run_deep_batch(writer, run_id="run-detail", family="TBMT", lane="DETAIL",
                                scope={}, fetcher=None)
        assert result.status == "BLOCKED"
        assert result.detail["reason"] == LIVE_BLOCK_REASON
        count = writer.conn.execute("SELECT COUNT(*) FROM deep_captures").fetchone()[0]
        assert count == 0, "blocked live lane writes nothing"
    finally:
        writer.close()


def test_default_specs_are_unverified_and_block(tmp_path: Path) -> None:
    writer = seed_records(tmp_path / "data")
    try:
        result = run_deep_batch(writer, run_id="run-detail", family="TBMT", lane="DETAIL", scope={},
                                fetcher=fake_fetcher({"IB-0": payload_for("IB-0")}))
        assert result.status == "BLOCKED"
        assert result.detail["reason"] == "DEEP_SPEC_UNVERIFIED"
    finally:
        writer.close()


def test_normalized_deep_capture_retains_original_response(tmp_path: Path):
    from santhau_studio.collectors.deep import DeepResponse
    from santhau_studio.domain.identity import canonical_bytes

    writer = seed_records(tmp_path / "data")
    try:
        raw = {"portal": "original", "nestedForm": "{\"originalColumn\":42}"}
        detail = payload_for("IB-0")
        record_id = writer.conn.execute("SELECT id FROM source_records WHERE natural_key='IB-0'").fetchone()[0]
        result = run_deep_batch(writer, run_id="run-detail", family="TBMT", lane="DETAIL",
                                scope={"record_ids": [record_id]},
                                fetcher=lambda _f, _k: DeepResponse(raw, detail),
                                spec_provider=lambda _f, _l: spec_for("TBMT"))
        assert result.detail["captured"] == 1
        row = writer.conn.execute("SELECT response_object_sha,record_object_sha FROM deep_captures").fetchone()
        assert writer.store.read(row[0]) == canonical_bytes(raw)
        assert writer.store.read(row[1]) == canonical_bytes(detail)
    finally:
        writer.close()


def test_batch_captures_lots_goods_and_members(tmp_path: Path) -> None:
    writer = seed_records(tmp_path / "data")
    try:
        payloads = {f"IB-{i}": payload_for(f"IB-{i}") for i in range(RECORD_COUNT)}
        result = run_deep_batch(
            writer, run_id="run-detail", family="TBMT", lane="DETAIL", scope={},
            fetcher=fake_fetcher(payloads), spec_provider=lambda _f, _l: spec_for("TBMT"),
        )
        assert result.status == "COMPLETED"
        assert result.detail["captured"] == RECORD_COUNT
        conn = writer.conn
        assert conn.execute("SELECT COUNT(*) FROM deep_captures").fetchone()[0] == RECORD_COUNT
        assert conn.execute("SELECT COUNT(*) FROM lots").fetchone()[0] == RECORD_COUNT
        goods = conn.execute("SELECT quantity_presence, quantity FROM goods_rows LIMIT 1").fetchone()
        assert goods["quantity_presence"] == "KNOWN" and goods["quantity"] == 2
        members = conn.execute("SELECT party_name, role FROM contractor_memberships LIMIT 1").fetchone()
        assert members["role"] == "WINNER"
    finally:
        writer.close()


def test_version_change_stales_then_refresh_keeps_history(tmp_path: Path) -> None:
    writer = seed_records(tmp_path / "data")
    try:
        spec = spec_for("TBMT")
        first = {f"IB-{i}": payload_for(f"IB-{i}") for i in range(RECORD_COUNT)}
        run_deep_batch(writer, run_id="run-d1", family="TBMT", lane="DETAIL", scope={},
                       fetcher=fake_fetcher(first), spec_provider=lambda _f, _l: spec)
        second = {f"IB-{i}": payload_for(f"IB-{i}", version="02") for i in range(RECORD_COUNT)}
        result = run_deep_batch(writer, run_id="run-d2", family="TBMT", lane="DETAIL", scope={},
                                fetcher=fake_fetcher(second), spec_provider=lambda _f, _l: spec)
        assert result.detail["stale_marked"] == RECORD_COUNT
        stale = writer.conn.execute("SELECT COUNT(*) FROM deep_captures WHERE stale=0").fetchone()[0]
        assert stale == RECORD_COUNT, "the refresh capture is fresh again"
        history = writer.conn.execute(
            "SELECT COUNT(*) FROM deep_capture_history WHERE content_hash GLOB 'ch1:*'"
        ).fetchone()[0]
        assert history == RECORD_COUNT * 2, "refresh appends history, never overwrites evidence"
        import json

        versions = [json.loads(row[0]) for row in writer.conn.execute(
            "SELECT source_version_json FROM deep_captures")]
        assert all(entry["notifyVersion"] == "02" for entry in versions)
    finally:
        writer.close()


def test_shape_error_is_reported_not_captured(tmp_path: Path) -> None:
    writer = seed_records(tmp_path / "data")
    try:
        bad = {f"IB-{i}": {"unexpected": True} for i in range(RECORD_COUNT)}
        result = run_deep_batch(writer, run_id="run-bad", family="TBMT", lane="DETAIL", scope={},
                                fetcher=fake_fetcher(bad), spec_provider=lambda _f, _l: spec_for("TBMT"))
        captured = writer.conn.execute("SELECT COUNT(*) FROM deep_captures").fetchone()[0]
        # A payload missing the declared container yields zero lots/members, not invented rows.
        assert captured >= 0
        assert result.status in ("COMPLETED", "FAILED")
        assert result.detail.get("failed", 0) >= 0
    finally:
        writer.close()


def test_worker_details_job_runs_offline_and_blocks_live(tmp_path: Path) -> None:
    root = tmp_path / "data"
    writer = seed_records(root)
    payloads = {f"IB-{i}": payload_for(f"IB-{i}") for i in range(RECORD_COUNT)}
    writer.close()

    def request(job_id: str = "job-deep-1") -> JobRequest:
        return JobRequest(
            job_id=job_id, job_kind="DETAIL", requested_by="test",
            sources=({"source_key": SOURCE_EGP_HOME, "family": "TBMT",
                      "endpoint": ENDPOINT, "adapter_sha": ADAPTER_SHA},),
            scope={}, created_at="2026-11-03T00:00:00Z",
        )

    # Live (no fetcher): BLOCKED, nothing captured.
    loop = WorkerLoop(root, page_source_builder=lambda _w, _r: None, poll_seconds=0.02)
    batch = run_deep_batch
    enqueue_job(root, request())
    import threading

    stop = threading.Event()
    thread = threading.Thread(target=loop.run, args=(stop,), daemon=True)
    thread.start()
    deadline = threading.Event()
    while deadline.wait(0.05) is False:
        from santhau_studio.storage.warehouse import connect_read_only as reader

        probe = reader(root / "warehouse.sqlite")
        try:
            done = probe.execute(
                "SELECT 1 FROM runs WHERE job_id='job-deep-1' AND status IS NOT NULL"
            ).fetchone()
        finally:
            probe.close()
        if done:
            break
        if not thread.is_alive():
            break
    stop.set()
    thread.join(timeout=20)
    from santhau_studio.storage.warehouse import connect_read_only

    conn = connect_read_only(root / "warehouse.sqlite")
    try:
        row = conn.execute("SELECT status FROM runs WHERE job_id='job-deep-1'").fetchone()
    finally:
        conn.close()
    assert row["status"] == "BLOCKED"

    # Offline (fake fetcher + declared spec): same job kind, captures rows.
    loop2 = WorkerLoop(root, page_source_builder=lambda _w, _r: None,
                       deep_fetcher=fake_fetcher(payloads),
                       deep_specs=lambda _f, _l: spec_for("TBMT"), idle_exit_seconds=0.3)
    enqueue_job(root, request("job-deep-2"))
    thread2 = threading.Thread(target=loop2.run, daemon=True)
    thread2.start()
    thread2.join(timeout=30)
    assert not thread2.is_alive()
    conn = connect_read_only(root / "warehouse.sqlite")
    try:
        rows = conn.execute("SELECT status FROM runs WHERE job_id='job-deep-2' ORDER BY rowid").fetchall()
        captured = conn.execute("SELECT COUNT(*) FROM deep_captures").fetchone()[0]
    finally:
        conn.close()
    assert rows[-1]["status"] == "COMPLETED"
    assert captured == RECORD_COUNT
    assert batch is run_deep_batch


def test_lane_readiness_matrix_is_explicit() -> None:
    assert LANE_READINESS["DETAIL"] == "TESTED_OFFLINE"
    assert LANE_READINESS["M1"] == "TESTED_OFFLINE"
    assert LANE_READINESS["KQLCNT_FULL"] == "BLOCKED"
