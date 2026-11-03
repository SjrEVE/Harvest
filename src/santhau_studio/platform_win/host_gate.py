"""A per-user host lease shared by collector processes, including separate Data Roots."""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from santhau_studio.platform_win.os_lock import DataRootLock


class HostRequestGate:
    def __init__(self, host: str, interval: float) -> None:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        root = base / "SanThauDataStudio" / "network" / hashlib.sha256(host.lower().encode()).hexdigest()[:24]
        self.lock = DataRootLock(root, role="host-request")
        self.state = root / "next-request.txt"
        self.delay = max(0.0, interval)

    def __enter__(self):
        self.lock.acquire(retries=1200, retry_delay_seconds=0.05)
        try:
            try:
                next_request = float(self.state.read_text(encoding="ascii"))
            except (OSError, ValueError):
                next_request = 0.0
            remaining = next_request - time.time()
            if remaining > 0:
                time.sleep(remaining)
        except BaseException:
            self.lock.release()
            raise
        return self

    def defer(self, seconds: float | None) -> None:
        self.delay = max(self.delay, seconds or 0.0)

    def __exit__(self, *_exc: object) -> None:
        try:
            with self.state.open("w", encoding="ascii") as handle:
                handle.write(str(time.time() + self.delay))
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            self.lock.release()
