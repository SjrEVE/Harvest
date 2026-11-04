from contextlib import closing
from datetime import UTC, datetime

from santhau_studio.storage.warehouse import WarehouseWriter, connect_read_only
from santhau_studio.workers.jobs import JobRequest, WorkerLoop, enqueue_job


def test_headless_worker_never_builds_a_serving_database(tmp_path):
    with closing(WarehouseWriter.open(tmp_path, create_schema=True)):
        pass
    loop = WorkerLoop(tmp_path, publish_serving=False, idle_exit_seconds=0.01, poll_seconds=0.001)
    loop.run()
    assert loop.exit_code == 0, loop.last_error
    assert not list(tmp_path.glob("serving*"))


def test_headless_shutdown_requeues_inflight_request(tmp_path):
    with closing(WarehouseWriter.open(tmp_path, create_schema=True)):
        pass
    request = JobRequest(
        job_id="test-shutdown",
        job_kind="BACKFILL",
        requested_by="test",
        sources=(),
        scope={"from": "2026-01-01T00:00:00Z", "to": "2026-01-02T00:00:00Z"},
        created_at=datetime.now(UTC).isoformat(),
    )
    enqueue_job(tmp_path, request)
    loop = WorkerLoop(
        tmp_path, publish_serving=False, resume_on_shutdown=True, idle_exit_seconds=0.01
    )

    def dispatch(_request, _run):
        loop.flag.request_shutdown()
        return "PARTIAL", {"reason": "signal"}

    loop._dispatch = dispatch
    loop.run()
    assert loop.exit_code == 0
    assert len(list((tmp_path / "jobs/queued").glob("*.json"))) == 1
    assert not list((tmp_path / "jobs/done").glob("*.json"))
    assert not list(tmp_path.glob("serving*"))
    with closing(connect_read_only(tmp_path / "warehouse.sqlite")) as conn:
        assert conn.execute("SELECT lifecycle,status FROM runs").fetchone()[:] == (
            "TERMINATED",
            "INTERRUPTED",
        )
