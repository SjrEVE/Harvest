"""Worker lifecycle the GUI controls (§2.3): spawn if absent, attach if present, never duplicate.

Spawning is explicit (UI-04 button, or a future first-run flow) rather than on GUI start: the
data root is the user's choice in UI-01, and a worker started against a default root before that
choice would be a side effect nobody asked for.

The child is detached on purpose — closing the GUI must not kill collection (FAULT-12). The
worker holds the data-root lock for its lifetime, so a second spawn is refused by the worker
itself (`DataRootBusy`), and this class checks the lock first anyway.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from santhau_studio.desktop.backend import StudioBackend

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


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

    def ensure_running(self) -> dict:
        from santhau_studio.collectors.offline_acceptance import enabled
        if enabled(self.backend.data_root) and os.environ.get('SANTHAU_OFFLINE_ACCEPTANCE_ROOT') != str(self.backend.data_root.resolve()):
            return {'ok':False,'error':'SYNTHETIC_ROOT: mở bằng profile offline rõ ràng; không được chạy live trên kho thử nghiệm.'}
        status = self.backend.worker_status()
        if status["running"]:
            return {"ok": True, "spawned": False, "attached": True, "worker": status}
        command = collector_command()
        if command is None:
            return {"ok": False, "spawned": False, "error": "không tìm thấy SanThauCollector.exe"}
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
            return {"ok": False, "spawned": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "spawned": True, "pid": process.pid, "worker": self.backend.worker_status()}
