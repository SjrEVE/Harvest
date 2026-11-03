"""Single-writer enforcement for one data root (§5.2, FAULT-06).

SQLite in WAL mode allows one writer process, but it enforces that through the database file
only while *its* handle is open. The raw object store, the staging directory, the serving
snapshot and the backup writer are outside SQLite's reach, so the invariant "exactly one
process writes this data root" needs an OS-level lock of its own.

The lock is an exclusive byte-range lock on `writer.lock` inside the data root:

* the OS releases it when the handle closes, including when the process is killed or the
  machine reboots — there is no stale lock file to reason about, only an inert file;
* the locked byte sits well past end-of-file (`LOCK_OFFSET`) and is never written. Windows
  byte-range locks are *mandatory*: with byte 0 locked, the holder's own record cannot be read
  through any other handle, so a refused process could never name its competitor. Locking at
  1 MiB keeps that record readable;
* `msvcrt.locking` is per *handle*, not per process, so a second `acquire()` in the same
  process also fails. That is deliberate: the UI never takes this lock, it reads the serving
  snapshot.

`msvcrt.locking` acts relative to the handle's file position, so locking and unlocking each
pin the offset themselves — unlocking from the position a write left behind fails outright.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Self

LOCK_SUFFIX = ".lock"
LOCK_OFFSET = 1 << 20

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> None:
        os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _release_lock(fd: int) -> None:
        os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _release_lock(fd: int) -> None:
        del fd  # flock is whole-file and position-free; closing the descriptor releases it


class DataRootBusy(RuntimeError):
    """Another process already writes this data root."""


@dataclass(frozen=True)
class LockHolder:
    pid: int
    role: str
    machine: str
    acquired_at: float

    def as_json(self) -> str:
        return json.dumps(
            {"pid": self.pid, "role": self.role, "machine": self.machine, "acquired_at": self.acquired_at},
            sort_keys=True,
        )


class DataRootLock:
    """Owns the writer lock for one data root for the lifetime of one process."""

    def __init__(self, data_root: Path, *, role: str = "worker") -> None:
        self.root = Path(data_root)
        self.role = role
        self.path = self.root / f"writer{LOCK_SUFFIX}"
        self._fd: int | None = None
        self.holder: LockHolder | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self, *, retries: int = 0, retry_delay_seconds: float = 0.2) -> Self:
        """Take the exclusive lock. Raises DataRootBusy naming the current holder.

        `retries` exists for the pause/resume path, where a stopping worker releases within a
        few hundred milliseconds; it is bounded so a wedged worker gets reported rather than
        waited out.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        attempts = max(1, retries + 1)
        for attempt in range(attempts):
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
            try:
                _try_lock(fd)
            except OSError:
                os.close(fd)
                if attempt + 1 < attempts:
                    time.sleep(retry_delay_seconds)
                    continue
                raise DataRootBusy(f"{self.path} is held by {_describe_holder(self.path)}") from None
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                os.ftruncate(fd, 0)
                holder = LockHolder(os.getpid(), self.role, socket.gethostname() or "local", time.time())
                os.write(fd, holder.as_json().encode("utf-8"))
                os.fsync(fd)
            except OSError:
                _release_lock(fd)
                os.close(fd)
                raise
            self._fd = fd
            self.holder = holder
            return self

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            _release_lock(self._fd)
        finally:
            os.close(self._fd)
            self._fd = None
            self.holder = None

    def __enter__(self) -> Self:
        if not self.held:
            self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


def holder_info(path: Path) -> dict | None:
    """Read the current holder's record without contending for the lock."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.strip():
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _describe_holder(path: Path) -> str:
    """The holder wrote its identity before losing the race, so a refused process can name it."""
    info = holder_info(path)
    if info is None:
        return "an unknown process (no readable holder record)"
    return f"pid {info['pid']} role {info.get('role', '?')} on {info.get('machine', '?')}"
