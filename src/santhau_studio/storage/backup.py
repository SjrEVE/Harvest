"""Backup and restore with verification (§7.2 UI-10, §11 "restore verify").

A backup is a directory holding the warehouse snapshot, the raw objects it references and a
manifest listing every object with its hash. Restore verifies *before* swapping anything in:
FAULT-08 requires that a backup missing one raw object blocks the swap and names the hash,
rather than restoring a database full of references to bytes that do not exist.

Two independent gates, because they catch different failures:
1. every object in the manifest is physically present and hashes correctly;
2. every object the *restored* warehouse references is covered by that manifest — a backup
   directory assembled from a mismatched database and raw set passes gate 1 and is still
   unusable.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sqlite3
import tempfile
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from santhau_studio.storage.rawstore import ObjectCorruptError, RawObjectStore

MANIFEST_NAME = "backup-manifest.json"
MANIFEST_VERSION = 1
WAREHOUSE_NAME = "warehouse.sqlite"
RAW_DIR_NAME = "raw"


class RestoreBlocked(RuntimeError):
    """Refusing to swap is correct behaviour: a partial restore is worse than none."""


@dataclass(frozen=True)
class BackupResult:
    path: Path
    objects: int
    warehouse_bytes: int
    manifest_sha: str


@dataclass(frozen=True)
class RestoreReport:
    restored: bool
    objects_verified: int
    missing: tuple[str, ...]
    corrupt: tuple[str, ...]
    reason: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def create_backup(data_root: Path, destination: Path | None = None) -> BackupResult:
    """Snapshot the warehouse and copy the raw objects it references.

    Objects are enumerated from the database, not from the directory, so an orphan file
    (no row) is deliberately excluded — §5.2 classifies it as garbage, not data.
    """
    data_root = Path(data_root)
    warehouse = data_root / WAREHOUSE_NAME
    if not warehouse.exists():
        raise RestoreBlocked(f"no warehouse at {warehouse}")

    target = Path(destination) if destination else data_root / "backups" / f"backup-{_stamp()}"
    target.mkdir(parents=True, exist_ok=True)
    objects_dir = target / RAW_DIR_NAME
    objects_dir.mkdir(exist_ok=True)

    # The online backup API, not shutil.copyfile: a WAL database keeps committed transactions
    # in warehouse.sqlite-wal, so a byte copy of the main file alone can restore an older
    # state than the one that was backed up. backup() reads through the WAL and writes one
    # self-contained file with no sidecars.
    conn = sqlite3.connect(warehouse)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT sha256, byte_len, codec, storage_rel_path, integrity_status,"
            " redaction_policy_version FROM raw_objects ORDER BY sha256"
        ).fetchall()
        _snapshot_into(conn, target / WAREHOUSE_NAME)
    finally:
        conn.close()

    entries = []
    store = RawObjectStore(data_root)
    try:
        for row in rows:
            payload = _read_object_for_backup(store, data_root, row)
            if payload is None:
                raise RestoreBlocked(
                    f"cannot back up: object row without file or readable bytes, sha256={row['sha256']} "
                    f"status={row['integrity_status']}"
                )
            relative = f"{row['sha256']}.bin.gz"
            (objects_dir / relative).write_bytes(gzip.compress(payload, 6, mtime=0))
            entries.append(
                {
                    "sha256": row["sha256"],
                    "byte_len": row["byte_len"],
                    "codec": row["codec"],
                    "file": relative,
                    "integrity_status": row["integrity_status"],
                    "redaction_policy_version": row["redaction_policy_version"],
                }
            )
    finally:
        store.close()

    manifest = {
        "version": MANIFEST_VERSION,
        "created_at_utc": _now(),
        "warehouse": WAREHOUSE_NAME,
        "object_count": len(entries),
        "objects": entries,
    }
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True)
    (target / MANIFEST_NAME).write_text(manifest_text, encoding="utf-8")

    return BackupResult(
        path=target,
        objects=len(entries),
        warehouse_bytes=(target / WAREHOUSE_NAME).stat().st_size,
        manifest_sha=hashlib.sha256(manifest_text.encode("utf-8")).hexdigest(),
    )


def _read_object_for_backup(store: RawObjectStore, data_root: Path, row: sqlite3.Row) -> bytes | None:
    """Object bytes through the store (dual-read), falling back to quarantine (§5.5 evidence)."""
    try:
        return store.read(row["sha256"])
    except ObjectCorruptError:
        quarantined = data_root / "quarantine" / f"{row['sha256']}.bin.gz"
        return _decompress(quarantined) if quarantined.exists() else None


def load_manifest(source: Path) -> dict:
    manifest = json.loads((Path(source) / MANIFEST_NAME).read_text(encoding="utf-8"))
    if manifest.get("version") != MANIFEST_VERSION:
        raise RestoreBlocked(f"unsupported backup manifest version: {manifest.get('version')!r}")
    if not (Path(source) / WAREHOUSE_NAME).exists():
        raise RestoreBlocked(f"backup has no warehouse snapshot: {source}")
    if manifest["object_count"] != len(manifest["objects"]):
        raise RestoreBlocked("backup manifest object_count disagrees with its object list")
    return manifest


def verify_backup(source: Path) -> RestoreReport:
    """Check every manifest object exists and hashes correctly, without writing anything."""
    source = Path(source)
    manifest = load_manifest(source)
    missing: list[str] = []
    corrupt: list[str] = []
    for entry in manifest["objects"]:
        path = source / RAW_DIR_NAME / entry["file"]
        if not path.exists():
            missing.append(entry["sha256"])
            continue
        payload = _decompress(path)
        if payload is None:
            corrupt.append(entry["sha256"])
            continue
        if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            corrupt.append(entry["sha256"])
            continue
        if len(payload) != entry["byte_len"]:
            corrupt.append(entry["sha256"])
    return RestoreReport(
        restored=False,
        objects_verified=len(manifest["objects"]) - len(missing) - len(corrupt),
        missing=tuple(missing),
        corrupt=tuple(corrupt),
        reason=None if not (missing or corrupt) else _reason(missing, corrupt),
    )


def restore_backup(source: Path, data_root: Path) -> RestoreReport:
    """Verify first, then stage objects, then swap the warehouse. Never a partial swap."""
    source = Path(source)
    report = verify_backup(source)
    if report.missing or report.corrupt:
        return RestoreReport(
            restored=False,
            objects_verified=report.objects_verified,
            missing=report.missing,
            corrupt=report.corrupt,
            reason=f"restore blocked: {report.reason}",
        )

    data_root = Path(data_root)
    data_root.mkdir(parents=True, exist_ok=True)
    store = RawObjectStore(data_root)
    manifest = load_manifest(source)
    manifest_shas = {entry["sha256"] for entry in manifest["objects"]}

    staged = Path(tempfile.mkdtemp(prefix="restore-", dir=str(data_root)))
    try:
        # Staged through the backup API so a backup directory that still carries a -wal
        # (hand-assembled, or interrupted) is read completely rather than partially.
        staged_warehouse = staged / WAREHOUSE_NAME
        source_conn = sqlite3.connect(source / WAREHOUSE_NAME)
        try:
            _snapshot_into(source_conn, staged_warehouse)
        finally:
            source_conn.close()
        try:
            _validate_staged_warehouse(staged_warehouse, manifest_shas)
        except RestoreBlocked as exc:
            return RestoreReport(
                restored=False,
                objects_verified=len(manifest["objects"]),
                missing=(),
                corrupt=(),
                reason=str(exc),
            )

        for entry in manifest["objects"]:
            payload = _decompress(source / RAW_DIR_NAME / entry["file"])
            if payload is None:
                raise RestoreBlocked(f"unreadable object during restore: {entry['sha256']}")
            ref = store.put(payload)  # re-fsyncs and re-verifies content addressing
            if ref.sha256 != entry["sha256"]:
                raise RestoreBlocked(f"object stored under unexpected hash: {ref.sha256}")

        final = data_root / WAREHOUSE_NAME
        try:
            _swap_warehouse(data_root, final, staged_warehouse)
        except OSError as exc:
            raise RestoreBlocked(
                "cannot swap the warehouse: the file is held open. Stop the collector worker "
                "and the UI before restoring (Win32 does not allow renaming an open database). "
                f"Underlying error: {exc}"
            ) from exc
    except RestoreBlocked as exc:
        return RestoreReport(
            restored=False,
            objects_verified=len(manifest["objects"]),
            missing=(),
            corrupt=(),
            reason=str(exc),
        )
    finally:
        shutil.rmtree(staged, ignore_errors=True)

    return RestoreReport(
        restored=True,
        objects_verified=len(manifest["objects"]),
        missing=(),
        corrupt=(),
        reason=None,
    )


def _swap_warehouse(data_root: Path, final: Path, staged_warehouse: Path) -> None:
    if final.exists():
        # Timestamped, never reused: a second restore must not destroy what the first one kept.
        previous = data_root / f"warehouse.pre-restore-{_stamp()}.sqlite"
        suffix = 0
        while previous.exists():
            suffix += 1
            previous = data_root / f"warehouse.pre-restore-{_stamp()}-{suffix}.sqlite"
        final.replace(previous)
        # Sidecars belong to the database that produced them. Left in place, a stale
        # -wal would be replayed onto the freshly restored one.
        for side in ("-wal", "-shm"):
            stale = data_root / f"{WAREHOUSE_NAME}{side}"
            if stale.exists():
                stale.replace(data_root / f"{previous.name}{side}")
    staged_warehouse.replace(final)
    for side in ("-wal", "-shm"):
        (data_root / f"{WAREHOUSE_NAME}{side}").unlink(missing_ok=True)


def _snapshot_into(source: sqlite3.Connection, destination: Path) -> None:
    """Copy a live database, WAL included, into one self-contained file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        target.commit()
    finally:
        target.close()
    for side in ("-wal", "-shm"):
        sidecar = destination.with_name(destination.name + side)
        if sidecar.exists():
            sidecar.unlink()


def _validate_staged_warehouse(path: Path, manifest_shas: set[str]) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        referenced = {row["sha256"] for row in conn.execute("SELECT sha256 FROM raw_objects")}
        dangling = sorted(referenced - manifest_shas)
        if dangling:
            raise RestoreBlocked(
                f"restored warehouse references {len(dangling)} object(s) absent from the backup, "
                f"first {dangling[0]}"
            )
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RestoreBlocked(f"restored warehouse has {len(violations)} FK violations")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RestoreBlocked("restored warehouse failed integrity_check")
    finally:
        conn.close()


def _decompress(path: Path) -> bytes | None:
    try:
        with gzip.open(path, "rb") as handle:
            return handle.read()
    except (OSError, EOFError, zlib.error, gzip.BadGzipFile):
        return None


def _reason(missing: list[str], corrupt: list[str]) -> str:
    parts = []
    if missing:
        parts.append(f"{len(missing)} object(s) absent: {', '.join(missing[:3])}")
    if corrupt:
        parts.append(f"{len(corrupt)} object(s) failed hash/length: {', '.join(corrupt[:3])}")
    return "; ".join(parts)
