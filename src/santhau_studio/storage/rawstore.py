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

from santhau_studio.storage.segments import (
    DEFAULT_SEGMENT_BYTES,
    FaultHook,
    SegmentCorruptError,
    SegmentMissing,
    SegmentStore,
)

CODEC = "gzip"
GZIP_LEVEL = 6
CHUNK = 1 << 16

LAYOUT_OBJECTS = "objects"
LAYOUT_SEGMENTS = "segments"
LAYOUTS = (LAYOUT_OBJECTS, LAYOUT_SEGMENTS)


class ObjectCorruptError(RuntimeError):
    """The bytes on disk do not match, or do not decompress to, the recorded hash."""


@dataclass(frozen=True)
class ObjectRef:
    sha256: str
    byte_len: int
    storage_rel_path: str
    codec: str = CODEC
    already_existed: bool = False
    segment: int | None = None
    offset: int | None = None


def _rel_path(sha256: str) -> str:
    return f"raw/objects/{sha256[:2]}/{sha256}.bin.gz"


class RawObjectStore:
    def __init__(
        self,
        data_root: Path,
        *,
        redaction_policy_version: int = 2,
        layout: str = LAYOUT_OBJECTS,
        segment_bytes: int = DEFAULT_SEGMENT_BYTES,
        fault: FaultHook | None = None,
        read_only: bool = False,
    ) -> None:
        if layout not in LAYOUTS:
            raise ValueError(f"unknown raw layout {layout!r}")
        self.root = Path(data_root)
        self.objects_dir = self.root / "raw" / "objects"
        self.staging_dir = self.root / "staging"
        self.quarantine_dir = self.root / "quarantine"
        self.redaction_policy_version = redaction_policy_version
        self.layout = layout
        self.segment_bytes = segment_bytes
        self._fault = fault
        self.read_only = read_only
        self._segments: SegmentStore | None = None
        if not read_only:
            for directory in (self.objects_dir, self.staging_dir, self.quarantine_dir):
                directory.mkdir(parents=True, exist_ok=True)
        if layout == LAYOUT_SEGMENTS:
            _ = self.segments  # create the segment directory up front for this layout

    @property
    def segments_dir(self) -> Path:
        return self.root / "raw" / "segments"

    @property
    def segments(self) -> SegmentStore | None:
        """Segment backend, opened lazily: legacy roots must not gain segment files just by reading."""
        if self._segments is None and (
            self.layout == LAYOUT_SEGMENTS or (self.segments_dir / "index.sqlite").exists()
        ):
            self._segments = SegmentStore(
                self.root, segment_bytes=self.segment_bytes, fault=self._fault, read_only=self.read_only
            )
        return self._segments

    def close(self) -> None:
        if self._segments is not None:
            self._segments.close()
            self._segments = None

    # ------------------------------------------------------------------ write

    def put(self, payload: bytes) -> ObjectRef:
        if self.read_only:
            raise PermissionError("raw store is read-only")
        """Persist one object. Never overwrites: content addressing means same bytes = same object."""
        if not isinstance(payload, bytes):
            raise TypeError("raw store takes post-redaction bytes")
        if self.layout == LAYOUT_SEGMENTS:
            assert self.segments is not None
            sha, location, existed = self.segments.put(payload)
            rel = self.segments.rel_path(sha) or ""
            return ObjectRef(sha, len(payload), rel, already_existed=existed,
                             segment=location.segment, offset=location.offset)
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
        segments = self.segments
        if segments is not None and segments.has(sha256):
            try:
                return segments.read(sha256)
            except SegmentMissing:
                pass
            except SegmentCorruptError as exc:
                raise ObjectCorruptError(str(exc)) from exc
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
        """sha256 of every object file physically present (legacy layout).

        Path.stem on "<sha>.bin.gz" yields "<sha>.bin", not "<sha>" — using it made every
        object look simultaneously missing and orphaned, which would have marked a healthy
        store CORRUPT on the first recovery run.
        """
        return {path.name.removesuffix(".bin.gz") for path in self.objects_dir.rglob("*.bin.gz")}

    def known_shas(self) -> set[str]:
        """Every sha the store can serve, across both layouts (dual-read)."""
        known = self.scan_files()
        segments = self.segments
        if segments is not None:
            known |= segments.known_shas()
        return known

    def recover(self, conn: sqlite3.Connection) -> RecoveryReport:
        """§5.2 step 5: reconcile rows against bytes. Never raises for a single bad object.

        Marking CORRUPT is a DB write, not a deletion: §5.1 forbids deleting or reusing a
        damaged object, and §5.5 requires quarantine to stay visible.
        """
        rows = {row["sha256"]: row for row in conn.execute("SELECT sha256, storage_rel_path FROM raw_objects")}
        files = self.known_shas()

        missing_file_set = set(rows) - files
        missing_files = sorted(missing_file_set)
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
                        "OBJECT_ROW_ORPHAN" if sha in missing_file_set else "HASH",
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

    def migrate_to_segments(self, conn: sqlite3.Connection, *, remove_legacy: bool = False) -> dict[str, int]:
        """Move every referenced object's bytes into segments; verify bytes and cardinality.

        Legacy files stay in place by default, which is the rollback: deleting `raw/segments`
        and reopening the store in object layout restores the previous read source with no row
        edit. Rows get a segment locator only after the bytes verify.
        """
        rows = conn.execute("SELECT sha256 FROM raw_objects ORDER BY sha256").fetchall()
        migrated = 0
        failures: list[str] = []
        for row in rows:
            sha = row["sha256"]
            try:
                payload = self.read(sha)  # dual-read: legacy today, segment after the move
            except ObjectCorruptError as exc:
                failures.append(f"{sha}: {exc}")
                continue
            if hashlib.sha256(payload).hexdigest() != sha:
                failures.append(f"{sha}: hash mismatch before migration")
                continue
            assert self.segments is not None
            new_sha, _location, _existed = self.segments.put(payload)
            if new_sha != sha:
                failures.append(f"{sha}: segment stored {new_sha}")
                continue
            rel = self.segments.rel_path(sha) or ""
            conn.execute("UPDATE raw_objects SET storage_rel_path=?, codec='gzip' WHERE sha256=?", (rel, sha))
            migrated += 1
        conn.commit()
        assert self.segments is not None
        verified, bad = self.segments.verify_all()
        failures.extend(f"{sha}: segment verify failed" for sha in bad)
        if remove_legacy and not failures:
            for row in rows:
                self.path_for(row["sha256"]).unlink(missing_ok=True)
        return {"rows": len(rows), "migrated": migrated, "verified": verified,
                "failures": len(failures), "legacy_removed": int(remove_legacy and not failures)}

    def collect_orphan_files(self, conn: sqlite3.Connection, *, dry_run: bool = True) -> tuple[str, ...]:
        """Delete legacy object files that no row references (§5.2: after the verifying job).

        Row orphans are never collected here — §5.2 says keep them and age out separately.
        Segment objects cannot be deleted in place; `compact(keep)` is their collection path.
        """
        rows = {row[0] for row in conn.execute("SELECT sha256 FROM raw_objects")}
        orphans = sorted(self.scan_files() - rows)
        if not dry_run:
            for sha in orphans:
                self.path_for(sha).unlink(missing_ok=True)
        return tuple(orphans)

    def segment_orphans(self, conn: sqlite3.Connection) -> tuple[str, ...]:
        """Segment objects no row references; dropped by the next compact, never deleted inline."""
        segments = self.segments
        if segments is None:
            return ()
        rows = {row[0] for row in conn.execute("SELECT sha256 FROM raw_objects")}
        return tuple(sorted(segments.known_shas() - rows))

    def compact_segments(self, conn: sqlite3.Connection) -> dict[str, int]:
        """Rewrite live segment objects into fresh segments, dropping orphans."""
        segments = self.segments
        if segments is None:
            return {"kept": 0, "dropped": 0}
        keep = {row[0] for row in conn.execute("SELECT sha256 FROM raw_objects")}
        return segments.compact(keep)

    def quarantine_page(self, response_sha: str) -> Path:
        """Move an unreadable page out of the readable set without destroying it (§5.5).

        In segment layout the bytes are immutable inside a segment: the object is exported to
        the quarantine directory and the segment is left untouched (its index row remains, so
        the evidence trail is not edited by an integrity response). A corrupt frame is exported
        as raw bytes (`.frame.bin`) because decompressing it is exactly what failed.
        """
        target = self.quarantine_dir / f"{response_sha}.bin.gz"
        segments = self.segments
        if segments is not None and segments.has(response_sha):
            try:
                segments.export(response_sha, target)
            except SegmentCorruptError:
                segments.export_frame(response_sha, self.quarantine_dir / f"{response_sha}.frame.bin")
            return target
        source = self.path_for(response_sha)
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
