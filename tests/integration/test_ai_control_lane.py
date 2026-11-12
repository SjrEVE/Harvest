"""AI Control Lane: a read-only state channel that never blocks on the worker.

Regression for the "blind agent" failure: IPC ping died with PipeClosedError while
the worker rebuilt serving.sqlite. This lane must answer in <50ms regardless.
"""
import sys
import threading
import time

import pytest

from santhau_studio.workers.ipc import worker_pipe_name


@pytest.fixture(autouse=True)
def _fresh(tmp_path):
    from santhau_studio.workers import ai_control

    ai_control.configure(tmp_path)
    yield
    ai_control.configure(tmp_path)


def test_state_answers_while_rebuild_is_blocked():
    from santhau_studio.workers import ai_control

    ai_control.emit("PUBLISHING_SNAPSHOT", serving={"rebuilding": True})
    entered, release = threading.Event(), threading.Event()

    def blocked_build():
        entered.set()
        release.wait(5)  # stand-in for the 2.2 GB build_serving_db

    t = threading.Thread(target=blocked_build, daemon=True)
    t.start()
    assert entered.wait(1)
    try:
        started = time.perf_counter()
        reply = ai_control.handle({"op": "state"})
        elapsed = time.perf_counter() - started
        assert elapsed < 0.05, f"state handler blocked for {elapsed:.3f}s"
        assert reply["phase"] == "PUBLISHING_SNAPSHOT"
        assert reply["serving"]["rebuilding"] is True
        assert reply["stale"] is False
    finally:
        release.set()
        t.join(timeout=2)


def test_events_are_bounded_and_monotonic():
    from santhau_studio.workers import ai_control

    for i in range(300):
        ai_control.emit("COLLECTING", progress={"done": i})
    reply = ai_control.handle({"op": "events"})
    events = reply["events"]
    assert len(events) <= 200
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert events[-1]["phase"] == "COLLECTING"


def test_initial_state_is_valid_without_any_worker():
    from santhau_studio.workers import ai_control

    reply = ai_control.handle({"op": "state"})
    assert reply["seq"] == 0
    assert reply["phase"] in ("STARTING", "UNKNOWN")
    assert reply["worker_pid"] is None
    assert reply["stale"] is True  # nothing has published yet


def test_ai_pipe_name_is_separate_from_control_pipe():
    from santhau_studio.workers.ai_control import ai_pipe_name

    name = ai_pipe_name(data_root="D:/Harvest/SanThauData")
    assert name.endswith("-ai")
    assert name != worker_pipe_name(data_root="D:/Harvest/SanThauData")


@pytest.mark.skipif(sys.platform != "win32", reason="named pipe is Windows-only")
def test_ai_pipe_answers_over_a_real_named_pipe(tmp_path):
    from santhau_studio.workers import ai_control
    from santhau_studio.workers.ipc import PipeClient

    ai_control.emit("IDLE", worker_pid=4242)
    stop = threading.Event()
    server = ai_control.AIControlServer(data_root=tmp_path)
    thread = threading.Thread(target=server.serve_until, args=(stop,), daemon=True)
    thread.start()
    try:
        client = PipeClient.connect(ai_control.ai_pipe_name(data_root=tmp_path), timeout_seconds=5)
        try:
            reply = client.request({"op": "state"})
        finally:
            client.close()
        assert reply["phase"] == "IDLE"
        assert reply["worker_pid"] == 4242
    finally:
        stop.set()
        server.wake()
        thread.join(timeout=3)


def test_build_serving_db_reports_rebuild_to_ai_lane(tmp_path, monkeypatch):
    """The 2.2 GB rebuild is exactly when the old IPC went deaf; it must emit."""
    import sqlite3

    from santhau_studio.storage import serving
    from santhau_studio.workers import ai_control

    ai_control.configure(tmp_path)
    calls = []
    real_emit = ai_control.emit

    def spy(phase, **kw):
        calls.append((phase, kw))
        return real_emit(phase, **kw)

    monkeypatch.setattr(serving.ai_control, "emit", spy)
    monkeypatch.setattr(
        "santhau_studio.storage.read_model.materialise_records",
        lambda *_a, **_k: None,
    )
    warehouse = tmp_path / "warehouse.sqlite"
    sqlite3.connect(warehouse).close()
    serving.build_serving_db(warehouse, tmp_path / "serving.sqlite")

    assert calls, "build_serving_db emitted nothing to the AI lane"
    assert calls[0][0] == "PUBLISHING_SNAPSHOT"
    assert calls[0][1]["serving"]["rebuilding"] is True
    assert any(phase == "IDLE" and kw["serving"]["rebuilding"] is False
               for phase, kw in calls), "rebuild finish was not reported"
