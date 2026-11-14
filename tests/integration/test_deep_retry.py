"""Escalating deep retry: back off, quarantine, and release on a real fix.

Covers P1-2: a permanently failing deep task must not be re-fetched every
hour, a transient failure must still recover, and a quarantined task must
come back when the parser/adapter changes, the listing changes, or an
operator releases it by hand. No network is used: failures are driven
through the real collect path with a fetcher that raises, or through
``task_state`` directly.
"""

import json
from datetime import UTC, datetime, timedelta

from test_auto_deep import make_eligible
from test_deep_run import seed_records
from test_public_deep_refresh import capture, payload

from santhau_studio.collectors.cgttrg import public_spec
from santhau_studio.collectors.http_client import FetchFailed
from santhau_studio.linux.deep import deep_status, reset_deep_retry
from santhau_studio.storage.deep_metadata import write_task
from santhau_studio.storage.public_deep import task_state
from santhau_studio.workers.auto_deep import _lane_groups, candidates, configure, run_batch
from santhau_studio.workers.deep_run import run_deep_batch
from santhau_studio.workers.jobs import WorkerLoop


def retry_state(writer, record_id, lane):
    row = writer.conn.execute(
        "SELECT provenance_json FROM deep_tasks WHERE source_record_id=? AND lane=?",
        (record_id, lane),
    ).fetchone()
    return (json.loads(row[0]) or {}).get("deep_retry") or {}


def is_selected(conn, record_id, lane):
    return any(
        r["id"] == record_id and r["lane"] == lane for r in candidates(conn, 30)
    )


def backdate(writer, record_id, lane, minutes):
    stamp = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
    writer.conn.execute(
        "UPDATE deep_tasks SET updated_at_utc=? WHERE source_record_id=? AND lane=?",
        (stamp, record_id, lane),
    )


def fail(writer, record_id, lane, kind, error="boom"):
    task_state(writer.conn, record_id, lane, "FAILED", error=error, failure_kind=kind)


def test_fetch_failure_backs_off_then_quarantines(tmp_path):
    writer = seed_records(tmp_path)
    try:
        make_eligible(writer)
        configure(writer.conn, {"enabled": True, "batch_requests": 30})
        rid = 1
        # 1st failure: 60-minute cooldown.
        fail(writer, rid, "HSMT", "FETCH", "response is not JSON")
        assert retry_state(writer, rid, "HSMT") == {
            "consecutive_failures": 1, "failure_kind": "FETCH", "quarantined": False,
        }
        assert not is_selected(writer.conn, rid, "HSMT")
        backdate(writer, rid, "HSMT", 61)
        assert is_selected(writer.conn, rid, "HSMT")
        # 2nd failure: 6-hour cooldown.
        fail(writer, rid, "HSMT", "FETCH", "response is not JSON")
        assert retry_state(writer, rid, "HSMT")["consecutive_failures"] == 2
        backdate(writer, rid, "HSMT", 61)
        assert not is_selected(writer.conn, rid, "HSMT")
        backdate(writer, rid, "HSMT", 361)
        assert is_selected(writer.conn, rid, "HSMT")
        # 3rd failure: 24-hour cooldown.
        fail(writer, rid, "HSMT", "FETCH", "response is not JSON")
        backdate(writer, rid, "HSMT", 361)
        assert not is_selected(writer.conn, rid, "HSMT")
        backdate(writer, rid, "HSMT", 1441)
        assert is_selected(writer.conn, rid, "HSMT")
        # 4th failure: 72-hour cooldown.
        fail(writer, rid, "HSMT", "FETCH", "response is not JSON")
        backdate(writer, rid, "HSMT", 1441)
        assert not is_selected(writer.conn, rid, "HSMT")
        backdate(writer, rid, "HSMT", 4321)
        assert is_selected(writer.conn, rid, "HSMT")
        # 5th failure: quarantined -- not selected even a month later.
        fail(writer, rid, "HSMT", "FETCH", "response is not JSON")
        state = retry_state(writer, rid, "HSMT")
        assert state["consecutive_failures"] == 5 and state["quarantined"] is True
        assert state["quarantined_at_utc"]
        backdate(writer, rid, "HSMT", 60 * 24 * 30)
        assert not is_selected(writer.conn, rid, "HSMT")
        assert deep_status(tmp_path)["quarantined"] == {"packages": 1, "lane_tasks": 1}
    finally:
        writer.close()


