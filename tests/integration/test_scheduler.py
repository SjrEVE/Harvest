"""Task Scheduler opt-in (§2.3) and scheduled-worker behaviour.

The XML assertions prove what would be registered before anything touches the machine; the live
create/delete test is gated behind `SANTHAU_SCHEDULER_TEST=1` because it writes a real task into
the user's scheduler. The scheduled-run tests prove the two runtime rules: a busy root is a clean
skip (not a failure), and an idle scheduled run exits so the next trigger is not swallowed by
`IgnoreNew`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from santhau_studio.collectors.source_registry import SOURCE_EGP_HOME, SOURCES
from santhau_studio.platform_win.scheduler import (
    TASK_PREFIX,
    create,
    delete,
    parse_plan,
    plan,
    query,
    task_name,
)
from santhau_studio.workers.jobs import JobRequest, WorkerLoop, enqueue_job

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Task Scheduler is Windows-only")

REPO = Path(__file__).resolve().parents[2]


def _tag(element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _find_all(element, name: str) -> list:
    return [node for node in element.iter() if _tag(node) == name]


def test_plan_registers_two_daily_triggers_with_ignore_new(tmp_path: Path) -> None:
    schedule = plan(tmp_path / "data", REPO / "SanThauDataStudio.exe")
    tree = parse_plan(schedule.xml)
    starts = [node.text for node in _find_all(tree, "StartBoundary")]
    assert starts == ["2026-01-01T08:00:00", "2026-01-01T20:00:00"]
    policies = [node.text for node in _find_all(tree, "MultipleInstancesPolicy")]
    assert policies == ["IgnoreNew"]
    arguments = _find_all(tree, "Arguments")[0].text
    assert "--scheduled" in arguments and "--profile default" in arguments
    assert "--idle-exit-seconds" in arguments
    assert schedule.task_name.startswith(TASK_PREFIX)
    assert schedule.task_name == task_name(tmp_path / "data")


def test_task_name_is_scoped_to_the_data_root(tmp_path: Path) -> None:
    one = task_name(tmp_path / "root-a")
    two = task_name(tmp_path / "root-b")
    assert one != two
    assert one == task_name(tmp_path / "root-a")


@pytest.mark.skipif(
    os.environ.get("SANTHAU_SCHEDULER_TEST") != "1",
    reason="writes a real scheduled task; set SANTHAU_SCHEDULER_TEST=1 to exercise it",
)
def test_create_query_delete_roundtrip(tmp_path: Path) -> None:
    executable = REPO / "build" / "stage" / "SanThauCollector.exe"
    if not executable.exists():
        pytest.skip("packaged collector not built")
    root = tmp_path / "data"
    root.mkdir(parents=True)
    try:
        created = create(root, executable)
        assert created.task_name
        assert query(root)["present"] is True
    finally:
        assert delete(root) is True
    assert query(root)["present"] is False


def test_idle_worker_exits_on_its_own(tmp_path: Path) -> None:
    root = tmp_path / "data"
    loop = WorkerLoop(root, idle_exit_seconds=0.3)
    thread = threading.Thread(target=loop.run, daemon=True)
    started = time.monotonic()
    thread.start()
    thread.join(timeout=20)
    assert not thread.is_alive(), "idle scheduled worker must exit"
    assert loop.exit_code == 0
    assert time.monotonic() - started < 15


def test_scheduled_worker_finishes_the_queue_then_exits(tmp_path: Path) -> None:
    root = tmp_path / "data"
    request = JobRequest(
        job_id="job-sched-1",
        job_kind="DETAIL",
        requested_by="test",
        sources=({"source_key": SOURCE_EGP_HOME, "family": "TBMT",
                  "endpoint": SOURCES[SOURCE_EGP_HOME].endpoint, "adapter_sha": "a" * 64},),
        scope={"from": "2026-11-01T00:00:00Z", "to": "2026-11-02T00:00:00Z"},
        created_at="2026-11-03T00:00:00Z",
    )
    enqueue_job(root, request)
    loop = WorkerLoop(root, idle_exit_seconds=0.5)
    thread = threading.Thread(target=loop.run, daemon=True)
    thread.start()
    thread.join(timeout=30)
    assert not thread.is_alive()
    from santhau_studio.storage.warehouse import connect_read_only

    conn = connect_read_only(root / "warehouse.sqlite")
    try:
        row = conn.execute("SELECT lifecycle, status FROM runs WHERE job_id=?", (request.job_id,)).fetchone()
    finally:
        conn.close()
    assert row["lifecycle"] == "TERMINATED" and row["status"] == "BLOCKED"


def test_scheduled_launch_on_a_busy_root_exits_zero(tmp_path: Path) -> None:
    root = tmp_path / "data"
    holder = WorkerLoop(root, page_source_builder=lambda _writer, _request: None)
    stop = threading.Event()
    thread = threading.Thread(target=holder.run, args=(stop,), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and holder._writer is None:
            time.sleep(0.05)
        assert holder._writer is not None, "holder worker must own the root"
        result = subprocess.run(
            [sys.executable, "-m", "santhau_studio.workers.service",
             "--data-root", str(root), "--scheduled", "--profile", "default", "--idle-exit-seconds", "1"],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "PYTHONPATH": str(REPO / "src")},
            cwd=str(REPO),
        )
        assert result.returncode == 0, result.stderr
    finally:
        stop.set()
        thread.join(timeout=20)
