"""Byte-offset segment store behind the logical content-addressed objects (§5.1).

The benchmark (tools/bench_raw_store.py, 5k pages x 10 records) measured object-per-file reads
at ~13.2 ms/object versus ~0.023 ms for byte-offset spans — a ~128x gap at equal verification
success — and object layout paid 6.55x the logical size in NTFS cluster overhead. This module
keeps the identity contract intact (objects are still addressed by sha256 of their bytes) while
the bytes live packed in append-only segment files.

Layout of one segment: `<root>/raw/segments/seg-<8 digits>.bin.gz`, a concatenation of framed
blobs:

    MAGIC(4) | sha256(raw bytes, 32) | codec(1) | crc32(blob, 4) | raw_len(8) | blob_len(4) | blob

The frame is self-describing, so recovery can rebuild the sidecar index from the bytes alone.
`index.sqlite` next to the segments is a *cache*: fast random access, rebuildable, and never the
only proof that an object exists.

Write order per object: append+flush+fsync the segment, then insert the index row (index runs
`synchronous=NORMAL` — losing an index row costs a rescan, losing object bytes would be data
loss, so only the bytes get the expensive sync). A crash between the two leaves a valid framed
object that recovery indexes; a torn frame at the tail is truncated back to the last good frame.

G0-B invariants untouched: `raw_objects` rows, `ObjectRef.sha256`, quarantine semantics and the
verify-before-restore gate all behave exactly as with the file layout.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import struct
import zlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

MAGIC = b"SGS1"
CODEC_GZIP = 1
CODEC_NAMES = {CODEC_GZIP: "gzip"}
HEADER = struct.Struct("<4s32sBIQI")
GZIP_LEVEL = 6
DEFAULT_SEGMENT_BYTES = 64 * 1024 * 1024
SEGMENT_NAME = "seg-{seq:08d}.bin.gz"
INDEX_NAME = "index.sqlite"
SWAP_MARKER = "segments-swap.json"

FaultHook = Callable[[str], None]


class SegmentError(RuntimeError):
    """Segment storage failed in a way the caller must not paper over."""


class SegmentMissing(SegmentError):
    """No object with this sha256 exists in any segment."""


class SegmentCorruptError(SegmentError):
    """Framing, checksum or hash verification failed for one object."""


@dataclass(frozen=True)
class Location:
    segment: int
    offset: int
    blob_len: int
    raw_len: int
    crc32: int
    codec: int


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class SegmentStore:
    def __init__(
        self,
        data_root: Path,
        *,
        segment_bytes: int = DEFAULT_SEGMENT_BYTES,
        fault: FaultHook | None = None,
        directory: Path | None = None,
        read_only: bool = False,
    ) -> None:
        self.root = Path(data_root)
        self.directory = Path(directory) if directory else self.root / "raw" / "segments"
        self.segment_bytes = segment_bytes
        self._fault = fault
        self.read_only = read_only
        self._seq: int | None = None
        self._handle = None  # type: ignore[assignment]
        self._pos = 0
        self._conn = None
        if not read_only:
            self.recover_swap()  # before mkdir/open_index: an empty live directory is not a promotion
            self.directory.mkdir(parents=True, exist_ok=True)
        self._conn = self._open_index()
        if read_only:
            return  # readers must never recover/truncate a writer's in-flight segment
        if not (self.directory.parent / SWAP_MARKER).exists():
            shutil.rmtree(self.directory.with_name(self.directory.name + ".compact"), ignore_errors=True)
        self.recover_tail()

    # ------------------------------------------------------------------ index

    def _index_path(self) -> Path:
        return self.directory / INDEX_NAME

    def _open_index(self) -> sqlite3.Connection:
        if self.read_only:
            conn = sqlite3.connect(self._index_path().resolve().as_uri() + "?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            return conn
        conn = sqlite3.connect(self._index_path())
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")  # cache: losing a row is a rescan, not data loss
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS objects (sha256 TEXT PRIMARY KEY, segment INTEGER NOT NULL,"
            " offset INTEGER NOT NULL, blob_len INTEGER NOT NULL, raw_len INTEGER NOT NULL,"
            " codec INTEGER NOT NULL, crc32 INTEGER NOT NULL, created_at_utc TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS segments (seq INTEGER PRIMARY KEY, bytes INTEGER NOT NULL,"
            " closed INTEGER NOT NULL DEFAULT 0, created_at_utc TEXT NOT NULL);"
        )
        conn.commit()
        return conn

    def close(self) -> None:
        self._close_active()
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> SegmentStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ write path

    def put(self, payload: bytes) -> tuple[str, Location, bool]:
        """Append one object; returns (sha256, location, already_existed)."""
        if self.read_only:
            raise PermissionError("segment store is read-only")
        sha = hashlib.sha256(payload).hexdigest()
        existing = self._location(sha)
        if existing is not None:
            return sha, existing, True
        blob = gzip.compress(payload, GZIP_LEVEL, mtime=0)
        header = HEADER.pack(MAGIC, bytes.fromhex(sha), CODEC_GZIP, zlib.crc32(blob), len(payload), len(blob))
        frame = header + blob
        handle = self._active(int(self.segment_bytes))
        offset = self._pos
        handle.write(frame)
        handle.flush()
        os.fsync(handle.fileno())
        self._pos += len(frame)
        self._fault_once("after_fsync")
        location = Location(segment=int(self._seq), offset=offset, blob_len=len(blob),
                            raw_len=len(payload), crc32=zlib.crc32(blob), codec=CODEC_GZIP)
        self._index(sha, location)
        self._fault_once("after_index")
        return sha, location, False

    def _fault_once(self, stage: str) -> None:
        if self._fault is not None:
            self._fault(stage)

    def _index(self, sha: str, location: Location) -> None:
        assert self._conn is not None
        self._conn.execute(
            "INSERT OR REPLACE INTO objects (sha256, segment, offset, blob_len, raw_len, codec, crc32,"
            " created_at_utc) VALUES (?,?,?,?,?,?,?,?)",
            (sha, location.segment, location.offset, location.blob_len, location.raw_len,
             location.codec, location.crc32, _now()),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO segments (seq, bytes, closed, created_at_utc) VALUES (?,?,?,?)",
            (location.segment, location.offset + HEADER.size + location.blob_len, 0, _now()),
        )
        self._conn.commit()

    def _active(self, limit: int):
        if self._handle is not None and self._pos + HEADER.size + 1 <= limit:
            return self._handle
        if self._handle is not None:
            self._close_active(closed=True)
            self._fault_once("after_rotate_close")
        if self._seq is None:
            self._adopt_last_segment(limit)
        if self._handle is not None:
            return self._handle
        seq = self._next_seq()
        path = self.directory / SEGMENT_NAME.format(seq=seq)
        self._handle = path.open("ab")
        self._seq = seq
        self._pos = path.stat().st_size
        assert self._conn is not None
        self._conn.execute(
            "INSERT OR IGNORE INTO segments (seq, bytes, closed, created_at_utc) VALUES (?,?,0,?)",
            (seq, self._pos, _now()),
        )
        self._conn.commit()
        return self._handle

    def _adopt_last_segment(self, limit: int) -> None:
        """A restarted process appends to the last open segment instead of opening a new one."""
        assert self._conn is not None
        paths = sorted(self.directory.glob("seg-*.bin.gz"))
        if not paths:
            return
        last = paths[-1]
        seq = int(last.name[4:12])
        row = self._conn.execute("SELECT bytes, closed FROM segments WHERE seq=?", (seq,)).fetchone()
        size = last.stat().st_size
        if row is not None and int(row["closed"]) == 1:
            return
        if row is not None and int(row["bytes"]) != size:
            return  # inconsistent with the index: recover_tail() has final say, do not append yet
        if size + HEADER.size + 1 > limit:
            return
        self._handle = last.open("ab")
        self._seq = seq
        self._pos = size

    def _close_active(self, *, closed: bool = False) -> None:
        if self._handle is not None:
            self._handle.flush()
            os.fsync(self._handle.fileno())
            self._handle.close()
            self._handle = None
        if closed and self._seq is not None and self._conn is not None:
            self._conn.execute("UPDATE segments SET closed=1 WHERE seq=?", (self._seq,))
            self._conn.commit()

    def _next_seq(self) -> int:
        assert self._conn is not None
        existing = [row[0] for row in self._conn.execute("SELECT seq FROM segments")]
        on_disk = [int(path.name[4:12]) for path in self.directory.glob("seg-*.bin.gz")]
        return max([*existing, *on_disk], default=0) + 1

    # ------------------------------------------------------------------ read path

    def _location(self, sha: str) -> Location | None:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT segment, offset, blob_len, raw_len, crc32, codec FROM objects WHERE sha256=?", (sha,)
        ).fetchone()
        return Location(*row) if row else None

    def has(self, sha: str) -> bool:
        return self._location(sha) is not None

    def read(self, sha: str) -> bytes:
        location = self._location(sha)
        if location is None:
            raise SegmentMissing(f"no segment object {sha}")
        path = self.directory / SEGMENT_NAME.format(seq=location.segment)
        try:
            with path.open("rb") as handle:
                handle.seek(location.offset)
                header = handle.read(HEADER.size)
                blob = handle.read(location.blob_len)
        except OSError as exc:
            raise SegmentCorruptError(f"cannot read {sha} from segment {location.segment}: {exc}") from exc
        return self._decode(sha, header, blob)

    def _decode(self, sha: str, header: bytes, blob: bytes) -> bytes:
        if len(header) != HEADER.size:
            raise SegmentCorruptError(f"short header for {sha}")
        magic, stored_sha, codec, crc, raw_len, blob_len = HEADER.unpack(header)
        if magic != MAGIC:
            raise SegmentCorruptError(f"bad frame magic for {sha}")
        if stored_sha.hex() != sha:
            raise SegmentCorruptError(f"frame holds {stored_sha.hex()} not {sha}")
        if len(blob) != blob_len or zlib.crc32(blob) != crc:
            raise SegmentCorruptError(f"blob checksum mismatch for {sha}")
        try:
            payload = gzip.decompress(blob) if codec == CODEC_GZIP else bytes(blob)
        except (OSError, EOFError, zlib.error, gzip.BadGzipFile) as exc:
            raise SegmentCorruptError(f"cannot decompress {sha}: {exc}") from exc
        if len(payload) != raw_len or hashlib.sha256(payload).hexdigest() != sha:
            raise SegmentCorruptError(f"hash mismatch for {sha}")
        return payload

    def verify_all(self) -> tuple[int, tuple[str, ...]]:
        assert self._conn is not None
        bad: list[str] = []
        count = 0
        for row in self._conn.execute("SELECT sha256 FROM objects ORDER BY sha256"):
            count += 1
            try:
                self.read(row["sha256"])
            except (SegmentMissing, SegmentCorruptError):
                bad.append(row["sha256"])
        return count, tuple(bad)

    def known_shas(self) -> set[str]:
        assert self._conn is not None
        return {row[0] for row in self._conn.execute("SELECT sha256 FROM objects")}

    def rel_path(self, sha: str) -> str | None:
        location = self._location(sha)
        if location is None:
            return None
        return f"raw/segments/{SEGMENT_NAME.format(seq=location.segment)}#{location.offset}"

    # ------------------------------------------------------------------ recovery

    def recover_tail(self) -> dict[str, int]:
        """Scan the last segment from its indexed end, index complete frames, truncate a torn tail.

        Only the tail is rescanned: closed segments are trusted (their rows were committed after
        fsync) and a full rescan is what `verify_all` is for.
        """
        assert self._conn is not None
        paths = sorted(self.directory.glob("seg-*.bin.gz"))
        if not paths:
            return {"indexed": 0, "truncated_bytes": 0}
        last = paths[-1]
        seq = int(last.name[4:12])
        row = self._conn.execute("SELECT bytes FROM segments WHERE seq=?", (seq,)).fetchone()
        start = int(row["bytes"]) if row else 0
        indexed = 0
        truncated = 0
        with last.open("r+b") as handle:
            size = last.stat().st_size
            if start > size:  # index claims bytes the file does not have: distrust the claim
                start = 0
            handle.seek(start)
            position = start
            while position < size:
                header = handle.read(HEADER.size)
                if len(header) < HEADER.size:
                    break
                magic, stored_sha, codec, crc, raw_len, blob_len = HEADER.unpack(header)
                if magic != MAGIC or blob_len <= 0 or position + HEADER.size + blob_len > size:
                    break
                blob = handle.read(blob_len)
                try:
                    self._decode(stored_sha.hex(), header, blob)
                except SegmentCorruptError:
                    break
                sha = stored_sha.hex()
                if self._location(sha) is None:
                    self._index(sha, Location(seq, position, blob_len, raw_len, crc, codec))
                    indexed += 1
                position += HEADER.size + blob_len
            if position < size:
                handle.truncate(position)
                os.fsync(handle.fileno())
                truncated = size - position
        self._conn.execute(
            "UPDATE segments SET bytes=?, closed=0 WHERE seq=?", (position, seq)
        )
        self._conn.commit()
        return {"indexed": indexed, "truncated_bytes": truncated}

    def recover_swap(self) -> bool:
        """Finish or roll back an interrupted compaction before anything else touches segments."""
        marker = self.directory.parent / SWAP_MARKER
        if not marker.exists():
            return False
        try:
            state = json.loads(marker.read_text(encoding="utf-8"))
            staging = Path(state["new"])
            retired = Path(state["old"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SegmentCorruptError("invalid segment swap marker; no files changed") from exc
        expected_staging = self.directory.with_name(self.directory.name + ".compact")
        parent = self.directory.parent.resolve()
        if (staging.resolve() != expected_staging.resolve()
                or retired.resolve().parent != parent
                or not re.fullmatch(re.escape(self.directory.name) + r"\.old-[0-9TZ.\-]+", retired.name)
                or staging.is_symlink() or retired.is_symlink()):
            raise SegmentCorruptError("unsafe segment swap paths; no files changed")
        # Adopt the completed side: if the live directory is missing, promotion never finished.
        if not self.directory.exists():
            if staging.exists() and (staging / INDEX_NAME).is_file():
                staging.replace(self.directory)
            elif retired.exists() and (retired / INDEX_NAME).is_file():
                retired.replace(self.directory)
            else:
                raise SegmentCorruptError("no complete segment directory to recover; files preserved")
        if not (self.directory / INDEX_NAME).is_file():
            raise SegmentCorruptError("recovered segment index missing; retired data preserved")
        marker.unlink(missing_ok=True)
        for leftover in (staging, retired):
            if leftover and leftover.resolve() != self.directory.resolve() and leftover.exists():
                shutil.rmtree(leftover, ignore_errors=True)
        return True

    # ------------------------------------------------------------------ compaction

    def compact(self, keep: Iterable[str]) -> dict[str, int]:
        """Rewrite live objects into fresh segments and retire the old ones; orphans are dropped.

        Crash windows: after staging (marker not yet written → the staging directory is removed
        on the next attempt), after retiring the old directory and after promoting the new one
        (both covered by `recover_swap`, which runs at every open).
        """
        keep_set = set(keep)
        dropped = len(self.known_shas() - keep_set)
        staging = self.directory.with_name(self.directory.name + ".compact")
        if staging.exists():
            shutil.rmtree(staging)
        retired = self.directory.with_name(self.directory.name + ".old-" + _now().replace(":", ""))
        marker = self.directory.parent / SWAP_MARKER
        self._close_active()
        promoted = False
        live = 0
        try:
            with SegmentStore(self.root, segment_bytes=self.segment_bytes, directory=staging) as staged:
                for sha in sorted(self.known_shas() & keep_set):
                    staged.put(self.read(sha))
                    live += 1
            self._fault_once("compact_after_stage")
            marker.write_text(json.dumps({"old": str(retired), "new": str(staging)}), encoding="utf-8")
            with marker.open("rb+") as handle:  # a read-only handle cannot be fsynced
                os.fsync(handle.fileno())
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            self.directory.replace(retired)
            self._fault_once("compact_after_retire")
            staging.replace(self.directory)
            promoted = True
        finally:
            if self._conn is None:
                self.recover_swap()  # promotes staging when the live directory is missing
                self._conn = self._open_index()
                self._seq = None
                self._pos = 0
                self._handle = None
            if not promoted:
                shutil.rmtree(staging, ignore_errors=True)
        marker.unlink(missing_ok=True)
        shutil.rmtree(retired, ignore_errors=True)
        return {"kept": live, "dropped": dropped}

    # ------------------------------------------------------------------ quarantine

    def export_frame(self, sha: str, destination: Path) -> bool:
        """Copy the raw frame bytes (header + blob) for a corrupt object; forensics, not serving."""
        location = self._location(sha)
        if location is None:
            return False
        path = self.directory / SEGMENT_NAME.format(seq=location.segment)
        with path.open("rb") as handle:
            handle.seek(location.offset)
            frame = handle.read(HEADER.size + location.blob_len)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(frame)
        with destination.open("rb+") as handle:
            os.fsync(handle.fileno())
        return True

    def export(self, sha: str, destination: Path) -> None:
        """Write one object as a standalone gzip file (quarantine/backup export)."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = self.read(sha)
        destination.write_bytes(gzip.compress(payload, GZIP_LEVEL, mtime=0))
        with destination.open("rb+") as handle:  # read-only handles cannot be fsynced on Windows
            os.fsync(handle.fileno())