def test_transient_failures_never_quarantine_and_success_resets(tmp_path):
    writer = seed_records(tmp_path)
    try:
        make_eligible(writer)
        configure(writer.conn, {"enabled": True, "batch_requests": 30})
        rid = 1
        for _ in range(5):
            fail(writer, rid, "HSMT", "TRANSIENT", "timeout")
            backdate(writer, rid, "HSMT", 4321)
        state = retry_state(writer, rid, "HSMT")
        assert state["consecutive_failures"] == 5 and state["quarantined"] is False
        # Still eligible after the 72h cap: a transient failure can recover.
        assert is_selected(writer.conn, rid, "HSMT")
        # A success resets the series, so the next failure starts at 1 again.
        task_state(writer.conn, rid, "HSMT", "FETCHED")
        assert retry_state(writer, rid, "HSMT") == {
            "consecutive_failures": 0, "failure_kind": None, "quarantined": False,
        }
        fail(writer, rid, "HSMT", "TRANSIENT", "timeout")
        assert retry_state(writer, rid, "HSMT")["consecutive_failures"] == 1
    finally:
        writer.close()


def test_non_json_response_is_fetched_five_times_then_never_again(tmp_path):
    """IB2600586963 analogue: HSMT FAILED 'response is not JSON', via the real path."""
    writer = seed_records(tmp_path)
    try:
        make_eligible(writer)
        configure(writer.conn, {"enabled": True, "batch_requests": 30})
        rid = writer.conn.execute("SELECT MIN(id) FROM source_records").fetchone()[0]

        class NonJsonFetcher:
            def __init__(self):
                self.calls = 0

            def fetch_record(self, _record):
                self.calls += 1
                raise FetchFailed("egp_public_cgttrg: response is not JSON (JSONDecodeError)")

        fetcher = NonJsonFetcher()
        for wait in (61, 361, 1441, 4321):
            result = run_deep_batch(
                writer, run_id=f"non-json-{wait}", family="TBMT", lane="HSMT",
                scope={"record_ids": [rid]}, fetcher=fetcher, spec_provider=public_spec,
            )
            assert result.detail["failed"] == 1
            backdate(writer, rid, "HSMT", wait)
        # Fifth failure quarantines it; a sixth scan must not fetch again.
        result = run_deep_batch(
            writer, run_id="non-json-5", family="TBMT", lane="HSMT",
            scope={"record_ids": [rid]}, fetcher=fetcher, spec_provider=public_spec,
        )
        assert result.detail["failed"] == 1
        assert fetcher.calls == 5
        assert retry_state(writer, rid, "HSMT")["quarantined"] is True
        assert not is_selected(writer.conn, rid, "HSMT")
        backdate(writer, rid, "HSMT", 60 * 24 * 30)
        assert not is_selected(writer.conn, rid, "HSMT")
        # The failure kind recorded is FETCH (deterministic), not TRANSIENT.
        assert retry_state(writer, rid, "HSMT")["failure_kind"] == "FETCH"
    finally:
        writer.close()


