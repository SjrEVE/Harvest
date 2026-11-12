"""Worker lifecycle the GUI controls (§2.3): spawn if absent, attach if present, never duplicate.

Spawning is explicit (UI-04 button, or a future first-run flow) rather than on GUI start: the
data root is the user's choice in UI-01, and a worker started against a default root before that
choice would be a side effect nobody asked for.

The child is detached on purpose — closing the GUI must not kill collection (FAULT-12). The
worker holds the data-root lock for its lifetime, so a second spawn is refused by the worker
itself (`DataRootBusy`), and this class checks the lock first anyway.

RC3-5 / F12: a successful spawn is only reported after a bounded IPC handshake. Popen alone
means "start requested", not "ready"; an early child exit or a timed-out pipe is a failure.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from santhau_studio.desktop.backend import StudioBackend

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200

# Two GUI clicks must never race into two Popen calls before either sees the lock.
_START_LOCK = threading.Lock()


def collector_command() -> list[str] | None:
    """Sibling executable in a packaged install, module entry in a source tree."""
    sibling = Path(sys.executable).parent / "SanThauCollector.exe"
    if sibling.exists():
        return [str(sibling)]
    if (Path(__file__).resolve().parents[1] / "workers" / "service.py").exists():
        return [sys.executable, "-m", "santhau_studio.workers.service"]
    return None


def _launch_env() -> dict[str, str]:
    env = dict(os.environ)
    source_root = Path(__file__).resolve().parents[2]  # <repo>/src in a source tree
    if (source_root / "santhau_studio").is_dir():
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join(part for part in (str(source_root), existing) if part)
    return env


class WorkerSupervisor:
    def __init__(self, data_root: Path | str) -> None:
        self.backend = StudioBackend(data_root)

    def ensure_running(self, *, timeout: float = 30.0, poll_interval: float = 0.1) -> dict:
        """Attach to a ready worker or spawn one and wait for its IPC handshake."""
        from santhau_studio.collectors.offline_acceptance import enabled

        if enabled(self.backend.data_root) and os.environ.get(
            "SANTHAU_OFFLINE_ACCEPTANCE_ROOT"
        ) != str(self.backend.data_root.resolve()):
            return {
                "ok": False,
                "phase": "FAILED",
                "error": "SYNTHETIC_ROOT: mở bằng profile offline rõ ràng; không được chạy live trên kho thử nghiệm.",
            }
        with _START_LOCK:
            status = self.backend.worker_status()
            if status.get("running"):
                # Never spawn a second worker against a root that already has one.
                ready = self._wait_for_pipe(None, timeout, poll_interval)
                if isinstance(ready, str):
                    return {
                        "ok": False, "spawned": False, "attached": True,
                        "phase": "STARTING", "error": ready, "worker": status,
                    }
                return {
                    "ok": True, "spawned": False, "attached": True,
                    "phase": "READY", "worker": ready,
                }
            command = collector_command()
            if command is None:
                return {
                    "ok": False, "spawned": False, "phase": "FAILED",
                    "error": "không tìm thấy SanThauCollector.exe",
                }
            try:
                process = subprocess.Popen(
                    [*command, "--data-root", str(self.backend.data_root)],
                    creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
                    close_fds=True,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=_launch_env(),
                    cwd=str(self.backend.data_root.parent if self.backend.data_root.parent.exists() else Path.cwd()),
                )
            except OSError as exc:
                return {
                    "ok": False, "spawned": False, "phase": "FAILED",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            ready = self._wait_for_pipe(process, timeout, poll_interval)
            if isinstance(ready, str):
                return {
                    "ok": False, "spawned": True, "phase": "FAILED",
                    "error": ready, "pid": process.pid,
                }
            return {
                "ok": True, "spawned": True, "phase": "READY",
                "pid": process.pid, "worker": ready,
            }

    def _wait_for_pipe(self, process, timeout: float, interval: float):
        """Return a ready status dict, or an error string. Never claims ready on Popen."""
        deadline = time.monotonic() + timeout
        while True:
            if process is not None and process.poll() is not None:
                return f"WORKER_EXITED_EARLY: child exited with code {process.returncode}"
            status = self.backend.worker_status()
            if status.get("pipe_ok"):
                return status
            if time.monotonic() >= deadline:
                return f"WORKER_NOT_READY: IPC handshake timed out after {timeout:g}s"
            time.sleep(interval)
