"""Different workers must share pacing and hold one host request lease."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


def test_host_lease_serializes_processes_and_preserves_cooldown(tmp_path: Path, monkeypatch) -> None:
    from santhau_studio.platform_win.host_gate import HostRequestGate

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    script = (
        "import time\n"
        "from santhau_studio.platform_win.host_gate import HostRequestGate\n"
        "with HostRequestGate('test.example', 0.1):\n"
        " print('held', flush=True)\n"
        " time.sleep(0.4)\n"
    )
    process = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True,
                               env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")})
    try:
        assert process.stdout.readline().strip() == "held"
        started = time.monotonic()
        with HostRequestGate("test.example", 0.1):
            assert time.monotonic() - started >= 0.3
        started = time.monotonic()
        with HostRequestGate("test.example", 0.1):
            assert time.monotonic() - started >= 0.07
    finally:
        process.wait(timeout=5)