def test_quarantined_unsupported_task_returns_on_adapter_bump(tmp_path, monkeypatch):
    """IB2600588380 analogue: KHAC BLOCKED on all lanes, released by a bump."""
    from santhau_studio.collectors import cgttrg, public_deep

    writer = seed_records(tmp_path)
    try:
        make_eligible(writer)
        configure(writer.conn, {"enabled": True, "batch_requests": 30})
        rid = 1
        sha = writer.conn.execute(
            "SELECT current_record_object_sha FROM source_records WHERE id=?", (rid,)
        ).fetchone()[0]
        for _ in range(5):
            task_state(
                writer.conn, rid, "HSMT", "BLOCKED",
                error="UNSUPPORTED_PROCESS_APPLY:KHAC",
                listing_sha=sha, failure_kind="UNSUPPORTED",
            )
        write_task(
            writer.conn, rid, "HSMT",
            parser_version=cgttrg.PARSER_VERSION, adapter_version=2, capability="UNSUPPORTED_PROCESS_APPLY",
        )
        assert retry_state(writer, rid, "HSMT")["quarantined"] is True
        writer.conn.execute(
            "UPDATE deep_tasks SET updated_at_utc='2000-01-01T00:00:00Z' WHERE source_record_id=?",
            (rid,),
        )
        writer.conn.execute(
            "UPDATE deep_task_metadata SET task_updated_at_utc='2000-01-01T00:00:00Z' WHERE source_record_id=?",
            (rid,),
        )
        assert not is_selected(writer.conn, rid, "HSMT")
        monkeypatch.setattr(public_deep, "ADAPTER_VERSION", public_deep.ADAPTER_VERSION + 1)
        assert is_selected(writer.conn, rid, "HSMT")
    finally:
        writer.close()


def test_quarantined_task_returns_when_listing_changes(tmp_path):
    writer = seed_records(tmp_path)
    try:
        make_eligible(writer)
        configure(writer.conn, {"enabled": True, "batch_requests": 30})
        rid = 1
        for _ in range(5):
            fail(writer, rid, "HSMT", "FETCH", "response is not JSON")
        assert retry_state(writer, rid, "HSMT")["quarantined"] is True
        assert not is_selected(writer.conn, rid, "HSMT")
        # A new listing version is new evidence: retry now, as a fresh series.
        writer.conn.execute(
            "UPDATE deep_tasks SET listing_object_sha='republished-listing' WHERE source_record_id=? AND lane=?",
            (rid, "HSMT"),
        )
        assert is_selected(writer.conn, rid, "HSMT")
    finally:
        writer.close()


def test_quarantined_parse_error_returns_on_parser_bump(tmp_path, monkeypatch):
    """Form-1960 PARSE_ERROR analogue: replay/fix comes from a parser bump, not refetch."""
    import santhau_studio.collectors.cgttrg as cgttrg

    writer = seed_records(tmp_path)
    try:
        make_eligible(writer)
        configure(writer.conn, {"enabled": True, "batch_requests": 30})
        rid = writer.conn.execute("SELECT MIN(id) FROM source_records").fetchone()[0]
        captured = capture(writer, payload(), "HSMT")
        sha = writer.conn.execute(
            "SELECT current_record_object_sha FROM source_records WHERE id=?", (rid,)
        ).fetchone()[0]
        response_sha = writer.conn.execute(
            "SELECT response_object_sha FROM deep_captures WHERE id=?", (captured.capture_id,)
        ).fetchone()[0]
        for _ in range(5):
            task_state(
                writer.conn, rid, "HSMT", "PARSE_ERROR",
                response_sha=response_sha, capture_id=captured.capture_id,
                error='["FORM_BD.DT.02.1960_ROW_0_INVALID_FIELDS"]',
                listing_sha=sha, failure_kind="PARSE",
            )
        write_task(
            writer.conn, rid, "HSMT",
            parser_version=cgttrg.PARSER_VERSION, adapter_version=2, capability="STRUCTURED_ITEMS",
        )
        assert retry_state(writer, rid, "HSMT")["quarantined"] is True
        backdate(writer, rid, "HSMT", 60 * 24 * 30)
        assert not is_selected(writer.conn, rid, "HSMT")
        monkeypatch.setattr(cgttrg, "PARSER_VERSION", cgttrg.PARSER_VERSION + 1)
        assert is_selected(writer.conn, rid, "HSMT")
    finally:
        writer.close()


