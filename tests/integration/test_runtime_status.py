"""Live control state must win over stale serving data and remain observable at startup."""
from contextlib import closing

from test_worker_ipc import job_request, portal_builder

from santhau_studio.desktop.backend import StudioBackend
from santhau_studio.storage import serving as snapshot_store
from santhau_studio.storage.rawstore import RawObjectStore
from santhau_studio.storage.serving import build_serving_db
from santhau_studio.storage.warehouse import WarehouseWriter
from santhau_studio.workers.jobs import WorkerController, WorkerLoop, claim_next_job, enqueue_job


def test_ping_exposes_recovery_snapshot_and_idle_phases(tmp_path, monkeypatch):
    loop = WorkerLoop(tmp_path, idle_exit_seconds=.01)
    controller = WorkerController(loop)
    phases = []
    recover = RawObjectStore.recover
    build = snapshot_store.build_serving_db
    def recovering(store, conn):
        phases.append(controller({"op": "ping"}, "test")["runtime"]["phase"])
        return recover(store, conn)
    def building(*args):
        phases.append(controller({"op": "ping"}, "test")["runtime"]["phase"])
        return build(*args)
    monkeypatch.setattr(RawObjectStore, "recover", recovering)
    monkeypatch.setattr(snapshot_store, "build_serving_db", building)
    loop.run()
    assert loop.exit_code == 0, loop.last_error
    assert phases == ["RECOVERING", "PUBLISHING_SNAPSHOT"]
    assert controller({"op": "ping"}, "test")["runtime"]["phase"] == "STOPPED"


def test_live_running_state_overrides_interrupted_snapshot_and_clears_old_result(tmp_path, monkeypatch):
    request = job_request()
    enqueue_job(tmp_path, request)
    assert claim_next_job(tmp_path) == request
    with closing(WarehouseWriter.open(tmp_path, create_schema=True)) as writer:
        writer.register_run("old", request.job_id, "BACKFILL")
        writer.finish_run("old", "INTERRUPTED")
        assert build_serving_db(tmp_path / "warehouse.sqlite", tmp_path / "serving.sqlite").published
        writer.register_run("new", request.job_id, "BACKFILL")
        loop = WorkerLoop(tmp_path)
        loop._current = request
        controller = WorkerController(loop)
        backend = StudioBackend(tmp_path)
        monkeypatch.setattr(backend, "worker_status", lambda: {
            "running": True, "pipe_ok": True, "runtime": {"phase": "COLLECTING", "current_job_id": request.job_id}})
        monkeypatch.setattr(backend, "job_status", lambda job: controller({"op": "status", "job_id": job}, "test"))
        try:
            result = backend.jobs()
            assert result["active"][0]["lifecycle"] == "RUNNING"
            assert result["active"][0]["status"] is None
            assert result["active"][0]["live_confirmed"]
            assert result["counts"] == {"running": 1, "paused": 0, "queued": 0, "done": 0, "unknown": 0}
        finally:
            controller.close()


def test_worker_offline_does_not_claim_old_active_run_is_still_collecting(tmp_path, monkeypatch):
    request = job_request()
    enqueue_job(tmp_path, request)
    claim_next_job(tmp_path)
    with closing(WarehouseWriter.open(tmp_path, create_schema=True)) as writer:
        writer.register_run("old", request.job_id, "BACKFILL")
        assert build_serving_db(tmp_path / "warehouse.sqlite", tmp_path / "serving.sqlite").published
    backend = StudioBackend(tmp_path)
    monkeypatch.setattr(backend, "worker_status", lambda: {"running": False, "pipe_ok": False})
    result = backend.jobs()
    assert result["counts"]["running"] == 0
    assert result["active"][0]["display_state"] == "WORKER_STOPPED"


def test_command_without_running_target_is_refused_instead_of_pausing_future_job(tmp_path):
    controller = WorkerController(WorkerLoop(tmp_path))
    for command in ("PAUSE", "STOP_GRACEFUL"):
        response = controller({"op": "command", "command": command}, "test")
        assert not response["ok"]
    assert not controller.loop.flag.interrupt_requested


def test_resume_is_not_accepted_twice_before_queue_is_drained(tmp_path):
    loop = WorkerLoop(tmp_path)
    loop._paused_jobs.add("paused")
    controller = WorkerController(loop)
    payload = {"op": "command", "command": "RESUME", "job_id": "paused"}
    assert controller(payload, "test")["ok"]
    assert not controller(payload, "test")["ok"]


def test_progress_has_last_page_timestamp_and_pending_pause_phase(tmp_path):
    request = job_request()
    enqueue_job(tmp_path, request)
    loop = WorkerLoop(tmp_path, idle_exit_seconds=.01)
    controller = WorkerController(loop)
    normal = portal_builder(2, 5)
    seen = []
    def builder(writer, req):
        pages = normal(writer, req)
        def stream(*args):
            for page in pages(*args):
                yield page
                state = controller({"op": "ping"}, "test")["runtime"]
                seen.append(state)
                loop.post_command(job_id=req.job_id, command="PAUSE", sid="test")
                seen.append(controller({"op": "ping"}, "test")["runtime"])
        return stream
    loop._builder = builder
    loop.run()
    assert loop.exit_code == 0, loop.last_error
    assert seen[0]["phase"] == "COLLECTING" and seen[0]["progress"]["updated_at"]
    assert seen[1]["phase"] == "PAUSING"


def test_bad_queue_document_does_not_hide_worker_status(tmp_path, monkeypatch):
    queued = tmp_path / "jobs" / "queued"
    queued.mkdir(parents=True)
    (queued / "bad.json").write_text("{broken", encoding="utf-8")
    backend = StudioBackend(tmp_path)
    monkeypatch.setattr(backend, "worker_status", lambda: {"running": False, "pipe_ok": False})
    result = backend.jobs()
    assert result["queued"][0]["display_state"] == "UNREADABLE"
    assert result["worker"]["running"] is False
