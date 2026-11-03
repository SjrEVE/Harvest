"""Backup/restore through the production writer and the real raw store (§7.2 UI-10, FAULT-08).

The point of these tests is the *order* of operations in restore: verification happens
before any file is swapped. A restore that half-succeeds leaves a database referencing bytes
that are not on disk, which is strictly worse than not restoring.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from santhau_studio.collectors.extraction import extract_page, verify_extraction
from santhau_studio.collectors.source_registry import binding_for
from santhau_studio.storage import backup
from santhau_studio.storage.rawstore import RawObjectStore
from santhau_studio.storage.warehouse import WarehouseWriter, connect

DATA_ROOT_NAME = "data"


@pytest.fixture()
def env(tmp_path: Path):
    data_root = tmp_path / DATA_ROOT_NAME
    store = RawObjectStore(data_root)
    conn = connect(data_root / "warehouse.sqlite", create_schema=True)
    writer = WarehouseWriter(conn, store)
    writer.register_run("run-1", "job-1", "BACKFILL")
    yield writer, conn, store, data_root
    conn.close()


def commit_page(writer, records: list[dict], *, page_no: int = 0, family: str = "TBMT", attempt_no: int | None = None):
    page = {"code": 200, "page": {"content": records}}
    response_ref = writer.store_response(page)
    attempt = writer.ensure_attempt(
        run_id="run-1", family=family, endpoint="smart/search",
        interval_from="2026-10-31T00:00:00Z", interval_to="2026-11-01T00:00:00Z",
        page_no=page_no, attempt_no=attempt_no if attempt_no is not None else page_no + 1,
        outcome="OK", response=response_ref,
    )
    report = extract_page(page, binding_for(family).spec)
    verify_extraction(page, report)
    return writer.commit_page(run_id="run-1", attempt_id=attempt, response_ref=response_ref, records=report.records)


def scalar(conn: sqlite3.Connection, sql: str, *args: object):
    return conn.execute(sql, args).fetchone()[0]


def json_extract_title(conn: sqlite3.Connection) -> list[str]:
    return [row[0] for row in conn.execute("SELECT json_extract(normalized_json, '$.title') FROM source_record_versions")]


# ------------------------------------------------------------------ happy path

def test_backup_copies_every_referenced_object(env) -> None:
    writer, conn, _, data_root = env
    commit_page(writer, [{"notifyNo": "BK1000", "notifyVersion": "00", "bidName": "alpha"}])
    result = backup.create_backup(data_root)
    rows = scalar(conn, "SELECT COUNT(*) FROM raw_objects")
    assert result.objects == rows
    manifest = json.loads((result.path / backup.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert {e["sha256"] for e in manifest["objects"]} == {r[0] for r in conn.execute("SELECT sha256 FROM raw_objects")}


def test_restore_round_trip_reproduces_the_same_warehouse(env, tmp_path: Path) -> None:
    """Restore into an empty data root; every row and every raw byte must come back readable."""
    writer, conn, _, data_root = env
    commit_page(writer, [{"notifyNo": "BK1001", "notifyVersion": "00", "bidName": "one"}])
    commit_page(writer, [{"notifyNo": "BK1002", "notifyVersion": "00", "bidName": "two"}], page_no=1)
    expected_records = scalar(conn, "SELECT COUNT(*) FROM source_records")
    expected_objects = {row[0] for row in conn.execute("SELECT sha256 FROM raw_objects")}
    payloads = {sha: writer.store.read(sha) for sha in expected_objects}
    result = backup.create_backup(data_root)

    fresh = tmp_path / "restored"
    report = backup.restore_backup(result.path, fresh)
    assert report.restored, report.reason

    restored_conn = connect(fresh / "warehouse.sqlite")
    assert scalar(restored_conn, "SELECT COUNT(*) FROM source_records") == expected_records
    assert {row[0] for row in restored_conn.execute("SELECT sha256 FROM raw_objects")} == expected_objects
    restored_store = RawObjectStore(fresh)
    for sha, payload in payloads.items():
        assert restored_store.read(sha) == payload, "content addressing must survive the round trip"
    assert restored_conn.execute("PRAGMA foreign_key_check").fetchall() == []
    restored_conn.close()


def test_restore_twice_into_the_same_root_does_not_duplicate_objects(env, tmp_path: Path) -> None:
    """Same bytes = same object, even across restores (§5.1 forbids a second copy of identical content)."""
    writer, _, _, data_root = env
    commit_page(writer, [{"notifyNo": "BK1003", "notifyVersion": "00", "bidName": "x"}])
    result = backup.create_backup(data_root)
    scratch = tmp_path / "scratch"
    assert backup.restore_backup(result.path, scratch).restored
    objects_after_first = len(list((scratch / "raw" / "objects").rglob("*.bin.gz")))
    assert backup.restore_backup(result.path, scratch).restored
    assert len(list((scratch / "raw" / "objects").rglob("*.bin.gz"))) == objects_after_first
    with sqlite3.connect(scratch / "warehouse.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_objects").fetchone()[0] == objects_after_first


def test_restore_over_an_open_warehouse_reports_the_real_blocker(env) -> None:
    """Win32 will not rename a database another handle holds; the failure must name the cause."""
    writer, conn, _, data_root = env
    commit_page(writer, [{"notifyNo": "BK1099", "notifyVersion": "00", "bidName": "held"}])
    result = backup.create_backup(data_root)
    report = backup.restore_backup(result.path, data_root)
    assert not report.restored
    assert "held open" in report.reason and "Stop the collector worker" in report.reason
    assert scalar(conn, "SELECT COUNT(*) FROM source_records") == 1, "the live database is still the live one"


def test_verify_backup_writes_nothing(env) -> None:
    writer, _, _, data_root = env
    commit_page(writer, [{"notifyNo": "BK1004", "notifyVersion": "00", "bidName": "y"}])
    result = backup.create_backup(data_root)
    listing = {p.name for p in data_root.iterdir()}
    report = backup.verify_backup(result.path)
    assert report.missing == () and report.corrupt == ()
    assert {p.name for p in data_root.iterdir()} == listing


def test_empty_warehouse_backs_up_as_zero_objects(tmp_path: Path) -> None:
    data_root = tmp_path / DATA_ROOT_NAME
    data_root.mkdir()
    conn = connect(data_root / "warehouse.sqlite", create_schema=True)
    conn.close()
    result = backup.create_backup(data_root)
    assert result.objects == 0
    assert backup.restore_backup(result.path, tmp_path / "restored").restored


# ------------------------------------------------------------------ FAULT-08: blocked swap

def test_backup_missing_one_raw_object_blocks_swap_and_names_it(env) -> None:
    """FAULT-08: one absent object file must abort restore and report that exact hash."""
    writer, conn, _, data_root = env
    commit_page(writer, [{"notifyNo": "BK1005", "notifyVersion": "00", "bidName": "z"}])
    result = backup.create_backup(data_root)
    entries = json.loads((result.path / backup.MANIFEST_NAME).read_text(encoding="utf-8"))["objects"]
    victim = entries[0]["sha256"]
    (result.path / "raw" / entries[0]["file"]).unlink()

    report = backup.restore_backup(result.path, data_root)
    assert not report.restored
    assert report.missing == (victim,), "the report names the missing hash in full"
    assert victim in report.reason
    assert scalar(conn, "SELECT COUNT(*) FROM raw_objects") > 0, "live warehouse untouched"
    assert list(data_root.glob("warehouse.pre-restore*")) == []
    assert list(data_root.glob("restore-*")) == [], "staging temp dir must not leak"


def test_corrupt_raw_object_blocks_swap(env) -> None:
    """A gzip member with flipped bytes decompresses to the wrong content or not at all."""
    writer, _, store, data_root = env
    commit_page(writer, [{"notifyNo": "BK1006", "notifyVersion": "00", "bidName": "w"}])
    result = backup.create_backup(data_root)
    entries = json.loads((result.path / backup.MANIFEST_NAME).read_text(encoding="utf-8"))["objects"]
    target = result.path / "raw" / entries[0]["file"]
    victim = entries[0]["sha256"]
    live_bytes = store.read(victim)
    target.write_bytes(gzip.compress(b"tampered payload"))

    report = backup.restore_backup(result.path, data_root)
    assert not report.restored
    assert report.corrupt == (victim,)
    assert store.read(victim) == live_bytes, "tampering with the copy must not touch live bytes"


def test_manifest_and_warehouse_mismatch_blocks_restore(env) -> None:
    """Gate 2: per-file verification passes, yet the snapshot DB references objects not in the backup."""
    writer, _conn, _, data_root = env
    commit_page(writer, [{"notifyNo": "BK1007", "notifyVersion": "00", "bidName": "v"}])
    result = backup.create_backup(data_root)
    fabricated = hashlib.sha256(b"never stored").hexdigest()
    with sqlite3.connect(result.path / "warehouse.sqlite") as conn2:
        conn2.execute(
            "INSERT INTO raw_objects (sha256, byte_len, codec, storage_rel_path, integrity_status,"
            " redaction_policy_version, created_at_utc) VALUES (?,10,'gzip',?,'OK',1,'2026-11-03T00:00:00Z')",
            (fabricated, f"raw/objects/{fabricated[:2]}/{fabricated}.bin.gz"),
        )

    assert backup.verify_backup(result.path).missing == (), "file-level checks alone would pass"
    report = backup.restore_backup(result.path, data_root)
    assert not report.restored
    assert fabricated in report.reason


def test_backup_captures_transactions_still_in_the_wal(env) -> None:
    """A byte copy of warehouse.sqlite alone loses committed rows parked in the -wal.

    The writer stays open here, exactly as it would in a running app, so the newest
    transaction has not been checkpointed into the main file.
    """
    writer, _conn, _, data_root = env
    commit_page(writer, [{"notifyNo": "BK1020", "notifyVersion": "00", "bidName": "hot"}])
    assert (data_root / "warehouse.sqlite-wal").exists(), "the transaction is still in the WAL"
    result = backup.create_backup(data_root)
    assert not (result.path / "warehouse.sqlite-wal").exists(), "a backup must be one self-contained file"
    with sqlite3.connect(result.path / "warehouse.sqlite") as c:
        assert c.execute("SELECT COUNT(*) FROM source_records").fetchone()[0] == 1
        assert json_extract_title(c) == ["hot"]


def test_restore_refuses_when_manifest_version_is_unknown(env) -> None:
    writer, _, _, data_root = env
    commit_page(writer, [{"notifyNo": "BK1008", "notifyVersion": "00", "bidName": "u"}])
    result = backup.create_backup(data_root)
    manifest_path = result.path / backup.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = 99
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(backup.RestoreBlocked, match="unsupported backup manifest version"):
        backup.restore_backup(result.path, data_root)


def test_backup_refuses_object_row_without_file(env) -> None:
    """A backup that could never restore is refused at creation, not discovered at restore."""
    writer, conn, _, data_root = env
    commit_page(writer, [{"notifyNo": "BK1009", "notifyVersion": "00", "bidName": "t"}])
    ghost = hashlib.sha256(b"row with no bytes").hexdigest()
    conn.execute(
        "INSERT INTO raw_objects (sha256, byte_len, codec, storage_rel_path, integrity_status,"
        " redaction_policy_version, created_at_utc) VALUES (?,10,'gzip',?,'CORRUPT',1,'2026-11-03T00:00:00Z')",
        (ghost, f"raw/objects/{ghost[:2]}/{ghost}.bin.gz"),
    )
    with pytest.raises(backup.RestoreBlocked, match="object row without file"):
        backup.create_backup(data_root)


def test_create_backup_without_warehouse_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(backup.RestoreBlocked, match="no warehouse"):
        backup.create_backup(tmp_path / "nothing-here")


# ------------------------------------------------------------------ §5.2 / §5.5 edges

def test_backup_excludes_orphan_files(env) -> None:
    """§5.2: files with no row are garbage, not data, and must not enter a backup."""
    writer, conn, store, data_root = env
    commit_page(writer, [{"notifyNo": "BK1010", "notifyVersion": "00", "bidName": "s"}])
    orphan = store.put(b"orphan payload, no row references me")
    rows = scalar(conn, "SELECT COUNT(*) FROM raw_objects")
    result = backup.create_backup(data_root)
    assert result.objects == rows
    assert not (result.path / "raw" / f"{orphan.sha256}.bin.gz").exists()


def test_quarantined_object_is_still_backed_up(env) -> None:
    """§5.5 keeps damaged pages visible and on disk; a backup that silently dropped them loses evidence."""
    writer, _conn, store, data_root = env
    ref = writer.store_response({"code": 200, "page": {"content": []}})
    attempt = writer.ensure_attempt(
        run_id="run-1", family="TBMT", endpoint="smart/search",
        interval_from="2026-10-31T00:00:00Z", interval_to="2026-11-01T00:00:00Z",
        page_no=0, attempt_no=1, outcome="OK", response=ref,
    )
    writer.quarantine_response(
        run_id="run-1", attempt_id=attempt, response_ref=ref,
        failure="NOT_JSON", detail="fixture: page body cannot be parsed",
    )
    assert not store.path_for(ref.sha256).exists(), "page moved out of the readable set"
    result = backup.create_backup(data_root)
    assert ref.sha256 in {e["sha256"] for e in json.loads(
        (result.path / backup.MANIFEST_NAME).read_text(encoding="utf-8")
    )["objects"]}
    assert (result.path / "raw" / f"{ref.sha256}.bin.gz").exists()


def test_repeated_restores_keep_each_previous_warehouse(env, tmp_path: Path) -> None:
    """The pre-restore copy is the only rollback left, so names must never collide between restores."""
    writer, _, _, data_root = env
    commit_page(writer, [{"notifyNo": "BK1011", "notifyVersion": "00", "bidName": "r"}])
    result = backup.create_backup(data_root)
    scratch = tmp_path / "scratch"
    for _ in range(4):
        assert backup.restore_backup(result.path, scratch).restored
    kept = sorted(p.name for p in scratch.glob("warehouse.pre-restore*.sqlite"))
    assert len(kept) == 3, f"first restore had no predecessor; expected 3 retained, got {kept}"
    assert len(set(kept)) == 3
    assert (scratch / "warehouse.sqlite").exists()


def test_pre_restore_copy_is_a_usable_rollback(env) -> None:
    """Restoring an older backup over a newer warehouse must keep the newer one readable."""
    writer, conn, _, data_root = env
    commit_page(writer, [{"notifyNo": "BK1012", "notifyVersion": "00", "bidName": "new"}])
    older = backup.create_backup(data_root)
    commit_page(writer, [{"notifyNo": "BK1013", "notifyVersion": "00", "bidName": "second"}], attempt_no=2)
    assert scalar(conn, "SELECT COUNT(*) FROM source_records") == 2
    conn.close()  # the writer must be stopped before its database is swapped out

    report = backup.restore_backup(older.path, data_root)
    assert report.restored
    previous = sorted(data_root.glob("warehouse.pre-restore*.sqlite"))
    assert len(previous) == 1
    with sqlite3.connect(previous[0]) as c:
        assert c.execute("SELECT COUNT(*) FROM source_records").fetchone()[0] == 2
    with sqlite3.connect(data_root / "warehouse.sqlite") as c:
        assert c.execute("SELECT COUNT(*) FROM source_records").fetchone()[0] == 1


def test_restore_leaves_no_stale_wal_beside_the_new_warehouse(env, tmp_path: Path) -> None:
    """A leftover -wal belongs to the replaced database; replaying it onto the restored one is corruption."""
    writer, conn, _, data_root = env
    commit_page(writer, [{"notifyNo": "BK1014", "notifyVersion": "00", "bidName": "q"}])
    result = backup.create_backup(data_root)
    conn.close()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    assert backup.restore_backup(result.path, scratch).restored
    (scratch / "warehouse.sqlite-wal").write_bytes(b"leftover from a crashed writer")
    assert backup.restore_backup(result.path, scratch).restored
    assert not (scratch / "warehouse.sqlite-wal").exists(), "stale sidecar must not survive the swap"
    assert list(scratch.glob("warehouse.pre-restore*-wal")), "the leftover moved with its own database"
    with sqlite3.connect(scratch / "warehouse.sqlite") as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert c.execute("SELECT COUNT(*) FROM source_records").fetchone()[0] == 1
