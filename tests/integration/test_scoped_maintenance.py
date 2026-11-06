from contextlib import closing
from datetime import UTC, datetime

from santhau_studio.linux.config import make_jobs
from santhau_studio.linux.fixture import builder, initialize_fixture
from santhau_studio.linux.repair import configure_scope, coverage_report, repair_gaps
from santhau_studio.storage.warehouse import WarehouseWriter
from santhau_studio.workers.jobs import WorkerLoop


def setup(tmp_path, policy=None):
    initialize_fixture(tmp_path)
    configure_scope(
        tmp_path, first="2026-09-01", last="2026-09-01", families=("TBMT",), policy=policy
    )
    return tmp_path


def test_preview_no_writes_and_execute_deduplicates(tmp_path):
    root = setup(tmp_path)
    before = (root / "warehouse.sqlite").read_bytes()
    a = repair_gaps(root)
    assert a["dry_run"] and a["candidate_count"] == 1
    assert (root / "warehouse.sqlite").read_bytes() == before
    assert not list((root / "jobs").rglob("*.json"))
    assert repair_gaps(root, execute=True)["created"] == 1
    assert repair_gaps(root, execute=True)["created"] == 0


def test_fresh_day_budget_finishes_without_previous_proof_and_keeps_history(tmp_path):
    from test_runner_modes import page_of

    from santhau_studio.workers.scoped_maintenance import run_batch

    def eight_pages(_writer, _request):
        def pages(*_):
            yield from (page_of(i, 10, total=80) for i in range(8))
            yield page_of(8, 0, total=80)

        return pages

    root = setup(tmp_path)
    job = make_jobs(
        root, family="TBMT", first="2026-09-01", last="2026-09-01", limits={"max_pages": 3}
    )[0]
    with closing(WarehouseWriter.open(root, create_schema=True)) as writer:
        loop = WorkerLoop(data_root=root, page_source_builder=eight_pages)
        loop._writer = writer
        writer.register_run("initial", job.job_id, "BACKFILL")
        status, detail = loop._dispatch(job, "initial")
        # Trusted total lets the runner stop early instead of wasting the whole cap.
        assert status == "PARTIAL" and 0 < detail["pages"] <= 3
    assert coverage_report(root)["summary"]["proved"] == 0
    repair_gaps(root, execute=True)
    with closing(WarehouseWriter.open(root, create_schema=True)) as writer:
        loop._writer = writer
        assert run_batch(loop)
        assert (
            writer.conn.execute("SELECT state FROM maintenance_tasks").fetchone()[0] == "COMPLETE"
        )
        assert (
            writer.conn.execute(
                "SELECT COUNT(*) FROM coverage_intervals WHERE status='PARTIAL'"
            ).fetchone()[0]
            > 0
        )
    assert coverage_report(root)["summary"]["proved"] == 1


def test_stop_is_durable_and_discovery_cannot_resurrect(tmp_path):
    from santhau_studio.workers.scoped_maintenance import control, run_batch

    root = setup(tmp_path)
    repair_gaps(root, execute=True)
    with closing(WarehouseWriter.open(root, create_schema=True)) as writer:
        control(writer.conn, None, "STOP_GRACEFUL")
    repair_gaps(root, execute=True)
    with closing(WarehouseWriter.open(root, create_schema=True)) as writer:
        loop = WorkerLoop(
            data_root=root,
            page_source_builder=lambda *_: (_ for _ in ()).throw(AssertionError("HTTP")),
        )
        loop._writer = writer
        assert not run_batch(loop)
        assert (
            writer.conn.execute("SELECT state FROM maintenance_tasks").fetchone()[0] == "CANCELLED"
        )


def test_crash_reservations_charged_backoff_persisted_and_context_change_blocks(tmp_path):
    from santhau_studio.workers.scoped_maintenance import recover, run_batch

    root = setup(tmp_path)
    repair_gaps(root, execute=True)
    with closing(WarehouseWriter.open(root, create_schema=True)) as writer:
        conn = writer.conn
        conn.execute(
            "UPDATE maintenance_tasks SET state='RUNNING',attempts=1,requests_used=100,seconds_used=300,next_attempt_at='2026-01-01T00:00:00Z'"
        )
        recover(conn, now=datetime.now(UTC))
        row = conn.execute("SELECT * FROM maintenance_tasks").fetchone()
        assert row["state"] == "WAITING" and row["requests_used"] == 100
        assert datetime.fromisoformat(row["next_attempt_at"].replace("Z", "+00:00")) > datetime.now(
            UTC
        )
        conn.execute("UPDATE maintenance_tasks SET next_attempt_at='2026-01-01T00:00:00Z'")
        conn.execute("UPDATE filter_boundary_rules SET adapter_sha=?", ("a" * 64,))
        loop = WorkerLoop(data_root=root, page_source_builder=builder)
        loop._writer = writer
        run_batch(loop)
        assert conn.execute("SELECT state FROM maintenance_tasks").fetchone()[0] == "MANUAL_REVIEW"


