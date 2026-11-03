"""Canary ladder and access policy (RC2-A).

The invariant under test: a probe is reachability evidence, never collection proof. A COMPLETE
interval with a proof can only come from `Runner`, and the ladder must reflect that ordering.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from fake_egp_portal import FakePortal

from santhau_studio.collectors.canary import (
    COLLECTION_PROVEN,
    PROBE_UNVERIFIED,
    RULE_VERIFIED,
    access_policy,
    canary_state,
    probe_family,
)
from santhau_studio.collectors.lane_source import egp_page_source
from santhau_studio.collectors.source_registry import FAMILY_ORDER, SOURCE_EGP_HOME, SOURCES
from santhau_studio.domain.coverage import vn_calendar_day
from santhau_studio.storage.warehouse import WarehouseWriter
from santhau_studio.workers.jobs import JobRequest, WorkerLoop, enqueue_job
from santhau_studio.workers.runner import Runner

REPO = Path(__file__).resolve().parents[2]
ENDPOINT = SOURCES[SOURCE_EGP_HOME].endpoint
ADAPTER_SHA = hashlib.sha256(b"canary-state").hexdigest()
DAY = vn_calendar_day(date(2026, 11, 1))


def seed_rule(writer: WarehouseWriter, family: str, *, verified: int = 1) -> None:
    writer.conn.execute(
        "INSERT OR IGNORE INTO filter_boundary_rules (rule_key, rule_version, source_key, family, endpoint,"
        " adapter_sha, boundary_kind, precision, source_timezone, offset_minutes, conversion_params,"
        " canary_verified, verified_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"canary-{family}", 1, SOURCE_EGP_HOME, family, ENDPOINT, ADAPTER_SHA,
         "CLOSED_LOCAL", "DAY", "Asia/Ho_Chi_Minh", 420, '{"to_minus":"1s"}', verified,
         "2026-10-31T00:00:00Z"),
    )
    writer.conn.commit()


def run_day(writer: WarehouseWriter, family: str, portal: FakePortal) -> None:
    writer.register_run(f"run-{family}", f"job-{family}", "BACKFILL")
    runner = Runner(
        writer=writer, source_key=SOURCE_EGP_HOME, family=family, endpoint=ENDPOINT,
        adapter_sha=ADAPTER_SHA,
        page_source=egp_page_source(family=family, lane_factory=portal.lane),
    )
    assert runner.backfill(f"run-{family}", DAY)[0].status == "COMPLETE"


@pytest.fixture(autouse=True)
def _fresh_pacing():
    from santhau_studio.collectors.network_policy import HostRateLimiter

    HostRateLimiter.reset_registry()
    yield
    HostRateLimiter.reset_registry()


def test_probe_never_creates_proof_and_never_completes(tmp_path: Path) -> None:
    writer = WarehouseWriter.open(tmp_path / "data", create_schema=True)
    try:
        assert canary_state(writer.conn, SOURCE_EGP_HOME, "TBMT", ADAPTER_SHA)["state"] == PROBE_UNVERIFIED
        result = probe_family(writer, family="TBMT", adapter_sha=ADAPTER_SHA,
                              lane_factory=lambda _family: FakePortal().lane())
        assert result["ok"] is True
        # Evidence exists, but no interval and no proof: the rung is still PROBE_UNVERIFIED
        # because no boundary rule is verified yet.
        assert canary_state(writer.conn, SOURCE_EGP_HOME, "TBMT", ADAPTER_SHA)["state"] == PROBE_UNVERIFIED
        assert writer.conn.execute("SELECT COUNT(*) FROM coverage_intervals").fetchone()[0] == 0
        assert writer.conn.execute("SELECT COUNT(*) FROM coverage_proofs").fetchone()[0] == 0
        health = writer.conn.execute(
            "SELECT last_canary_ok_at_utc, consecutive_failures FROM adapter_health"
            " WHERE source_key=? AND family=? AND adapter_sha=?", (SOURCE_EGP_HOME, "TBMT", ADAPTER_SHA)
        ).fetchone()
        assert health["last_canary_ok_at_utc"] and health["consecutive_failures"] == 0
        marker = writer.conn.execute(
            "SELECT value_json FROM settings WHERE key=?", (f"canary:{SOURCE_EGP_HOME}:TBMT",)
        ).fetchone()
        assert marker is not None and '"shape_ok": true' in marker["value_json"]
    finally:
        writer.close()


def test_rule_verified_requires_probe_then_rule(tmp_path: Path) -> None:
    writer = WarehouseWriter.open(tmp_path / "data", create_schema=True)
    try:
        seed_rule(writer, "TBMT", verified=1)
        # A verified rule alone is not enough: no probe has proven the lane is reachable.
        assert canary_state(writer.conn, SOURCE_EGP_HOME, "TBMT", ADAPTER_SHA)["state"] == PROBE_UNVERIFIED
        probe_family(writer, family="TBMT", adapter_sha=ADAPTER_SHA,
                     lane_factory=lambda _family: FakePortal().lane())
        assert canary_state(writer.conn, SOURCE_EGP_HOME, "TBMT", ADAPTER_SHA)["state"] == RULE_VERIFIED
    finally:
        writer.close()


def test_collection_proven_only_after_a_completed_run(tmp_path: Path) -> None:
    writer = WarehouseWriter.open(tmp_path / "data", create_schema=True)
    try:
        seed_rule(writer, "TBMT", verified=1)
        probe_family(writer, family="TBMT", adapter_sha=ADAPTER_SHA,
                     lane_factory=lambda _family: FakePortal().lane())
        assert canary_state(writer.conn, SOURCE_EGP_HOME, "TBMT", ADAPTER_SHA)["state"] == RULE_VERIFIED
        run_day(writer, "TBMT", FakePortal())
        state = canary_state(writer.conn, SOURCE_EGP_HOME, "TBMT", ADAPTER_SHA)
        assert state["state"] == COLLECTION_PROVEN
    finally:
        writer.close()


def test_unverified_rule_blocks_probe_from_promotion(tmp_path: Path) -> None:
    writer = WarehouseWriter.open(tmp_path / "data", create_schema=True)
    try:
        seed_rule(writer, "TBMT", verified=0)
        probe_family(writer, family="TBMT", adapter_sha=ADAPTER_SHA,
                     lane_factory=lambda _family: FakePortal().lane())
        assert canary_state(writer.conn, SOURCE_EGP_HOME, "TBMT", ADAPTER_SHA)["state"] == PROBE_UNVERIFIED
    finally:
        writer.close()


def test_probe_refusal_is_recorded_as_access_event(tmp_path: Path) -> None:
    writer = WarehouseWriter.open(tmp_path / "data", create_schema=True)
    try:
        portal = FakePortal(faults={"TBMT": ["403"]})
        result = probe_family(writer, family="TBMT", adapter_sha=ADAPTER_SHA,
                              lane_factory=lambda _family: portal.lane())
        assert result["ok"] is False
        assert canary_state(writer.conn, SOURCE_EGP_HOME, "TBMT", ADAPTER_SHA)["state"] == PROBE_UNVERIFIED
        event = writer.conn.execute(
            "SELECT to_state, reason_kind FROM source_access_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert event["to_state"] == "BLOCKED" and event["reason_kind"] == "HTTP_403"
        health = writer.conn.execute(
            "SELECT consecutive_failures FROM adapter_health WHERE family='TBMT'"
        ).fetchone()
        assert health["consecutive_failures"] == 1
    finally:
        writer.close()


def test_access_policy_matrix() -> None:
    assert access_policy(SOURCE_EGP_HOME) == "PUBLIC"
    assert access_policy("egp_v2") == "SESSION"
    assert access_policy("moh_quotes") == "DISABLED"
    assert access_policy("mof_quotes") == "DISABLED"
    assert access_policy("nope") == "DISABLED"


def _job(family: str, source_key: str, job_id: str) -> JobRequest:
    return JobRequest(
        job_id=job_id, job_kind="BACKFILL", requested_by="test",
        sources=({"source_key": source_key, "family": family, "endpoint": ENDPOINT,
                  "adapter_sha": ADAPTER_SHA},),
        scope={"from": DAY.from_utc.isoformat().replace("+00:00", "Z"),
               "to": DAY.to_utc.isoformat().replace("+00:00", "Z")},
        created_at="2026-11-03T00:00:00Z",
    )


def _run_once(root: Path, request: JobRequest) -> dict:
    loop = WorkerLoop(root, page_source_builder=lambda _w, _r: egp_page_source(
        family=str(request.sources[0]["family"]), lane_factory=FakePortal().lane), idle_exit_seconds=0.3)
    enqueue_job(root, request)
    import threading

    thread = threading.Thread(target=loop.run, daemon=True)
    thread.start()
    thread.join(timeout=30)
    from santhau_studio.storage.warehouse import connect_read_only

    conn = connect_read_only(root / "warehouse.sqlite")
    try:
        return dict(conn.execute(
            "SELECT lifecycle, status FROM runs WHERE job_id=?", (request.job_id,)
        ).fetchone())
    finally:
        conn.close()


def test_worker_refuses_session_and_disabled_sources(tmp_path: Path) -> None:
    root = tmp_path / "data"
    writer = WarehouseWriter.open(root, create_schema=True)
    seed_rule(writer, "TBMT")
    writer.close()
    session = _run_once(root, _job("TBMT", "egp_v2", "job-session"))
    assert session["status"] == "BLOCKED"
    from santhau_studio.storage.warehouse import connect_read_only

    conn = connect_read_only(root / "warehouse.sqlite")
    try:
        detail = conn.execute(
            "SELECT detail_json FROM run_tasks WHERE run_id IN"
            " (SELECT run_id FROM runs WHERE job_id='job-session')"
        ).fetchone()["detail_json"]
    finally:
        conn.close()
    assert "SOURCE_ACCESS_SESSION" in detail

    disabled = _run_once(root, _job("TBMT", "moh_quotes", "job-disabled"))
    assert disabled["status"] == "BLOCKED"

    mismatched = _run_once(root, _job("TBMT", "egp_home", "job-ok"))
    assert mismatched["status"] == "COMPLETED"


def test_canary_cli_refuses_without_approval(tmp_path: Path) -> None:
    for command in (
        ["probe", "--family", "TBMT", "--data-root", str(tmp_path / "d")],
        ["collect", "--family", "TBMT", "--data-root", str(tmp_path / "d")],
    ):
        result = subprocess.run(
            [sys.executable, str(REPO / "tools" / "canary.py"), *command],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "SANTHAU_CANARY_APPROVED": "", "PYTHONPATH": str(REPO / "src")},
            cwd=str(REPO),
        )
        assert result.returncode == 2, result.stderr
        assert "REFUSED" in result.stderr


def test_canary_plan_lists_every_family() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO / "tools" / "canary.py"), "plan", "--day", "2026-11-01"],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": str(REPO / "src")}, cwd=str(REPO),
    )
    assert result.returncode == 0
    for family in FAMILY_ORDER:
        assert f"## {family}" in result.stdout
