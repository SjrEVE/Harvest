"""§5.4 crash matrix K1-K6 and the storage-side FAULT items, against the production code path.

The kills are real `os._exit` calls from a second process, injected at named points inside the
production write sequence — not simulated by skipping calls in a test double. `os._exit` is the
point: no `finally`, no cleanup, exactly what a power loss looks like.

The single invariant §5.3 promises is asserted everywhere: **no row, and no reference from a
version or observation, may point at bytes that cannot be read.** Losing an uncommitted page is
allowed; losing consistency is not.

K6 ("killed *during* COMMIT") cannot be hit deterministically from Python, so it is bracketed:
`K6_before` dies immediately before the COMMIT statement and `K6_after` dies immediately after
it. Those are the only two outcomes SQLite can produce, and both are asserted to be complete —
which is what FAULT-14 actually claims.
"""

from __future__ import annotations

import gzip
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from santhau_studio.collectors.extraction import extract_page, verify_extraction
from santhau_studio.collectors.source_registry import binding_for
from santhau_studio.storage import backup, serving
from santhau_studio.storage.rawstore import RawObjectStore
from santhau_studio.storage.warehouse import WarehouseWriter, connect

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
RECORD = {"notifyNo": "FA0001", "notifyVersion": "00", "title": "Gói thầu thử fault"}

CHILD = '''
import gzip, json, os, sys
from pathlib import Path

from santhau_studio.collectors.extraction import extract_page, verify_extraction
from santhau_studio.collectors.source_registry import binding_for
from santhau_studio.storage.rawstore import RawObjectStore
from santhau_studio.storage.warehouse import WarehouseWriter, connect

data_root, kill_at, record_file = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
ATTEMPT_NO = int(sys.argv[4])
# The record comes from a UTF-8 file, not from this source: the parent must commit byte-identical
# content for the idempotency assertions to mean anything.
RECORD = json.loads(record_file.read_text(encoding="utf-8"))


def die(*_a, **_k):
    os._exit(7)


store = RawObjectStore(data_root)
conn = connect(data_root / "warehouse.sqlite")

if kill_at == "K1":
    store.put = die
elif kill_at == "K2":
    _write = gzip.GzipFile.write

    def _k2(self, data, _o=_write):
        _o(self, data)
        die()

    gzip.GzipFile.write = _k2
elif kill_at == "K3":
    os.fsync = die
elif kill_at in ("K4", "K5"):
    _replace = Path.replace

    def _k45(self, target, _o=_replace):
        if kill_at == "K5":
            _o(self, target)
        die()

    Path.replace = _k45
elif kill_at in ("K6_before", "K6_after"):

    class CommitKill:
        def __init__(self, real):
            self._real = real

        def execute(self, sql, *args):
            if sql.strip() == "COMMIT":
                if kill_at == "K6_before":
                    die()
                result = self._real.execute(sql, *args)
                die()
                return result
            return self._real.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._real, name)

    conn = CommitKill(conn)

writer = WarehouseWriter(conn, store)
page = {"code": 200, "page": {"content": [RECORD]}}
response_ref = writer.store_response(page)
attempt = writer.ensure_attempt(
    run_id="run-1", family="TBMT", endpoint="smart/search",
    interval_from="2026-10-31T00:00:00Z", interval_to="2026-11-01T00:00:00Z",
    page_no=0, attempt_no=ATTEMPT_NO, outcome="OK", response=response_ref,
)
report = extract_page(page, binding_for("TBMT").spec)
verify_extraction(page, report)
writer.commit_page(run_id="run-1", attempt_id=attempt, response_ref=response_ref, records=report.records)
print("CHILD-COMPLETED")
'''


@pytest.fixture()
def warehouse(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    conn = connect(data_root / "warehouse.sqlite", create_schema=True)
    writer = WarehouseWriter(conn, RawObjectStore(data_root))
    writer.register_run("run-1", "job-1", "BACKFILL")
    conn.close()
    return data_root


def _run_child(
    data_root: Path, kill_at: str, tmp_path: Path, *, attempt_no: int = 1, record: dict | None = None
) -> int:
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    record_file = tmp_path / "record.json"
    record_file.write_text(json.dumps(record or RECORD, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(script), str(data_root), kill_at, str(record_file), str(attempt_no)],
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH=str(SRC_DIR)),
        timeout=120,
    )
    if proc.returncode not in (0, 7):
        raise AssertionError(f"child failed unexpectedly ({proc.returncode}): {proc.stderr[-800:]}")
    return proc.returncode


