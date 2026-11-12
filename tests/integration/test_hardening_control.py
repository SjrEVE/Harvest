from contextlib import closing
from datetime import UTC, datetime

from test_scoped_maintenance import setup

from santhau_studio.desktop.backend import StudioBackend
from santhau_studio.linux.fixture import builder
from santhau_studio.linux.repair import repair_gaps
from santhau_studio.storage import collection_scope
from santhau_studio.storage.warehouse import WarehouseWriter
from santhau_studio.workers.jobs import WorkerController, WorkerLoop
from santhau_studio.workers.scoped_maintenance import discover, run_batch


def test_shutdown_preserves_pending_repairs_and_resume_after_restart(tmp_path):
    setup(tmp_path)
    repair_gaps(tmp_path, execute=True)
    with closing(WarehouseWriter.open(tmp_path, create_schema=True)) as writer:
        loop = WorkerLoop(tmp_path)
        loop._writer = writer
        controller = WorkerController(loop)
        assert controller({"op":"command", "command":"FORCE_TERMINATE"}, "test")["ok"]
        loop._drain_commands()
        assert writer.conn.execute("SELECT state FROM maintenance_tasks").fetchone()[0] == "WAITING"
        assert writer.conn.execute("SELECT COUNT(*) FROM maintenance_holds").fetchone()[0] == 0
        restarted = WorkerLoop(tmp_path, page_source_builder=builder)
        restarted._writer = writer
        assert run_batch(restarted)
        assert writer.conn.execute("SELECT state FROM maintenance_tasks").fetchone()[0] == "COMPLETE"
        controller.close()


def test_background_job_and_paused_job_are_visible_and_controllable(tmp_path, monkeypatch):
    setup(tmp_path)
    repair_gaps(tmp_path, execute=True)
    with closing(WarehouseWriter.open(tmp_path, create_schema=True)) as writer:
        loop = WorkerLoop(tmp_path)
        loop._writer = writer
        controller = WorkerController(loop)
        backend = StudioBackend(tmp_path)
        monkeypatch.setattr(backend, "worker_status", lambda:{"running":True,"pipe_ok":True,**controller({"op":"ping"},"test")})
        monkeypatch.setattr(backend, "job_status", lambda jid:controller({"op":"status","job_id":jid},"test"))
        observed=[]
        def source(w, request):
            def pages(*args):
                result=backend.jobs()
                observed.append(result)
                assert result["counts"]["running"] == 1
                assert result["active"][0]["can_pause"]
                for page in builder(w, request)(*args):
                    yield page
                    loop.post_command(job_id=request.job_id, command="PAUSE", sid="test")
                    break
            return pages
        loop._builder=source
        assert run_batch(loop)
        loop._drain_commands()
        assert observed
        result=backend.jobs()
        assert result["counts"]["paused"] == 1
        assert result["active"][0]["can_resume"]
        controller.close()


# Pin the ceilings this assertion was written against. The invariant under test
# is "linear, not N-squared", so it must not silently depend on DEFAULT_POLICY.
PINNED_POLICY = {
    "repair_enabled": True, "reconcile_enabled": False, "max_attempts": 4,
    "max_total_requests": 6000, "max_total_seconds": 14400,
    "day_max_pages": 1200, "day_max_requests": 1500, "day_max_seconds": 3600,
    "quota_per_day": 1, "reconcile_quota_per_day": 2,
    "reconcile_every_days": 7, "overlap_days": 3,
}


def test_exhausted_quota_does_not_enumerate_scope_for_every_pending_task(tmp_path, monkeypatch):
    setup(tmp_path)
    now=datetime.now(UTC)
    with closing(WarehouseWriter.open(tmp_path, create_schema=True)) as writer:
        collection_scope.configure(writer.conn,first="2025-01-01",last="2025-06-29",families=("TBMT",),policy=PINNED_POLICY)
        discover(writer.conn,tmp_path,execute=True,now=now)
        key=writer.conn.execute("SELECT task_key FROM maintenance_tasks LIMIT 1").fetchone()[0]
        writer.register_run("quota","quota","BACKFILL")
        writer.finish_run("quota","COMPLETED")
        writer.conn.execute("INSERT INTO maintenance_attempts(task_key,attempt_no,run_id,started_at_utc,reserved_requests,reserved_seconds) VALUES(?,1,'quota',?,1,1)",(key,now.isoformat().replace("+00:00","Z")))
        loop=WorkerLoop(tmp_path)
        loop._writer=writer
        count=0
        original=collection_scope.windows
        def windows(scope):
            nonlocal count
            for item in original(scope):
                count+=1
                yield item
        monkeypatch.setattr(collection_scope,"windows",windows)
        assert not run_batch(loop)
        assert count <= 360  # At most two passes through 180 windows, not N squared.



def test_raised_default_ceiling_stays_linear(tmp_path, monkeypatch):
    """The RC3-7 ceiling may add a pass, but never turns O(N) into O(N^2)."""
    setup(tmp_path)
    now = datetime.now(UTC)
    with closing(WarehouseWriter.open(tmp_path, create_schema=True)) as writer:
        collection_scope.configure(writer.conn, first="2025-01-01", last="2025-06-29",
                                   families=("TBMT",), policy={"repair_enabled": True, "quota_per_day": 1})
        discover(writer.conn, tmp_path, execute=True, now=now)
        key = writer.conn.execute("SELECT task_key FROM maintenance_tasks LIMIT 1").fetchone()[0]
        writer.register_run("quota2", "quota2", "BACKFILL")
        writer.finish_run("quota2", "COMPLETED")
        writer.conn.execute(
            "INSERT INTO maintenance_attempts(task_key,attempt_no,run_id,started_at_utc,reserved_requests,reserved_seconds)"
            " VALUES(?,1,'quota2',?,1,1)", (key, now.isoformat().replace("+00:00", "Z")))
        loop = WorkerLoop(tmp_path)
        loop._writer = writer
        count = 0
        original = collection_scope.windows

        def windows(scope):
            nonlocal count
            for item in original(scope):
                count += 1
                yield item

        monkeypatch.setattr(collection_scope, "windows", windows)
        assert not run_batch(loop)
        assert count <= 600, f"ceiling turned linear into quadratic: {count} windows"
        assert count > 360, "the raised DEFAULT_POLICY did not take effect"
