"""FAULT-06: two workers on one data root — exactly one may write.

The lock is an OS byte-range lock, so the interesting cases are all cross-process: a holder
that is alive, one that stops politely, and one that is killed. The last two are the reason
this is not a "lock file with a pid in it" design: nothing has to be cleaned up after a crash.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from santhau_studio.platform_win.os_lock import DataRootBusy, DataRootLock, holder_info
from santhau_studio.storage.warehouse import WarehouseWriter

SRC_DIR = Path(__file__).resolve().parents[2] / "src"

HOLDER_SCRIPT = """
import os, sys, time
from pathlib import Path
from santhau_studio.platform_win.os_lock import DataRootLock

root, ready, hold_seconds = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3])
lock = DataRootLock(root, role="worker").acquire()
ready.write_text(str(os.getpid()))
time.sleep(hold_seconds)
"""


@pytest.fixture()
def holder(tmp_path: Path):
    script = tmp_path / "holder.py"
    script.write_text(HOLDER_SCRIPT, encoding="utf-8")
    root = tmp_path / "root"
    env = dict(os.environ, PYTHONPATH=str(SRC_DIR))

    def _start(hold_seconds: float = 8.0) -> subprocess.Popen:
        ready = tmp_path / "ready"
        ready.unlink(missing_ok=True)
        proc = subprocess.Popen(
            [sys.executable, str(script), str(root), str(ready), str(hold_seconds)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        deadline = time.time() + 20
        while not ready.exists():
            if proc.poll() is not None or time.time() > deadline:
                stderr = proc.communicate(timeout=5)[1]
                raise AssertionError(f"holder died before taking the lock: {stderr}")
            time.sleep(0.05)
        return proc

    yield _start, root


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        if sys.platform == "win32":
            # sys.executable here is the venv redirector: it re-execs the real interpreter,
            # and killing only the parent leaves the lock holder alive. Taskkill /T is the
            # honest version of "the worker was killed", which is what FAULT-06 has to show.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                text=True,
                check=False,
            )
        else:
            proc.kill()
    proc.wait(timeout=20)


# ------------------------------------------------------------------ same process

def test_second_acquire_in_the_same_process_is_refused(tmp_path: Path) -> None:
    """Windows byte-range locks are per handle, so an accidental double-open is caught too."""
    first = DataRootLock(tmp_path, role="worker").acquire()
    with pytest.raises(DataRootBusy, match="pid"):
        DataRootLock(tmp_path, role="ui").acquire()
    first.release()
    assert not first.held
    assert DataRootLock(tmp_path, role="worker").acquire().held


def test_release_is_idempotent(tmp_path: Path) -> None:
    """A stop path that runs release twice must not raise while unwinding."""
    lock = DataRootLock(tmp_path, role="worker").acquire()
    lock.release()
    lock.release()
    assert DataRootLock(tmp_path, role="worker").acquire().held


def test_context_manager_releases_on_exception(tmp_path: Path) -> None:
    with pytest.raises(ValueError), DataRootLock(tmp_path, role="worker"):
        raise ValueError("boom")
    assert DataRootLock(tmp_path, role="worker").acquire().held


def test_holder_record_names_pid_role_and_machine(tmp_path: Path) -> None:
    lock = DataRootLock(tmp_path, role="reconcile").acquire()
    try:
        info = holder_info(lock.path)
        assert info["pid"] == os.getpid()
        assert info["role"] == "reconcile"
        assert {"machine", "acquired_at"} <= set(info)
    finally:
        lock.release()


# ------------------------------------------------------------------ cross process

def test_running_holder_is_named_by_pid(holder) -> None:
    start, root = holder
    proc = start(8.0)
    try:
        with pytest.raises(DataRootBusy) as excinfo:
            DataRootLock(root, role="worker").acquire()
        # The child reports its own pid after taking the lock; the refusal must name that pid,
        # not merely contain a number. Popen.pid is the venv redirector here — the real
        # interpreter is a child of it — so the record's pid is the one an operator must kill.
        claimed = (root.parent / "ready").read_text()
        assert f"pid {claimed}" in str(excinfo.value), "the operator must be told who holds it"
        assert "role worker" in str(excinfo.value)
    finally:
        _kill(proc)


def test_polite_exit_frees_the_root_for_the_next_process(holder) -> None:
    start, root = holder
    proc = start(0.2)
    proc.wait(timeout=20)
    lock = DataRootLock(root, role="worker").acquire(retries=40, retry_delay_seconds=0.05)
    assert lock.held
    lock.release()


def test_killed_holder_leaves_no_stale_lock_to_clear(holder) -> None:
    """The whole point of an OS lock: the kernel drops it when the handle dies."""
    start, root = holder
    proc = start(60.0)
    with pytest.raises(DataRootBusy):
        DataRootLock(root, role="worker").acquire()
    _kill(proc)
    lock_path = root / "writer.lock"
    assert lock_path.exists(), "the inert file stays; only the lock is gone"
    assert DataRootLock(root, role="worker").acquire().held


def test_retries_bridge_a_worker_that_is_only_stopping(holder) -> None:
    """PAUSE then RESUME: a stop takes milliseconds, so a bounded wait beats an error dialog."""
    start, root = holder
    proc = start(0.4)
    try:
        lock = DataRootLock(root, role="worker").acquire(retries=80, retry_delay_seconds=0.05)
        assert lock.held
        lock.release()
    finally:
        _kill(proc)


def test_torn_lock_file_still_names_the_contender(holder) -> None:
    """A previous holder can die before writing its record; refusing must still be explained."""
    start, root = holder
    proc = start(8.0)
    try:
        (root / "writer.lock").write_text("torn", encoding="utf-8")
        with pytest.raises(DataRootBusy, match="unknown process"):
            DataRootLock(root, role="worker").acquire()
    finally:
        _kill(proc)


def test_missing_lock_directory_is_created(tmp_path: Path) -> None:
    root = tmp_path / "deep" / "data-root"
    assert DataRootLock(root, role="worker").acquire().held
    assert (root / "writer.lock").exists()


# ------------------------------------------------------------------ wiring into the writer

def test_writer_open_takes_the_lock_and_close_releases_it(tmp_path: Path) -> None:
    """Guard reachability: the lock only matters because the production writer holds it."""
    writer = WarehouseWriter.open(tmp_path, create_schema=True, role="backfill")
    try:
        assert (tmp_path / "writer.lock").exists()
        assert writer._lock is not None and writer._lock.held
        with pytest.raises(DataRootBusy):
            WarehouseWriter.open(tmp_path, role="worker")
    finally:
        writer.close()
    reopened = WarehouseWriter.open(tmp_path, role="worker")
    assert reopened._lock.held
    reopened.close()


def test_writer_open_releases_the_lock_when_bootstrap_fails(tmp_path: Path) -> None:
    """A failed open must not leave the data root locked for everyone else."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "warehouse.sqlite").write_bytes(b"not a database")
    with pytest.raises(Exception, match="not a database"):
        WarehouseWriter.open(root, create_schema=True)
    assert DataRootLock(root, role="worker").acquire().held


def test_writer_created_without_open_has_no_lock(tmp_path: Path) -> None:
    """The bare constructor stays available for tests, and must not claim to enforce anything."""
    from santhau_studio.storage.rawstore import RawObjectStore
    from santhau_studio.storage.warehouse import connect

    writer = WarehouseWriter(connect(tmp_path / "warehouse.sqlite"), RawObjectStore(tmp_path))
    assert writer._lock is None
    writer.close()