def test_transient_failure_delays_and_recovers_without_duplicate_requests(tmp_path):
    from santhau_studio.collectors.http_client import FetchFailed
    from santhau_studio.workers.scoped_maintenance import run_batch

    root = setup(tmp_path)
    repair_gaps(root, execute=True)

    def failing(*_):
        def pages(*_):
            raise FetchFailed("ConnectTimeout")

        return pages

    with closing(WarehouseWriter.open(root, create_schema=True)) as writer:
        loop = WorkerLoop(data_root=root, page_source_builder=failing)
        loop._writer = writer
        assert run_batch(loop)
        row = writer.conn.execute("SELECT * FROM maintenance_tasks").fetchone()
        assert row["state"] == "WAITING" and row["attempts"] == 1
        assert not run_batch(loop)
        loop._builder = builder
        writer.conn.execute("UPDATE maintenance_tasks SET next_attempt_at='2026-01-01T00:00:00Z'")
        assert run_batch(loop)
        assert (
            writer.conn.execute("SELECT state FROM maintenance_tasks").fetchone()[0] == "COMPLETE"
        )


def test_reconcile_is_opt_in_and_never_exits_scope(tmp_path):
    from santhau_studio.workers.scoped_maintenance import run_batch

    root = setup(tmp_path, {"reconcile_enabled": True})
    repair_gaps(root, execute=True)
    with closing(WarehouseWriter.open(root, create_schema=True)) as writer:
        loop = WorkerLoop(data_root=root, page_source_builder=builder)
        loop._writer = writer
        assert run_batch(loop)
        assert run_batch(loop)
        rows = writer.conn.execute(
            "SELECT * FROM maintenance_tasks WHERE kind='RECONCILE'"
        ).fetchall()
        assert len(rows) == 1 and rows[0]["state"] == "COMPLETE"
        assert rows[0]["interval_from_utc"] == "2026-08-31T17:00:00Z"
        assert rows[0]["interval_to_utc"] == "2026-09-01T17:00:00Z"
        assert not run_batch(loop)


def test_stop_before_any_repair_task_blocks_future_discovery(tmp_path):
    from santhau_studio.workers.scoped_maintenance import control, run_batch

    root = setup(tmp_path, {"repair_enabled": True})
    job = make_jobs(root, family="TBMT", first="2026-09-01", last="2026-09-01")[0]
    with closing(WarehouseWriter.open(root, create_schema=True)) as writer:
        control(writer.conn, job.job_id, "STOP_GRACEFUL", request=job)
        loop = WorkerLoop(
            root,
            page_source_builder=lambda *_: (_ for _ in ()).throw(AssertionError("HTTP after stop")),
        )
        loop._writer = writer
        assert not run_batch(loop)
        assert writer.conn.execute("SELECT COUNT(*) FROM maintenance_attempts").fetchone()[0] == 0


def test_maintenance_pause_can_resume_through_worker_controller(tmp_path):
    from santhau_studio.workers.scoped_maintenance import run_batch

    root = setup(tmp_path)
    repair_gaps(root, execute=True)
    with closing(WarehouseWriter.open(root, create_schema=True)) as writer:
        loop = WorkerLoop(root, page_source_builder=builder)

        def pause_builder(w, request):
            def pages(*args):
                for page in builder(w, request)(*args):
                    yield page
                    loop.flag.request_pause()

            return pages

        loop._builder = pause_builder
        loop._writer = writer
        assert run_batch(loop)
        assert writer.conn.execute("SELECT state FROM maintenance_tasks").fetchone()[0] == "PAUSED"
        loop.post_command(job_id=loop._paused_job, command="RESUME", sid="test")
        loop._drain_commands()
        writer.conn.execute("UPDATE maintenance_tasks SET next_attempt_at='2026-01-01T00:00:00Z'")
        loop._builder = builder
        assert run_batch(loop)
        assert (
            writer.conn.execute("SELECT state FROM maintenance_tasks").fetchone()[0] == "COMPLETE"
        )