def _open(data_root: Path) -> WarehouseWriter:
    """Recovery goes through the same entry point the app uses, lock included."""
    return WarehouseWriter.open(data_root)


def _commit_the_same_page(writer: WarehouseWriter, attempt_no: int = 2) -> None:
    page = {"code": 200, "page": {"content": [RECORD]}}
    ref = writer.store_response(page)
    attempt = writer.ensure_attempt(
        run_id="run-1", family="TBMT", endpoint="smart/search",
        interval_from="2026-10-31T00:00:00Z", interval_to="2026-11-01T00:00:00Z",
        page_no=0, attempt_no=attempt_no, outcome="OK", response=ref,
    )
    report = extract_page(page, binding_for("TBMT").spec)
    verify_extraction(page, report)
    writer.commit_page(run_id="run-1", attempt_id=attempt, response_ref=ref, records=report.records)


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = ("raw_objects", "observations", "source_records", "source_record_versions", "raw_revisions", "page_receipts")
    return {t: int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in tables}


def _files(store: RawObjectStore) -> set[str]:
    return store.scan_files()


def _staging(store: RawObjectStore) -> list[str]:
    return sorted(p.name for p in store.staging_dir.glob("*.part"))


def _assert_no_dangling_reference(conn: sqlite3.Connection, store: RawObjectStore) -> None:
    """§5.3's one promise: every reference resolves to bytes that actually read back."""
    for sha in (row[0] for row in conn.execute("SELECT sha256 FROM raw_objects")):
        store.read(sha)
    for sha in (row[0] for row in conn.execute("SELECT DISTINCT response_object_sha FROM observations")):
        store.read(sha)
    for sha in (row[0] for row in conn.execute("SELECT DISTINCT record_object_sha FROM observations")):
        store.read(sha)


# ------------------------------------------------------------------ K1-K5: killed before COMMIT

@pytest.mark.parametrize("kill_at", ["K1", "K2", "K3", "K4"])
def test_kill_before_the_object_is_durable_leaves_no_row(warehouse, tmp_path: Path, kill_at: str) -> None:
    """K1/K2/K3/K4 all land on the same promise: nothing was committed, so nothing references bytes."""
    rc = _run_child(warehouse, kill_at, tmp_path)
    assert rc == 7, "the child must really have died"
    writer = _open(warehouse)
    try:
        counts = _counts(writer.conn)
        assert counts["raw_objects"] == 0
        assert counts["observations"] == 0
        assert counts["source_records"] == 0
        assert counts["page_receipts"] == 0
        assert _files(writer.store) == set(), "no object reached the readable set"
        _assert_no_dangling_reference(writer.conn, writer.store)
        report = writer.store.recover(writer.conn)
        assert report.rows == 0
        # K2/K3/K4 leave the partial staging file behind; K1 never created one.
        leftover = len(_staging(writer.store))
        assert leftover == (0 if kill_at == "K1" else 1)
        assert writer.store.clear_staging() == leftover
        _commit_the_same_page(writer)  # "tải lại page" — the page is re-fetchable and lands once
    finally:
        writer.close()
    writer = _open(warehouse)
    try:
        assert _counts(writer.conn)["source_records"] == 1
        assert _staging(writer.store) == []
        assert writer.store.recover(writer.conn).clean, "recovery must be clean after the reload"
    finally:
        writer.close()


def test_kill_after_rename_before_commit_leaves_only_a_collectable_orphan(warehouse, tmp_path: Path) -> None:
    """K5 / FAULT-02 / FAULT-16: the bytes exist, no row points at them, and GC can take them back."""
    rc = _run_child(warehouse, "K5", tmp_path)
    assert rc == 7
    writer = _open(warehouse)
    try:
        files = _files(writer.store)
        assert len(files) == 1, "the kill lands at the first rename: the response object is on disk"
        counts = _counts(writer.conn)
        assert counts["raw_objects"] == 0
        assert counts["source_records"] == 0, "no false record may exist"
        assert counts["observations"] == 0
        report = writer.store.recover(writer.conn)
        assert set(report.orphan_files) == files, "recovery names them as orphans, not as corruption"
        assert report.missing_files == () and report.unreadable == ()
        dry = writer.store.collect_orphan_files(writer.conn, dry_run=True)
        assert set(dry) == files
        assert _files(writer.store) == files, "dry run must not delete"
        _assert_no_dangling_reference(writer.conn, writer.store)
        _commit_the_same_page(writer)
        assert set(writer.store.collect_orphan_files(writer.conn, dry_run=False)) <= files
    finally:
        writer.close()


