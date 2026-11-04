"""Weekly jobs retain proven work and never advance past an incomplete week."""
from contextlib import closing
from datetime import UTC, datetime

import pytest

from santhau_studio.collectors.egp_home import Page
from santhau_studio.collectors.http_client import FetchFailed
from santhau_studio.collectors.offline_acceptance import initialize
from santhau_studio.collectors.source_registry import FAMILY_ORDER
from santhau_studio.desktop.backend import StudioBackend
from santhau_studio.storage.warehouse import connect_read_only
from santhau_studio.workers.jobs import JobRequest, WorkerLoop


@pytest.fixture
def studio(tmp_path, monkeypatch):
    initialize(tmp_path)
    monkeypatch.setenv("SANTHAU_OFFLINE_ACCEPTANCE_ROOT", str(tmp_path.resolve()))
    return StudioBackend(tmp_path)


def queue(studio, **kw):
    return studio.enqueue_collection({"family": "ALL", "job_kind": "BACKFILL",
        "cadence": "WEEK", "date_from": "01/10/2026", "date_to": "08/10/2026", **kw})


def fast_builder(calls, *, fail=None, pause=None):
    def build(_writer, request):
        family = request.sources[0]["family"]
        used = 0
        def pages(interval, _granularity, _window):
            nonlocal used
            used += 1
            calls.append((family, interval))
            if fail:
                fail(family, interval, used)
            key, version = {"TBMT": ("notifyNo", "notifyVersion"), "KQLCNT": ("notifyNo", "notifyVersion"),
                "KHLCNT": ("planNo", "planVersion"), "HOP_DONG": ("contractCode", "contractVersion"),
                "DU_AN": ("pno", "pversion"), "YCBG": ("notifyNo", "notifyVersion")}[family]
            row = {key: f"SYNTH-{family}-{interval.from_utc:%Y%m%d}", version: "00",
                "publicDate": interval.from_utc.isoformat(), "publicDateKqlcnt": interval.from_utc.isoformat(),
                "bidName": "Hồ sơ thử ngoại tuyến", "pname": "Dự án thử", "ctName": "Hợp đồng thử"}
            yield Page(payload={"page": {"content": [row], "totalElements": 1}}, page_no=0,
                rows=1, has_more=False, total_hint=1, total_hint_trusted=True, http_status=200, attempt_log=())
            if pause:
                pause()
        return pages
    return build


def run(studio, builder):
    loop = WorkerLoop(studio.data_root, page_source_builder=builder,
        idle_exit_seconds=.01, poll_seconds=.001, clock=lambda: datetime(2026, 11, 4, tzinfo=UTC))
    loop.run()
    assert loop.exit_code == 0, loop.last_error
    return loop


def counts(studio):
    with closing(connect_read_only(studio.data_root / "warehouse.sqlite")) as conn:
        return tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("source_records", "source_record_versions", "observations"))


def test_one_durable_job_week_order_and_rerun_skips_all_saved_days(studio):
    result = queue(studio)
    assert result["ok"] and len(result["jobs"]) == 1
    files = list((studio.data_root / "jobs/queued").glob("*.json"))
    request = JobRequest.from_json(files[0].read_text())
    assert len(request.sources) == 6 and request.scope["cadence"] == "WEEK"
    calls = []
    run(studio, fast_builder(calls))
    assert len(calls) == 48
    assert [f for f, _ in calls[:6]] == list(FAMILY_ORDER)
    assert all(span.from_utc >= datetime(2026, 10, 7, 17, tzinfo=UTC) for _, span in calls[:6])
    assert all(span.from_utc < datetime(2026, 10, 7, 17, tzinfo=UTC) for _, span in calls[6:])
    assert calls[6][1].from_utc == datetime(2026, 10, 6, 17, tzinfo=UTC)
    before = counts(studio)
    assert before == (48, 48, 48)
    assert queue(studio)["ok"]
    calls.clear()
    run(studio, fast_builder(calls))
    assert calls == [] and counts(studio) == before


def test_newest_first_reuses_days_already_collected_by_oldest_first_job(studio):
    assert queue(studio, date_to="07/10/2026")["ok"]
    run(studio, fast_builder([]))
    assert counts(studio) == (42, 42, 42)
    assert queue(studio)["ok"]
    calls = []
    run(studio, fast_builder(calls))
    assert len(calls) == 6
    assert all(span.from_utc == datetime(2026, 10, 7, 17, tzinfo=UTC) for _, span in calls)
    assert counts(studio) == (48, 48, 48)


