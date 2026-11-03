"""Raw object store — immutable, content-addressed, crash-durable (§5.1, §5.2).

Layout: <root>/raw/objects/<2 hex>/<sha256>.bin.gz
Writes land in <root>/staging/ first, are fsynced, then os.replace'd into place.

The durability argument differs per platform and is stated rather than assumed:

* file content is durable after os.fsync on both POSIX and Windows;
* os.replace is atomic when source and destination share a volume (NTFS guarantees it);
* **directory fsync is not available on Windows.** os.fsync on a directory handle
  raises, and FlushFileBuffers on a volume needs elevation. A crash between rename and
  journal flush can therefore lose the *name* while the bytes survive. The store does not
  pretend otherwise: recovery treats "file exists, no row" as collectable garbage and
  "row exists, no file" as CORRUPT, which is the asymmetry §5.2 defines.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import zlib
from dataclasses import dataclass
from pathlib import Path

CODEC = "gzip"
GZIP_LEVEL = 6
CHUNK = 1 << 16


class ObjectCorruptError(RuntimeError):
    """The bytes on disk do not match, or do not decompress to, the recorded hash."""


@dataclass(frozen=True)
class ObjectRef:
    sha256: str
    byte_len: int
    storage_rel_path: str
    codec: str = CODEC
    already_existed: bool = False


def _rel_path(sha256: str) -> str:
    return f"raw/objects/{sha256[:2]}/{sha256}.bin.gz"


class RawObjectStore:
    def __init__(self, data_root: Path, *, redaction_policy_version: int = 1) -> None:
        self.root = Path(data_root)
        self.objects_dir = self.root / "raw" / "objects"
        self.staging_dir = self.root / "staging"
        self.quarantine_dir = self.root / "quarantine"
        self.redaction_policy_version = redaction_policy_version
        for directory in (self.objects_dir, self.staging_dir, self.quarantine_dir):
            directory.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ write

    def put(self, payload: bytes) -> ObjectRef:
        """Persist one object. Never overwrites: content addressing means same bytes = same object."""
        if not isinstance(payload, bytes):
            raise TypeError("raw store takes post-redaction bytes")
        sha = hashlib.sha256(payload).hexdigest()
        rel = _rel_path(sha)
        final = self.root / rel
        if final.exists():
            return ObjectRef(sha, len(payload), rel, already_existed=True)

        final.parent.mkdir(parents=True, exist_ok=True)
        staged = self.staging_dir / f"{sha}.{os.getpid()}.{next(_counter)}.bin.gz.part"
        try:
            with (
                staged.open("wb") as handle,
                gzip.GzipFile(filename="", mode="wb", fileobj=handle, compresslevel=GZIP_LEVEL, mtime=0) as gz,
            ):
                gz.write(payload)
                gz.flush()
                handle.flush()
                os.fsync(handle.fileno())  # BUG-06: gzip.close() alone only flushes, never fsyncs
            _sync_directory(staged.parent)
            staged.replace(final)  # atomic within one volume
            _sync_directory(final.parent)
        finally:
            staged.unlink(missing_ok=True)  # a crashed sibling's leftovers must not accumulate
        return ObjectRef(sha, len(payload), rel)

    def put_json(self, payload: object, canonical: bytes) -> ObjectRef:
        """Store canonical bytes; the caller owns serialisation so hash inputs stay reproducible."""
        del payload  # accepted for call-site readability; canonical is authoritative
        return self.put(canonical)

    # ------------------------------------------------------------------ read

    def path_for(self, sha256: str) -> Path:
        return self.root / _rel_path(sha256)

    def read(self, sha256: str) -> bytes:
        path = self.path_for(sha256)
        if not path.exists():
            raise ObjectCorruptError(f"object row without file: {sha256}")
        try:
            with gzip.open(path, "rb") as handle:
                data = handle.read()
        except (OSError, EOFError, zlib.error, gzip.BadGzipFile) as exc:
            # zlib.error is not an OSError: a damaged deflate stream must be reported as a
            # corrupt object (FAULT-13) rather than escape as an unhandled crash.
            raise ObjectCorruptError(f"unreadable object {sha256}: {exc}") from exc
        actual = hashlib.sha256(data).hexdigest()
        if actual != sha256:
            raise ObjectCorruptError(f"hash mismatch for {sha256}: bytes hash to {actual}")
        return data

    def verify(self, sha256: str) -> tuple[bool, str]:
        try:
            self.read(sha256)
        except ObjectCorruptError as exc:
            return False, str(exc)
        return True, "ok"

    # ------------------------------------------------------------------ recovery / GC

    def scan_files(self) -> set[str]:
        """sha256 of every object file physically present.

        Path.stem on "<sha>.bin.gz" yields "<sha>.bin", not "<sha>" — using it made every
        object look simultaneously missing and orphaned, which would have marked a healthy
        store CORRUPT on the first recovery run.
        """
        return {path.name.removesuffix(".bin.gz") for path in self.objects_dir.rglob("*.bin.gz")}

    def recover(self, conn: sqlite3.Connection) -> RecoveryReport:
        """§5.2 step 5: reconcile rows against files. Never raises for a single bad object.

        Marking CORRUPT is a DB write, not a deletion: §5.1 forbids deleting or reusing a
        damaged object, and §5.5 requires quarantine to stay visible.
        """
        rows = {row["sha256"]: row for row in conn.execute("SELECT sha256, storage_rel_path FROM raw_objects")}
        files = self.scan_files()

        missing_files = sorted(set(rows) - files)
        orphan_files = sorted(files - set(rows))
        unreadable: list[tuple[str, str]] = []
        for sha in sorted(set(rows) & files):
            ok, detail = self.verify(sha)
            if not ok:
                unreadable.append((sha, detail))

        conn.execute("BEGIN")
        try:
            stamp = _now_utc()
            for sha, detail in [*[(s, "file absent") for s in missing_files], *unreadable]:
                conn.execute(
                    "UPDATE raw_objects SET integrity_status='CORRUPT' WHERE sha256=?",
                    (sha,),
                )
                # datetime('now') yields "YYYY-MM-DD HH:MM:SS" with no Z, which the
                # acted_at_utc CHECK rejects. Timestamps are produced in Python.
                conn.execute(
                    "INSERT INTO integrity_repairs (target_kind, target_ref, finding, action, acted_at_utc, run_id, detail_json)"
                    " VALUES (?, ?, ?, 'MARK_CORRUPT', ?, ?, ?)",
                    (
                        "OBJECT_ROW_ORPHAN" if sha in missing_files else "HASH",
                        sha,
                        detail,
                        stamp,
                        "recovery",
                        json.dumps({"reason": detail}),
                    ),
                )
            conn.execute("COMMIT")
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            raise

        return RecoveryReport(
            rows=len(rows),
            files=len(files),
            missing_files=tuple(missing_files),
            orphan_files=tuple(orphan_files),
            unreadable=tuple(sha for sha, _ in unreadable),
        )

    def collect_orphan_files(self, conn: sqlite3.Connection, *, dry_run: bool = True) -> tuple[str, ...]:
        """Delete object files that no row references (§5.2: safe after the verifying job finished).

        Row orphans are never collected here — §5.2 says keep them and age out separately.
        """
        rows = {row[0] for row in conn.execute("SELECT sha256 FROM raw_objects")}
        orphans = sorted(self.scan_files() - rows)
        if not dry_run:
            for sha in orphans:
                self.path_for(sha).unlink(missing_ok=True)
        return tuple(orphans)

    def quarantine_page(self, response_sha: str) -> Path:
        """Move an unreadable page out of the readable set without destroying it (§5.5)."""
        source = self.path_for(response_sha)
        target = self.quarantine_dir / f"{response_sha}.bin.gz"
        if source.exists():
            shutil.move(str(source), str(target))
        return target

    def clear_staging(self) -> int:
        """Drop partial writes left by a crash. They were never durable and have no rows."""
        removed = 0
        for path in self.staging_dir.glob("*.part"):
            path.unlink(missing_ok=True)
            removed += 1
        return removed


@dataclass(frozen=True)
class RecoveryReport:
    rows: int
    files: int
    missing_files: tuple[str, ...]
    orphan_files: tuple[str, ...]
    unreadable: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not (self.missing_files or self.orphan_files or self.unreadable)


def _sync_directory(path: Path) -> None:
    """Best-effort directory sync. A no-op on Windows; see module docstring for why."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass  # Windows: EINVAL on a directory handle. Expected, not a bug to hide.
    finally:
        os.close(fd)


def _now_utc() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


_counter = iter(range(1_000_000_000))