def test_manual_release_reenables_quarantined_task(tmp_path):
    from contextlib import closing

    from santhau_studio.storage.warehouse import connect_read_only

    writer = seed_records(tmp_path)
    try:
        make_eligible(writer)
        configure(writer.conn, {"enabled": True, "batch_requests": 30})
        rid = 1
        for _ in range(5):
            fail(writer, rid, "HSMT", "FETCH", "response is not JSON")
        assert retry_state(writer, rid, "HSMT")["quarantined"] is True
        assert not is_selected(writer.conn, rid, "HSMT")
    finally:
        writer.close()
    result = reset_deep_retry(tmp_path, record_id=rid, lane="HSMT")
    assert result == {
        "ok": True, "record_id": rid, "lane": "HSMT",
        "was_quarantined": True, "previous_consecutive_failures": 5,
        "network_requests": 0,
    }
    with closing(connect_read_only(tmp_path / "warehouse.sqlite")) as conn:
        assert is_selected(conn, rid, "HSMT")
    assert reset_deep_retry(tmp_path, record_id=999, lane="HSMT")["ok"] is False


def test_fetched_refresh_after_30_days_is_unchanged(tmp_path):
    """FETCHED/SOURCE_EMPTY refresh behaviour (refresh_after_days=30) is untouched."""
    writer = seed_records(tmp_path)
    try:
        make_eligible(writer)
        configure(writer.conn, {"enabled": True, "batch_requests": 30})
        rid = writer.conn.execute("SELECT MIN(id) FROM source_records").fetchone()[0]
        captured = capture(writer, payload())
        sha = writer.conn.execute(
            "SELECT current_record_object_sha FROM source_records WHERE id=?", (rid,)
        ).fetchone()[0]
        task_state(
            writer.conn, rid, "DETAIL", "FETCHED",
            capture_id=captured.capture_id, listing_sha=sha,
        )
        # Fresh capture: not selected. A stale one is, exactly as before.
        assert not is_selected(writer.conn, rid, "DETAIL")
        writer.conn.execute(
            "UPDATE deep_captures SET stale=1, stale_reason='SOURCE_VERSION_CHANGED' WHERE id=?",
            (captured.capture_id,),
        )
        assert is_selected(writer.conn, rid, "DETAIL")
    finally:
        writer.close()


def test_lane_groups_chunk_within_fetcher_budget():
    targets = [
        {"id": i, "lane": lane}
        for lane in ("HSMT", "REOFFER", "DETAIL")
        for i in range(25)
    ]
    groups = _lane_groups(targets)
    assert [(lane, len(chunk)) for lane, chunk in groups] == [
        ("HSMT", 10), ("HSMT", 10), ("HSMT", 5),
        ("REOFFER", 10), ("REOFFER", 10), ("REOFFER", 5),
        ("DETAIL", 10), ("DETAIL", 10), ("DETAIL", 5),
    ]


def test_run_batch_dispatches_one_job_per_lane(tmp_path):
    writer = seed_records(tmp_path)
    try:
        make_eligible(writer)
        configure(writer.conn, {"enabled": True, "batch_requests": 9})
        loop = WorkerLoop(tmp_path)
        loop._writer = writer
        seen = []

        def dispatch(request, _run_id):
            seen.append(request)
            return "COMPLETED", {"requests_used": 0}

        loop._dispatch = dispatch
        assert run_batch(loop, "parent") == 9
        assert [r.job_kind for r in seen] == ["HSMT", "REOFFER", "DETAIL"]
        assert all(len(r.scope["record_ids"]) == 3 for r in seen)
        assert all(r.scope["record_ids"] == [1, 2, 3] for r in seen)
        assert all(r.request_limits["max_requests"] == 6 for r in seen)
        assert all(r.request_limits["max_seconds"] > 90 for r in seen)
        assert len({r.job_id for r in seen}) == 3
    finally:
        writer.close()