def test_budget_continues_only_missing_days_before_next_week(studio):
    assert queue(studio)["ok"]
    calls = []
    def cap(_family, _interval, used):
        if used > 2:
            raise FetchFailed("REQUEST_BUDGET_EXHAUSTED")
    run(studio, fast_builder(calls, fail=cap))
    assert counts(studio) == (48, 48, 48)
    with closing(connect_read_only(studio.data_root / "warehouse.sqlite")) as conn:
        parent = conn.execute("SELECT status FROM runs WHERE job_id NOT LIKE '%.w%' ORDER BY rowid DESC LIMIT 1").fetchone()
        assert parent[0] == "COMPLETED"


def test_network_failure_and_no_progress_stop_without_next_week(studio):
    result = queue(studio)
    calls = []
    def fail(_family, _interval, _used):
        raise FetchFailed("NETWORK_UNAVAILABLE")
    run(studio, fast_builder(calls, fail=fail))
    assert len(calls) <= 7
    assert len({f for f, _ in calls}) == 1
    assert all(span.from_utc >= datetime(2026, 10, 7, 17, tzinfo=UTC) for _, span in calls)
    assert studio.job_status(result["job_id"]) is not None
    with closing(connect_read_only(studio.data_root / "warehouse.sqlite")) as conn:
        assert conn.execute("SELECT status FROM runs WHERE job_id=?", (result["job_id"],)).fetchone()[0] == "PARTIAL"


def test_weekly_rejects_missing_family_without_partial_queue(studio):
    import sqlite3

    from santhau_studio.storage.serving import build_serving_db
    with closing(sqlite3.connect(studio.data_root / "warehouse.sqlite")) as conn:
        conn.execute("UPDATE filter_boundary_rules SET canary_verified=0 WHERE family='KHLCNT'")
        conn.commit()
    build_serving_db(studio.data_root / "warehouse.sqlite", studio.serving_path)
    result = queue(studio)
    assert not result["ok"] and result["blocked"]
    assert not list((studio.data_root / "jobs/queued").glob("*.json"))


def test_weekly_refuses_old_running_worker_without_interrupting_it(studio, monkeypatch):
    monkeypatch.setattr(studio, "worker_status", lambda: {"running": True, "pipe_ok": True})
    result = queue(studio)
    assert not result["ok"] and result["error_code"] == "WORKER_UPGRADE_REQUIRED"
    assert not list((studio.data_root / "jobs/queued").glob("*.json"))


@pytest.mark.parametrize("capability", ["weekly-six-v1", "weekly-six-newest-v2"])
def test_weekly_refuses_a_worker_with_outdated_weekly_handling(studio, monkeypatch, capability):
    monkeypatch.setattr(studio, "worker_status", lambda: {
        "running": True, "pipe_ok": True, "capabilities": [capability]})
    result = queue(studio)
    assert not result["ok"] and result["error_code"] == "WORKER_UPGRADE_REQUIRED"
    assert not list((studio.data_root / "jobs/queued").glob("*.json"))


def test_totals_match_each_family_date_and_do_not_sum_overlapping_proofs(studio):
    assert queue(studio)["ok"]
    run(studio, fast_builder([]))
    report = studio.coverage_range({"date_from": "01/10/2026", "date_to": "08/10/2026", "resolution": "WEEK"})
    assert report["ok"] and len(report["periods"]) == 2
    for period in report["periods"]:
        expected = 7 if period["date_from"] == "01/10/2026" else 1
        for cell in period["families"]:
            assert (cell["stored"], cell["source_total"], cell["missing"], cell["percent"]) == (expected, expected, 0, 100)
            assert cell["proven_days"] == expected
    # An intentional refresh adds overlapping proof; totals must not double.
    assert studio.enqueue_collection({"family": "ALL", "job_kind": "RECONCILE", "date_from": "01/10/2026", "date_to": "08/10/2026"})["ok"]
    run(studio, fast_builder([]))
    again = studio.coverage_range({"date_from": "01/10/2026", "date_to": "08/10/2026", "resolution": "WEEK"})
    assert [c["source_total"] for p in again["periods"] for c in p["families"]] == [1]*6 + [7]*6
    unknown = studio.coverage_range({"date_from": "01/09/2026", "date_to": "30/09/2026"})
    assert all(c["source_total"] is None and c["missing"] is None and c["percent"] is None
               for c in unknown["periods"][0]["families"])


