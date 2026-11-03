"""Milestone G: the GUI starts a real worker process, attaches instead of duplicating, and stops it.

This is the packaged-lifecycle contract of §2.3 exercised in dev form: detached spawn, lock-based
attach, pipe health, and FORCE_TERMINATE returning the process to idle. Windows-only because the
worker lock and the pipe are.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import time
from pathlib import Path

import pytest

from santhau_studio.desktop.backend import StudioBackend
from santhau_studio.desktop.supervisor import WorkerSupervisor, collector_command

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="worker lock and pipe are Windows-only")


def wait_for(predicate, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    return predicate()


def _kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)


def test_collector_command_targets_the_real_worker() -> None:
    command = collector_command()
    assert command, "collector command must resolve in a source tree"
    assert "workers.service" in " ".join(command) or command[0].endswith("SanThauCollector.exe")


def test_gui_spawns_worker_attaches_and_terminates(tmp_path: Path) -> None:
    root = tmp_path / "data"
    supervisor = WorkerSupervisor(root)
    backend = StudioBackend(root)
    spawned = supervisor.ensure_running()
    assert spawned["ok"] and spawned["spawned"], spawned
    holder_pid = None
    try:
        assert wait_for(lambda: backend.worker_status()["running"], timeout=30), "worker never took the lock"
        holder_pid = backend.worker_status()["pid"]
        assert wait_for(lambda: backend.worker_status()["pipe_ok"], timeout=15), "worker pipe never answered"

        second = supervisor.ensure_running()
        assert second["ok"] and second["spawned"] is False and second["attached"] is True

        assert backend.worker_command("FORCE_TERMINATE", None)["ok"] is True
        assert wait_for(lambda: not backend.worker_status()["running"], timeout=20), "worker did not stop"
    finally:
        status = backend.worker_status()
        if status["running"] and status["pid"]:
            _kill_tree(int(status["pid"]))
        elif holder_pid:
            with contextlib.suppress(Exception):
                _kill_tree(int(holder_pid))
