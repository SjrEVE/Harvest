"""One GUI per data root (RC2-D): named mutex, released by process death.

The worker is protected by the data-root lock; this is the GUI-side counterpart, and the reason
uninstall can close the app through Restart Manager instead of leaving locked DLLs behind.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from santhau_studio.platform_win.process import acquire_single_instance, single_instance_name

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32 mutex")

REPO = Path(__file__).resolve().parents[2]


def test_name_is_scoped_to_the_data_root(tmp_path: Path) -> None:
    one = single_instance_name(str(tmp_path / "a"))
    two = single_instance_name(str(tmp_path / "b"))
    assert one != two
    assert one.startswith("Local\\SanThauDataStudio-")


def test_second_holder_is_refused_until_the_first_exits(tmp_path: Path) -> None:
    name = single_instance_name(str(tmp_path / "root"))
    holder_code = (
        "import sys, time\n"
        f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
        "from santhau_studio.platform_win.process import acquire_single_instance\n"
        f"assert acquire_single_instance({name!r})\n"
        "print('held', flush=True)\n"
        "time.sleep(30)\n"
    )
    holder = subprocess.Popen([sys.executable, "-c", holder_code],
                              stdout=subprocess.PIPE, text=True,
                              env={**os.environ, "PYTHONPATH": str(REPO / "src")})
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        assert acquire_single_instance(name) is False, "a live holder must refuse the second GUI"
    finally:
        holder.kill()
        holder.wait(timeout=10)
    deadline = time.monotonic() + 10
    acquired = False
    while time.monotonic() < deadline:
        if acquire_single_instance(name):
            acquired = True
            break
        time.sleep(0.1)
    assert acquired, "the mutex must be released when the holding process dies"
