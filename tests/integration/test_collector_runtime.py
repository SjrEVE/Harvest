"""Platform-neutral collector regressions, with no Qt or Windows pipe imports."""
import hashlib
from contextlib import closing
from datetime import date

from santhau_studio.collectors.source_registry import SOURCES
from santhau_studio.domain.coverage import vn_calendar_day
from santhau_studio.storage.warehouse import WarehouseWriter
from santhau_studio.workers.jobs import JobRequest, WorkerController, WorkerLoop

ADAPTER = hashlib.sha256(b"headless-runtime-fixture").hexdigest()


def job_request(**kwargs):
    day = vn_calendar_day(date(2026, 11, 1))
    values = {"job_id": "headless-test", "job_kind": "BACKFILL", "requested_by": "test",
              "sources": ({"source_key": "egp_home", "family": "TBMT",
                           "endpoint": SOURCES["egp_home"].endpoint, "adapter_sha": ADAPTER},),
              "scope": {"from": day.from_utc.isoformat(), "to": day.to_utc.isoformat()},
              "created_at": "2026-11-02T00:00:00Z"}
    return JobRequest(**{**values, **kwargs})


def insert_rule(writer, request, precision="HOUR"):
    source = request.sources[0]
    writer.conn.execute(
        "INSERT INTO filter_boundary_rules (rule_key, rule_version, source_key, family, endpoint,"
        " adapter_sha, boundary_kind, precision, source_timezone, offset_minutes, conversion_params,"
        " canary_verified, verified_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("headless-fixture", 1, source["source_key"], source["family"], source["endpoint"],
         source["adapter_sha"], "CLOSED_LOCAL", precision, "Asia/Ho_Chi_Minh", 420,
         '{"to_minus":"1s"}', 1, "2026-10-31T00:00:00Z"))
    writer.conn.commit()


def test_idle_control_rejects_pause_or_stop_without_poisoning_next_job(tmp_path):
    loop = WorkerLoop(tmp_path, publish_serving=False)
    controller = WorkerController(loop)
    for command in ("PAUSE", "STOP_GRACEFUL"):
        reply = controller({"op": "command", "command": command}, "test")
        assert not reply["ok"]
    assert not loop.flag.interrupt_requested


def test_pending_resume_cannot_enqueue_duplicate_commands(tmp_path):
    loop = WorkerLoop(tmp_path, publish_serving=False)
    loop._paused_jobs.add("paused")
    controller = WorkerController(loop)
    command = {"op": "command", "command": "RESUME", "job_id": "paused"}
    assert controller(command, "test")["ok"]
    assert not controller(command, "test")["ok"]


def test_completed_status_does_not_reuse_previous_progress(tmp_path):
    request = job_request()
    with closing(WarehouseWriter.open(tmp_path, create_schema=True)) as writer:
        writer.register_run("finished", request.job_id, "BACKFILL")
        writer.finish_run("finished", "COMPLETED")
        loop = WorkerLoop(tmp_path, publish_serving=False)
        loop.progress = {"job_id": request.job_id, "rows": 100, "state": "COLLECTING"}
        controller = WorkerController(loop)
        try:
            reply = controller({"op": "status", "job_id": request.job_id}, "test")
            assert reply["runs"][-1]["status"] == "COMPLETED"
            assert reply["progress"] == {}
        finally:
            controller.close()


def test_headless_runtime_exposes_start_and_stop_without_serving(tmp_path):
    loop = WorkerLoop(tmp_path, publish_serving=False, idle_exit_seconds=.01)
    controller = WorkerController(loop)
    assert controller({"op": "ping"}, "test")["runtime"]["phase"] == "STARTING"
    loop.run()
    assert loop.exit_code == 0, loop.last_error
    state = controller({"op": "ping"}, "test")["runtime"]
    assert state["phase"] == "STOPPED" and state["progress"] == {}
    assert not list(tmp_path.glob("serving*"))