def test_kill_after_rename_keeps_every_existing_reference_readable(warehouse, tmp_path: Path) -> None:
    """The orphan is garbage, not damage: an object written by a *previous* commit stays intact."""
    writer = _open(warehouse)
    _commit_the_same_page(writer, attempt_no=1)
    healthy = set(_files(writer.store))
    writer.close()
    # A distinct body: content addressing means an identical page never reaches the rename,
    # so a page that only repeats known bytes is not a K5 case at all.
    rc = _run_child(warehouse, "K5", tmp_path, attempt_no=2, record=dict(RECORD, notifyNo="FA0002"))
    assert rc == 7
    writer = _open(warehouse)
    try:
        for sha in healthy:
            assert writer.store.read(sha), "an already committed object stays readable"
        report = writer.store.recover(writer.conn)
        assert set(report.orphan_files) == _files(writer.store) - healthy
        assert report.unreadable == () and report.missing_files == ()
        assert _counts(writer.conn)["source_records"] == 1, "the killed page added nothing"
    finally:
        writer.close()


# ------------------------------------------------------------------ K6 / FAULT-14

@pytest.mark.parametrize("kill_at", ["K6_before", "K6_after"])
def test_kill_around_commit_yields_only_complete_states(warehouse, tmp_path: Path, kill_at: str) -> None:
    """FAULT-14: either the whole page is committed or none of it — never a mix of the two."""
    rc = _run_child(warehouse, kill_at, tmp_path)
    assert rc == 7
    writer = _open(warehouse)
    try:
        counts = _counts(writer.conn)
        if kill_at == "K6_after":
            assert counts == {
                "raw_objects": 2,
                "observations": 1,
                "source_records": 1,
                "source_record_versions": 1,
                "raw_revisions": 1,
                "page_receipts": 1,
            }
        else:
            assert counts["observations"] == 0 and counts["source_records"] == 0
        # The mix check: an observation without its record, or a record without its version,
        # would be the half-committed state the matrix forbids.
        orphans = writer.conn.execute(
            "SELECT COUNT(*) FROM observations o LEFT JOIN source_records s"
            " ON s.current_record_object_sha=o.record_object_sha WHERE s.id IS NULL"
        ).fetchone()[0]
        assert orphans == 0
        recordless = writer.conn.execute(
            "SELECT COUNT(*) FROM source_records sr LEFT JOIN source_record_versions v"
            " ON v.source_record_id=sr.id WHERE v.id IS NULL"
        ).fetchone()[0]
        assert recordless == 0
        _assert_no_dangling_reference(writer.conn, writer.store)
    finally:
        writer.close()


def test_commit_is_idempotent_across_a_kill_after_commit(warehouse, tmp_path: Path) -> None:
    """FAULT-03: replaying the page after a completed commit must not duplicate anything."""
    assert _run_child(warehouse, "K6_after", tmp_path) == 7
    writer = _open(warehouse)
    try:
        before = _counts(writer.conn)
        _commit_the_same_page(writer, attempt_no=2)
        after = _counts(writer.conn)
        assert after["source_records"] == before["source_records"] == 1
        assert after["source_record_versions"] == before["source_record_versions"] == 1
        assert after["raw_revisions"] == before["raw_revisions"] == 1
        assert after["observations"] == before["observations"] + 1, "the re-observation is recorded"
        assert after["page_receipts"] == before["page_receipts"], "still one receipt per logical request"
    finally:
        writer.close()


# ------------------------------------------------------------------ FAULT-04 disk full

