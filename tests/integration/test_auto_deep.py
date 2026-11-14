from datetime import UTC, datetime

from test_deep_run import seed_records

from santhau_studio.workers.auto_deep import candidates, configure, run_batch
from santhau_studio.workers.jobs import JobRequest, WorkerLoop, enqueue_job


def make_eligible(writer):
    writer.conn.execute(
        "UPDATE source_record_versions SET normalized_json=json_set(normalized_json,'$.bidForm','CGTTRG')"
    )


def test_auto_deep_disabled_by_default_and_bounded_when_enabled(tmp_path):
    writer = seed_records(tmp_path)
    try:
        make_eligible(writer)
        assert candidates(writer.conn, 6) == []
        configure(writer.conn, {"enabled": True, "batch_requests": 4})
        result = candidates(writer.conn, 4)
        assert len(result) == 4
        assert {r["lane"] for r in result} == {"HSMT", "REOFFER", "DETAIL"}
    finally:
        writer.close()


def test_auto_deep_skips_checkpoint_and_cools_down_failures(tmp_path):
    writer = seed_records(tmp_path)
    try:
        make_eligible(writer)
        configure(writer.conn, {"enabled": True, "batch_requests": 6})
        selected = candidates(writer.conn, 6)
        record = selected[0]
        writer.conn.execute(
            "INSERT INTO deep_tasks(source_record_id,lane,state,updated_at_utc,error) VALUES(?,?,'BLOCKED',?,?)",
            (record["id"], record["lane"], datetime.now(UTC).isoformat(), "HTTP_403"),
        )
        assert not any(
            r["id"] == record["id"] and r["lane"] == record["lane"]
            for r in candidates(writer.conn, 6)
        )
    finally:
        writer.close()


def test_auto_deep_configuration_rejects_unbounded_or_fast_requests(tmp_path):
    import pytest

    writer = seed_records(tmp_path)
    try:
        for value in (0, 31, -1):
            with pytest.raises(ValueError):
                configure(writer.conn, {"enabled": True, "batch_requests": value})
        with pytest.raises(ValueError):
            configure(writer.conn, {"enabled": "yes"})
    finally:
        writer.close()


def test_auto_deep_runs_inside_parent_without_resetting_pause_or_current_job(tmp_path):
    writer = seed_records(tmp_path)
    try:
        make_eligible(writer)
        configure(writer.conn, {"enabled": True, "batch_requests": 2})
        loop = WorkerLoop(tmp_path)
        loop._writer = writer
        parent = JobRequest("parent", "BACKFILL", "test", (), {}, created_at="2026-11-05T00:00:00Z")
        loop._current = parent
        seen = []

        def dispatch(request, _run_id):
            seen.append(request)
            loop.flag.request_pause()
            return "PARTIAL", {"reason": "STOP_REQUESTED"}

        loop._dispatch = dispatch
        assert run_batch(loop, "parent-run") == 1
        assert loop._current is parent and loop.flag.interrupt_requested
        assert len(seen) == 1 and seen[0].request_limits["max_requests"] <= 4
    finally:
        writer.close()


def test_auto_deep_idle_does_not_starve_user_queue(tmp_path):
    writer = seed_records(tmp_path)
    make_eligible(writer)
    configure(writer.conn, {"enabled": True, "batch_requests": 2})
    writer.close()
    request = JobRequest("user-job", "DETAIL", "test", (), {}, created_at="2026-11-05T00:00:00Z")
    enqueue_job(tmp_path, request)
    loop = WorkerLoop(tmp_path, idle_exit_seconds=0.01, poll_seconds=0.001)
    called = []

    def dispatch(request, _run_id):
        called.append(request.job_id)
        if request.job_id != "user-job":
            loop.flag.request_shutdown()
        return "BLOCKED", {}

    loop._dispatch = dispatch
    loop.run()
    assert called[0] == "user-job"
    assert loop.exit_code == 0


def test_idle_automatic_collection_stays_paused_with_user_job(tmp_path):
    from santhau_studio.workers.auto_deep import enqueue_idle_batch

    writer = seed_records(tmp_path)
    try:
        make_eligible(writer)
        configure(writer.conn, {"enabled": True, "batch_requests": 2})
        loop = WorkerLoop(tmp_path)
        loop._writer = writer
        loop._paused_jobs.add("paused-parent")
        assert not enqueue_idle_batch(loop)
        assert not list((tmp_path / "jobs" / "queued").glob("*.json"))
    finally:
        writer.close()


def test_automatic_retry_budget_is_shared_between_record_jobs(tmp_path):
    writer = seed_records(tmp_path)
    try:
        make_eligible(writer)
        configure(writer.conn, {"enabled": True, "batch_requests": 6})
        loop = WorkerLoop(tmp_path)
        loop._writer = writer
        limits = []
        def dispatch(request, _run_id):
            limits.append(request.request_limits["max_requests"])
            return "FAILED", {"requests_used": request.request_limits["max_requests"]}
        loop._dispatch = dispatch
        # Six lane tasks are batched into one job per lane, so the shared
        # request budget still stops the scan after two lane jobs -- but four
        # lane tasks (HSMT x2, REOFFER x2) are dispatched, not two.
        assert run_batch(loop) == 4
        assert limits == [4, 2]
    finally:
        writer.close()


def test_automatic_worker_dispatch_persists_public_data_and_skips_it_next_time(tmp_path):
    from test_public_deep import hsmt

    from santhau_studio.collectors.cgttrg import public_spec

    writer = seed_records(tmp_path)
    try:
        make_eligible(writer)
        configure(writer.conn, {"enabled": True, "batch_requests": 9})
        calls = []

        class Fetcher:
            def __init__(self, request):
                self.request = request

            def fetch_record(self, record):
                calls.append((record["id"], self.request.job_kind))
                if self.request.job_kind == "HSMT":
                    value = hsmt([{"name": "Item", "qty": 2, "uom": "box"}])
                    value["bidoInvBiddingDTO"][0]["notifyNo"] = record["natural_key"]
                    return value
                return {
                    "notifyNo": record["natural_key"],
                    "notifyVersion": "00",
                    "bidForm": "CGTTRG",
                }

        loop = WorkerLoop(tmp_path, deep_factory=lambda _w, r, _s: Fetcher(r), deep_specs=public_spec)
        loop._writer = writer
        # Nine logical lane tasks are still dispatched and persisted. DETAIL and
        # REOFFER share one endpoint/body, so six physical fetches are enough:
        # HSMT plus one shared detail/reoffer response for each of three records.
        assert run_batch(loop, "parent") == 9
        assert len(calls) == 6
        assert sorted({kind for _, kind in calls}) == ["HSMT", "REOFFER"]
        assert writer.conn.execute("SELECT COUNT(*) FROM deep_tasks").fetchone()[0] == 9
        assert writer.conn.execute("SELECT COUNT(*) FROM deep_captures").fetchone()[0] == 9
        assert writer.conn.execute("SELECT COUNT(*) FROM goods_rows").fetchone()[0] == 3
        assert not candidates(writer.conn, 9)
        loop._deep_next_scan = 0
        assert run_batch(loop, "parent") == 0
        assert len(calls) == 6
    finally:
        writer.close()