@pytest.mark.parametrize("command", ["PAUSE", "STOP_GRACEFUL", "FORCE_TERMINATE"])
def test_weekly_controls_keep_records_and_do_not_advance_week(studio, command):
    import threading
    result = queue(studio)
    calls = []
    stopped = threading.Event()
    loop = WorkerLoop(studio.data_root, idle_exit_seconds=.01, poll_seconds=.001,
        clock=lambda: datetime(2026, 11, 4, tzinfo=UTC))
    normal = fast_builder(calls)
    def controlled(writer, request):
        pages = normal(writer, request)
        def stream(*args):
            if len(calls) == 2 and not stopped.is_set():
                stopped.set()
                loop.post_command(job_id=result["job_id"], command=command, sid="test")
            yield from pages(*args)
        return stream
    loop._builder = controlled
    loop.run()
    assert loop.exit_code == 0, loop.last_error
    assert stopped.is_set() and 2 <= counts(studio)[0] <= 3
    before = counts(studio)
    assert all(span.from_utc >= datetime(2026, 10, 7, 17, tzinfo=UTC) for _, span in calls)
    # Resume a paused parent or crash-recovered parent through the existing queue.
    if command == "PAUSE":
        from santhau_studio.workers.jobs import requeue_active_job
        assert requeue_active_job(studio.data_root, result["job_id"])
    else:
        assert queue(studio)["ok"]
    calls.clear()
    run(studio, fast_builder(calls))
    assert counts(studio) == (48, 48, 48)
    assert len(calls) == 48 - before[0]


def test_real_process_crash_recovers_parent_and_keeps_proven_days(studio, tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    result = queue(studio)
    seam = tmp_path / "crash_builder.py"
    seam.write_text("import importlib.util, os\n"
        f"spec=importlib.util.spec_from_file_location('fixture', {str(Path(__file__).resolve())!r})\n"
        "fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)\n"
        "calls=[]\nnormal=fixture.fast_builder(calls)\n"
        "def build(writer,request):\n"
        "    pages=normal(writer,request)\n"
        "    def stream(*args):\n"
        "        if len(calls)==2: os._exit(71)\n"
        "        yield from pages(*args)\n"
        "    return stream\n", encoding="utf-8")
    process = subprocess.run([sys.executable, "-m", "santhau_studio.workers.service", "--data-root", str(studio.data_root),
        "--page-source-file", str(seam), "--idle-exit-seconds", ".1"],
        env={k: v for k, v in os.environ.items() if k != "SANTHAU_OFFLINE_ACCEPTANCE_ROOT"}, capture_output=True, timeout=20)
    assert process.returncode == 71, process.stderr
    assert counts(studio) == (2, 2, 2)
    calls = []
    run(studio, fast_builder(calls))
    assert counts(studio) == (48, 48, 48) and len(calls) == 46
    with closing(connect_read_only(studio.data_root / "warehouse.sqlite")) as conn:
        states = [row[0] for row in conn.execute("SELECT status FROM runs WHERE job_id=? ORDER BY rowid", (result["job_id"],))]
        assert states == ["INTERRUPTED", "COMPLETED"]


def test_worker_upgrades_old_display_projection_without_any_source_request(studio):
    import sqlite3
    with closing(sqlite3.connect(studio.serving_path)) as conn:
        conn.execute("DROP INDEX ix_rf_collection_date")
        conn.execute("ALTER TABLE record_fields DROP COLUMN collection_date")
        conn.commit()
    calls = []
    run(studio, fast_builder(calls))
    assert not calls
    report = studio.coverage_range({"date_from": "01/10/2026", "date_to": "08/10/2026"})
    assert report["counts_available"]


def test_snapshot_is_marked_stale_for_each_family_after_previous_publish(studio):
    from santhau_studio.storage.serving import serving_state
    assert queue(studio, date_to="01/10/2026")["ok"]
    stale_states = []
    normal = fast_builder([])
    def checked(writer, request):
        stale_states.append(serving_state(studio.serving_path)["stale"])
        return normal(writer, request)
    run(studio, checked)
    assert stale_states == [True] * 6
    assert not serving_state(studio.serving_path)["stale"]


def test_parent_status_survives_more_than_200_child_runs(studio):
    from santhau_studio.storage.serving import build_serving_db
    from santhau_studio.storage.warehouse import WarehouseWriter
    from santhau_studio.workers.jobs import claim_next_job, finish_job
    result = queue(studio)
    assert claim_next_job(studio.data_root).job_id == result["job_id"]
    with closing(WarehouseWriter.open(studio.data_root)) as writer:
        writer.register_run("parent", result["job_id"], "BACKFILL")
        writer.finish_run("parent", "COMPLETED")
        writer.conn.execute("UPDATE runs SET started_at_utc='2026-01-01T00:00:00Z' WHERE run_id='parent'")
        for i in range(240):
            writer.register_run(f"child-{i}", f"{result['job_id']}.w{i}", "BACKFILL")
            writer.finish_run(f"child-{i}", "COMPLETED")
        writer.conn.commit()
    assert finish_job(studio.data_root, result["job_id"])
    assert build_serving_db(studio.data_root / "warehouse.sqlite", studio.serving_path).published
    done = studio.jobs()["done"]
    assert done[0].get("status") == "COMPLETED"