def test_disk_full_mid_write_leaves_no_row_and_no_silent_repair(tmp_path: Path, monkeypatch) -> None:
    """ENOSPC during the staging write: the page fails loudly and the store holds nothing."""
    import errno

    data_root = tmp_path / "data"
    data_root.mkdir()
    conn = connect(data_root / "warehouse.sqlite", create_schema=True)
    writer = WarehouseWriter(conn, RawObjectStore(data_root))
    writer.register_run("run-1", "job-1", "BACKFILL")
    page = {"code": 200, "page": {"content": [RECORD]}}

    def exploding_write(_self, _data, *_args):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(gzip.GzipFile, "write", exploding_write)
    # The failure lands while writing the response object, i.e. before any row exists.
    with pytest.raises(OSError, match="No space left"):
        writer.store_response(page)

    assert conn.execute("SELECT COUNT(*) FROM raw_objects").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0] == 0
    assert not conn.in_transaction
    assert list(writer.store.objects_dir.rglob("*.bin.gz")) == []
    assert _staging(writer.store) == [], "the .part must be cleaned by the writer's own finally"
    recovery = writer.store.recover(conn)
    assert recovery.rows == 0 and recovery.clean
    assert conn.execute("SELECT COUNT(*) FROM integrity_repairs").fetchone()[0] == 0, "nothing silently 'repaired'"
    conn.close()


def test_disk_full_after_object_written_still_rolls_the_page_back(tmp_path: Path, monkeypatch) -> None:
    """The response object may already be durable; the page's rows must not be."""
    import errno

    data_root = tmp_path / "data"
    data_root.mkdir()
    conn = connect(data_root / "warehouse.sqlite", create_schema=True)
    writer = WarehouseWriter(conn, RawObjectStore(data_root))
    writer.register_run("run-1", "job-1", "BACKFILL")
    page = {"code": 200, "page": {"content": [RECORD]}}
    ref = writer.store_response(page)
    attempt = writer.ensure_attempt(
        run_id="run-1", family="TBMT", endpoint="smart/search",
        interval_from="2026-10-31T00:00:00Z", interval_to="2026-11-01T00:00:00Z",
        page_no=0, attempt_no=1, outcome="OK", response=ref,
    )
    report = extract_page(page, binding_for("TBMT").spec)
    verify_extraction(page, report)

    def exploding_put(_payload):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(writer.store, "put", exploding_put)
    with pytest.raises(OSError, match="No space left"):
        writer.commit_page(run_id="run-1", attempt_id=attempt, response_ref=ref, records=report.records)
    monkeypatch.undo()

    counts = _counts(conn)
    assert counts["source_records"] == 0 and counts["observations"] == 0
    assert counts["raw_objects"] == 1, "the response object was registered before the failure"
    assert counts["page_receipts"] == 0
    assert not conn.in_transaction, "an OSError inside the page must roll the transaction back"
    recovery = writer.store.recover(conn)
    assert recovery.unreadable == () and recovery.missing_files == ()
    _commit_the_same_page(writer)
    assert conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0] == 1
    conn.close()


# ------------------------------------------------------------------ FAULT-13 / FAULT-07

def test_corrupt_gzip_member_is_reported_not_deleted_and_degrades_the_snapshot(
    warehouse: Path, tmp_path: Path
) -> None:
    """FAULT-13 + §5.5: damaged bytes stay visible, other pages keep serving, and the snapshot says so."""
    writer = _open(warehouse)
    _commit_the_same_page(writer, attempt_no=1)
    other_page = {"code": 200, "page": {"content": [dict(RECORD, notifyNo="FA0002", title="Page khác")]}}
    ref = writer.store_response(other_page)
    attempt = writer.ensure_attempt(
        run_id="run-1", family="TBMT", endpoint="smart/search",
        interval_from="2026-11-01T00:00:00Z", interval_to="2026-11-02T00:00:00Z",
        page_no=0, attempt_no=3, outcome="OK", response=ref,
    )
    report = extract_page(other_page, binding_for("TBMT").spec)
    verify_extraction(other_page, report)
    writer.commit_page(run_id="run-1", attempt_id=attempt, response_ref=ref, records=report.records)

    victim = ref.sha256
    path = writer.store.path_for(victim)
    path.write_bytes(b"\x1f\x8b\x08\x00garbage garbage garbage")
    recovery = writer.store.recover(writer.conn)
    assert victim in recovery.unreadable
    assert path.exists(), "§5.1: a damaged object is never deleted or reused"
    assert writer.conn.execute(
        "SELECT COUNT(*) FROM integrity_repairs WHERE action='MARK_CORRUPT'"
    ).fetchone()[0] == 1
    intact = _files(writer.store) - {victim}
    assert intact, "the store holds more than the damaged object"
    for sha in intact:
        assert writer.store.read(sha), "bytes that are still good stay readable"

    result = serving.build_serving_db(warehouse / "warehouse.sqlite", tmp_path / "serving.sqlite")
    assert result.published, result.reason
    assert result.degraded, "a snapshot with unreadable bytes must not be published as complete"
    state = serving.serving_state(tmp_path / "serving.sqlite")
    assert state["degraded"] is True
    assert state["degradation"]["corrupt_objects"] == 1
    writer.close()


