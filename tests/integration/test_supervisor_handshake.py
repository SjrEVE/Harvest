"""F12: Popen is not readiness. No duplicate spawns, no false ok on early exit."""
import pytest

from santhau_studio.desktop import supervisor as sup
from santhau_studio.desktop.backend import StudioBackend


class FakeProcess:
    def __init__(self, exit_code=None):
        self.pid = 424242
        self.returncode = exit_code
        self._exit_code = exit_code

    def poll(self):
        return self._exit_code


def test_spawn_reports_failure_when_child_exits_immediately(tmp_path, monkeypatch):
    monkeypatch.setattr(sup, "collector_command", lambda: ["fake-collector"])
    monkeypatch.setattr(sup.subprocess, "Popen", lambda *_a, **_k: FakeProcess(exit_code=3))
    monkeypatch.setattr(
        StudioBackend, "worker_status",
        lambda _self: {"running": False, "pipe_ok": False},
    )
    result = sup.WorkerSupervisor(tmp_path).ensure_running(timeout=1, poll_interval=0.01)
    assert result["ok"] is False
    assert result["spawned"] is True
    assert result["phase"] == "FAILED"
    assert "WORKER_EXITED_EARLY" in result["error"]


def test_spawn_waits_for_ipc_before_reporting_ready(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sup, "collector_command", lambda: ["fake-collector"])
    monkeypatch.setattr(sup.subprocess, "Popen", lambda *_a, **_k: FakeProcess())

    def status(_self):
        calls.append(1)
        if len(calls) == 1:
            return {"running": False, "pipe_ok": False}
        if len(calls) < 4:
            return {"running": True, "pipe_ok": False, "pid": 424242}
        return {"running": True, "pipe_ok": True, "pid": 424242}

    monkeypatch.setattr(StudioBackend, "worker_status", status)
    sup_obj = sup.WorkerSupervisor(tmp_path)
    calls.clear()
    result = sup_obj.ensure_running(timeout=2, poll_interval=0.01)
    assert result["ok"] is True and result["phase"] == "READY"
    assert len(calls) >= 4, "must poll IPC, not report ok immediately after Popen"


def test_attach_never_spawns_a_second_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(sup, "collector_command", lambda: ["fake-collector"])
    monkeypatch.setattr(
        sup.subprocess, "Popen",
        lambda *_a, **_k: pytest.fail("must not spawn a second worker"),
    )
    monkeypatch.setattr(
        StudioBackend, "worker_status",
        lambda _self: {"running": True, "pipe_ok": True, "pid": 7},
    )
    result = sup.WorkerSupervisor(tmp_path).ensure_running(timeout=1, poll_interval=0.01)
    assert result["ok"] and result["spawned"] is False and result["attached"] is True
    assert result["phase"] == "READY"