def test_weekly_transient_fault_does_not_starve_other_families(tmp_path):
    from santhau_studio.collectors.http_client import FetchFailed

    initialize_fixture(tmp_path)
    request = make_jobs(tmp_path, first="2026-09-01", last="2026-09-01")[0]

    def failing_tbmt(writer, job):
        if job.sources[0]["family"] == "TBMT":

            def pages(*_):
                raise FetchFailed("ConnectTimeout")

            return pages
        return builder(writer, job)

    with closing(WarehouseWriter.open(tmp_path, create_schema=True)) as writer:
        loop = WorkerLoop(tmp_path, page_source_builder=failing_tbmt, publish_serving=False)
        loop._writer = writer
        writer.register_run("weekly", request.job_id, "BACKFILL")
        status, detail = loop._dispatch(request, "weekly")
        assert status == "PARTIAL" and detail["pending_repairs"]
        assert (
            writer.conn.execute(
                "SELECT COUNT(DISTINCT family) FROM coverage_intervals WHERE status='COMPLETE'"
            ).fetchone()[0]
            == 5
        )
        assert (
            writer.conn.execute(
                "SELECT COUNT(*) FROM maintenance_tasks WHERE approved=0"
            ).fetchone()[0]
            == 1
        )


def test_explicit_stop_command_survives_crash_recovery(tmp_path):
    from santhau_studio.workers.jobs import claim_next_job, enqueue_job, recover_interrupted_runs

    root = setup(tmp_path)
    job = make_jobs(root, family="TBMT", first="2026-09-01", last="2026-09-01")[0]
    enqueue_job(root, job)
    claim_next_job(root)
    with closing(WarehouseWriter.open(root, create_schema=True)) as writer:
        writer.register_run("stopped-before-crash", job.job_id, "BACKFILL")
        writer.conn.execute(
            "INSERT INTO job_commands(job_id,seq,command,issued_at_utc,issued_by_sid) VALUES(?,1,'STOP_GRACEFUL','2026-11-06T00:00:00Z','test')",
            (job.job_id,),
        )
        summary = recover_interrupted_runs(writer.conn, root)
        assert summary["requeued_jobs"] == 0
    assert not list((root / "jobs/queued").glob("*.json"))
    assert len(list((root / "jobs/done").glob("*.json"))) == 1


def test_discovery_does_not_retry_previous_http_access_denial(tmp_path):
    from santhau_studio.collectors.http_client import AccessBlocked
    from santhau_studio.workers.scoped_maintenance import run_batch

    root = setup(tmp_path, {"repair_enabled": True})
    job = make_jobs(root, family="TBMT", first="2026-09-01", last="2026-09-01")[0]
    calls = []

    def blocked(*_):
        def pages(*_):
            calls.append(1)
            raise AccessBlocked("HTTP_403")

        return pages

    with closing(WarehouseWriter.open(root, create_schema=True)) as writer:
        loop = WorkerLoop(root, page_source_builder=blocked)
        loop._writer = writer
        writer.register_run("denied", job.job_id, "BACKFILL")
        loop._dispatch(job, "denied")
        assert len(calls) == 1
        assert not run_batch(loop)
        assert len(calls) == 1
        assert (
            writer.conn.execute("SELECT state FROM maintenance_tasks").fetchone()[0]
            == "MANUAL_REVIEW"
        )


def test_reconcile_never_reports_full_check_when_only_half_scope_was_proved(tmp_path):
    root = setup(tmp_path)
    backfill = make_jobs(root, family="TBMT", first="2026-09-01", last="2026-09-01")[0]
    audit = make_jobs(root, mode="RECONCILE", family="TBMT", first="2026-09-01", last="2026-09-02")[
        0
    ]
    with closing(WarehouseWriter.open(root, create_schema=True)) as writer:
        loop = WorkerLoop(root, page_source_builder=builder)
        loop._writer = writer
        writer.register_run("one-day", backfill.job_id, "BACKFILL")
        assert loop._dispatch(backfill, "one-day")[0] == "COMPLETED"
        writer.register_run("two-days-audit", audit.job_id, "RECONCILE")
        status, detail = loop._dispatch(audit, "two-days-audit")
        assert status == "PARTIAL" and detail["audit_status"] == "PARTIAL"
        assert detail["checked_intervals"] == 1