def test_quarantined_page_degrades_the_snapshot_and_keeps_other_records_serving(warehouse, tmp_path: Path) -> None:
    """§5.5: quarantine is shown with a warning, never silently hidden and never counted as verified."""
    writer = _open(warehouse)
    _commit_the_same_page(writer, attempt_no=1)
    healthy = serving.build_serving_db(warehouse / "warehouse.sqlite", tmp_path / "clean.sqlite")
    assert healthy.published and not healthy.degraded
    assert serving.serving_state(tmp_path / "clean.sqlite")["degradation"] is None

    ref = writer.store_response({"code": 200, "page": {"content": []}})
    attempt = writer.ensure_attempt(
        run_id="run-1", family="TBMT", endpoint="smart/search",
        interval_from="2026-11-01T00:00:00Z", interval_to="2026-11-02T00:00:00Z",
        page_no=0, attempt_no=2, outcome="OK", response=ref,
    )
    writer.quarantine_response(
        run_id="run-1", attempt_id=attempt, response_ref=ref,
        failure="GZIP_MEMBER_CORRUPT", detail="fixture: member fails CRC",
    )
    assert not writer.store.path_for(ref.sha256).exists()
    assert (writer.store.quarantine_dir / f"{ref.sha256}.bin.gz").exists(), "§5.5: kept, not destroyed"

    degraded = serving.build_serving_db(warehouse / "warehouse.sqlite", tmp_path / "snap.sqlite")
    assert degraded.published and degraded.degraded
    state = serving.serving_state(tmp_path / "snap.sqlite")
    assert state["degradation"]["quarantined_pages"] == 1
    snapshot = sqlite3.connect(tmp_path / "snap.sqlite")
    assert snapshot.execute("SELECT COUNT(*) FROM source_records").fetchone()[0] == 1, "the good page still serves"
    assert snapshot.execute("SELECT COUNT(*) FROM quarantined_responses").fetchone()[0] == 1, "and the warning is visible"
    snapshot.close()
    writer.close()


def test_healthy_store_publishes_without_a_degradation_marker(warehouse, tmp_path: Path) -> None:
    writer = _open(warehouse)
    _commit_the_same_page(writer, attempt_no=1)
    writer.close()
    result = serving.build_serving_db(warehouse / "warehouse.sqlite", tmp_path / "serving.sqlite")
    assert result.published and not result.degraded
    assert serving.serving_state(tmp_path / "serving.sqlite")["degradation"] is None


# ------------------------------------------------------------------ FAULT-09

def test_restore_refuses_a_warehouse_with_fk_violations(warehouse, tmp_path: Path) -> None:
    """A snapshot whose rows no longer hang together must never swap in, however complete its files look."""
    writer = _open(warehouse)
    _commit_the_same_page(writer, attempt_no=1)
    result = backup.create_backup(warehouse)
    writer.close()

    tampered = connect(result.path / "warehouse.sqlite")
    try:
        tampered.execute("PRAGMA foreign_keys=OFF")
        tampered.execute("DELETE FROM fetch_attempts")
    finally:
        tampered.close()
    report = backup.restore_backup(result.path, tmp_path / "restored")
    assert not report.restored
    assert "FK violations" in report.reason
    assert not (tmp_path / "restored" / "warehouse.sqlite").exists(), "refusal must not create a restored root"


def test_backup_manifest_with_wrong_object_count_is_refused(warehouse, tmp_path: Path) -> None:
    writer = _open(warehouse)
    _commit_the_same_page(writer, attempt_no=1)
    result = backup.create_backup(warehouse)
    writer.close()

    manifest_path = result.path / backup.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["object_count"] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(backup.RestoreBlocked, match="object_count disagrees"):
        backup.restore_backup(result.path, tmp_path / "restored")
