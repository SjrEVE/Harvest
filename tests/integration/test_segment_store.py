"""Segment raw store: framing, rotation, crash windows, compaction, dual-read and backup.

Crash windows are simulated with the store's fault hook (an exception raised between two durable
steps), then a fresh store instance is opened — exactly what a restart does. The assertions are
about what an object store must guarantee: indexed objects are readable, torn tails are gone,
and no crash window makes a row point at bytes nobody can read.
"""

from __future__ import annotations

import gzip
import hashlib
from pathlib import Path

import pytest

from santhau_studio.storage import backup
from santhau_studio.storage.rawstore import (
    LAYOUT_OBJECTS,
    LAYOUT_SEGMENTS,
    ObjectCorruptError,
    RawObjectStore,
)
from santhau_studio.storage.segments import HEADER, SegmentStore
from santhau_studio.storage.warehouse import WarehouseWriter, connect


class Boom(RuntimeError):
    pass


def fail_at(stage: str):
    def hook(actual: str) -> None:
        if actual == stage:
            raise Boom(stage)

    return hook


def payloads(count: int) -> list[bytes]:
    return [f'{{"n": {i}, "text": "Gói thầu số {i}"}}'.encode() for i in range(count)]


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    return tmp_path / "data"


# ------------------------------------------------------------------ basics

def test_put_read_roundtrip_and_dedup(root: Path) -> None:
    store = RawObjectStore(root, layout=LAYOUT_SEGMENTS)
    try:
        first = store.put(b"hello")
        again = store.put(b"hello")
        assert first.sha256 == sha(b"hello")
        assert again.already_existed is True and again.sha256 == first.sha256
        assert first.segment == 1 and first.offset == 0
        assert first.storage_rel_path.startswith("raw/segments/seg-00000001.bin.gz#")
        assert store.read(first.sha256) == b"hello"
        count, bad = store.segments.verify_all()
        assert count == 1 and bad == ()
    finally:
        store.close()


def test_rotation_opens_the_next_segment(root: Path) -> None:
    store = RawObjectStore(root, layout=LAYOUT_SEGMENTS, segment_bytes=400)
    try:
        refs = [store.put(payload) for payload in payloads(20)]
        segments = {ref.segment for ref in refs}
        assert len(segments) > 1, "small segment limit must rotate"
        assert all(store.read(ref.sha256) is not None for ref in refs)
        assert len(list((root / "raw" / "segments").glob("seg-*.bin.gz"))) == len(segments)
    finally:
        store.close()


def test_restart_continues_the_last_open_segment(root: Path) -> None:
    first = RawObjectStore(root, layout=LAYOUT_SEGMENTS)
    ref_a = first.put(b"first")
    first.close()
    second = RawObjectStore(root, layout=LAYOUT_SEGMENTS)
    try:
        ref_b = second.put(b"second")
        assert ref_b.segment == ref_a.segment, "restart must append to the open segment, not rotate"
        assert second.read(ref_a.sha256) == b"first"
    finally:
        second.close()


def test_missing_object_is_reported_not_invented(root: Path) -> None:
    store = RawObjectStore(root, layout=LAYOUT_SEGMENTS)
    try:
        with pytest.raises(ObjectCorruptError):
            store.read("0" * 64)
    finally:
        store.close()


def test_tampered_blob_fails_verification(root: Path) -> None:
    store = RawObjectStore(root, layout=LAYOUT_SEGMENTS)
    try:
        ref = store.put(b"tamper me")
        segment = root / "raw" / "segments" / "seg-00000001.bin.gz"
        data = bytearray(segment.read_bytes())
        data[-1] ^= 0xFF  # flip a blob byte; the frame CRC must notice
        segment.write_bytes(bytes(data))
        with pytest.raises(ObjectCorruptError):
            store.read(ref.sha256)
        _, bad = store.segments.verify_all()
        assert bad == (ref.sha256,)
    finally:
        store.close()


# ------------------------------------------------------------------ crash windows

def test_torn_tail_is_truncated_on_reopen(root: Path) -> None:
    store = RawObjectStore(root, layout=LAYOUT_SEGMENTS)
    ref = store.put(b"good object")
    store.close()
    segment = root / "raw" / "segments" / "seg-00000001.bin.gz"
    with segment.open("ab") as handle:
        handle.write(b"SGS1partial-frame-without-its-blob")
    reopened = RawObjectStore(root, layout=LAYOUT_SEGMENTS)
    try:
        assert reopened.read(ref.sha256) == b"good object"
        assert b"partial-frame" not in segment.read_bytes(), "torn tail must be truncated"
        after = reopened.put(b"after recovery")
        assert reopened.read(after.sha256) == b"after recovery"
    finally:
        reopened.close()


def test_crash_after_fsync_before_index_is_recovered(root: Path) -> None:
    store = RawObjectStore(root, layout=LAYOUT_SEGMENTS, fault=fail_at("after_fsync"))
    with pytest.raises(Boom):
        store.put(b"durable but unindexed")
    store.close()
    reopened = RawObjectStore(root, layout=LAYOUT_SEGMENTS)
    try:
        assert reopened.read(sha(b"durable but unindexed")) == b"durable but unindexed"
    finally:
        reopened.close()


def test_crash_after_index_keeps_the_object(root: Path) -> None:
    store = RawObjectStore(root, layout=LAYOUT_SEGMENTS, fault=fail_at("after_index"))
    with pytest.raises(Boom):
        store.put(b"indexed and readable")
    store.close()
    reopened = RawObjectStore(root, layout=LAYOUT_SEGMENTS)
    try:
        assert reopened.read(sha(b"indexed and readable")) == b"indexed and readable"
    finally:
        reopened.close()


def test_partial_append_is_truncated_and_earlier_objects_survive(root: Path) -> None:
    store = RawObjectStore(root, layout=LAYOUT_SEGMENTS)
    keep = store.put(b"before the crash")
    store.close()
    segment = root / "raw" / "segments" / "seg-00000001.bin.gz"
    with segment.open("ab") as handle:
        handle.write(b"SGS1\x00" + b"\x00" * 20)  # header cut off mid-write
    reopened = RawObjectStore(root, layout=LAYOUT_SEGMENTS)
    try:
        assert reopened.read(keep.sha256) == b"before the crash"
        assert reopened.read(keep.sha256) is not None
    finally:
        reopened.close()


# ------------------------------------------------------------------ warehouse reconciliation

def test_recover_reconciles_segment_objects_against_rows(root: Path) -> None:
    writer = WarehouseWriter.open(root, create_schema=True, raw_layout=LAYOUT_SEGMENTS)
    try:
        ref = writer.store.put(b"row object")
        writer.register_object(ref)
        orphan = writer.store.put(b"orphan object")
        writer.conn.commit()
        report = writer.store.recover(writer.conn)
        assert report.missing_files == () and report.unreadable == ()
        assert report.orphan_files == (orphan.sha256,)
        assert writer.store.segment_orphans(writer.conn) == (orphan.sha256,)

        segment = root / "raw" / "segments" / "seg-00000001.bin.gz"
        data = bytearray(segment.read_bytes())
        data[HEADER.size + 5] ^= 0xFF  # flip a byte inside the *row* object's blob
        segment.write_bytes(bytes(data))
        broken = writer.store.recover(writer.conn)
        assert ref.sha256 in broken.unreadable
        status = writer.conn.execute(
            "SELECT integrity_status FROM raw_objects WHERE sha256=?", (ref.sha256,)
        ).fetchone()[0]
        assert status == "CORRUPT"
    finally:
        writer.close()


def test_quarantine_export_leaves_the_segment_untouched(root: Path) -> None:
    writer = WarehouseWriter.open(root, create_schema=True, raw_layout=LAYOUT_SEGMENTS)
    try:
        ref = writer.store.put(b"page bytes")
        segment = root / "raw" / "segments" / "seg-00000001.bin.gz"
        before = segment.read_bytes()
        target = writer.store.quarantine_page(ref.sha256)
        assert target.exists()
        with gzip.open(target, "rb") as handle:
            assert handle.read() == b"page bytes"
        assert segment.read_bytes() == before, "evidence is immutable inside a segment"
        assert writer.store.read(ref.sha256) == b"page bytes"
    finally:
        writer.close()


# ------------------------------------------------------------------ dual read

def test_dual_read_serves_legacy_and_segment_objects(root: Path) -> None:
    legacy = RawObjectStore(root, layout=LAYOUT_OBJECTS)
    legacy_ref = legacy.put(b"legacy object")
    legacy.close()
    segmented = RawObjectStore(root, layout=LAYOUT_SEGMENTS)
    try:
        new_ref = segmented.put(b"segment object")
        assert segmented.read(legacy_ref.sha256) == b"legacy object"
        assert segmented.read(new_ref.sha256) == b"segment object"
        assert {legacy_ref.sha256, new_ref.sha256} <= segmented.known_shas()
    finally:
        segmented.close()


# ------------------------------------------------------------------ compaction

def test_compact_drops_orphans_and_keeps_live_objects(root: Path) -> None:
    writer = WarehouseWriter.open(root, create_schema=True, raw_layout=LAYOUT_SEGMENTS, )
    try:
        live = [writer.store.put(f"live {i}".encode()) for i in range(5)]
        orphan = writer.store.put(b"never referenced")
        for ref in live:
            writer.register_object(ref)
        writer.conn.commit()
        result = writer.store.compact_segments(writer.conn)
        assert result["kept"] == 5 and result["dropped"] == 1
        assert not writer.store.segments.has(orphan.sha256)
        assert all(writer.store.read(ref.sha256).startswith(b"live") for ref in live)
        count, bad = writer.store.segments.verify_all()
        assert count == 5 and bad == ()
        assert not (root / "raw" / "segments-swap.json").exists()
    finally:
        writer.close()


def test_compact_crash_after_stage_rolls_back(root: Path) -> None:
    store = RawObjectStore(root, layout=LAYOUT_SEGMENTS, fault=fail_at("compact_after_stage"))
    first = store.put(b"kept")
    with pytest.raises(Boom):
        store.segments.compact({first.sha256})
    store.close()
    reopened = RawObjectStore(root, layout=LAYOUT_SEGMENTS)
    try:
        assert reopened.read(first.sha256) == b"kept"
        assert not (root / "raw" / "segments.compact").exists()
    finally:
        reopened.close()


def test_compact_crash_after_retire_still_serves_every_object(root: Path) -> None:
    store = RawObjectStore(root, layout=LAYOUT_SEGMENTS, fault=fail_at("compact_after_retire"))
    refs = [store.put(f"obj {i}".encode()) for i in range(4)]
    with pytest.raises(Boom):
        store.segments.compact({ref.sha256 for ref in refs})
    store.close()
    reopened = RawObjectStore(root, layout=LAYOUT_SEGMENTS)
    try:
        assert all(reopened.read(ref.sha256) == f"obj {i}".encode() for i, ref in enumerate(refs))
        assert not (root / "raw" / "segments-swap.json").exists()
        assert not list((root / "raw").glob("segments.old-*"))
    finally:
        reopened.close()


# ------------------------------------------------------------------ backup / restore

def test_backup_and_restore_roundtrip_over_segments(root: Path, tmp_path: Path) -> None:
    writer = WarehouseWriter.open(root, create_schema=True, raw_layout=LAYOUT_SEGMENTS)
    try:
        refs = []
        for i in range(3):
            ref = writer.store.put(f"backup payload {i}".encode())
            writer.register_object(ref)
            refs.append(ref)
        writer.conn.commit()
    finally:
        writer.close()

    archive = tmp_path / "backup"
    result = backup.create_backup(root, archive)
    assert result.objects == 3
    assert backup.verify_backup(archive).missing == ()

    fresh = tmp_path / "restored"
    report = backup.restore_backup(archive, fresh)
    assert report.restored, report.reason
    store = RawObjectStore(fresh, layout=LAYOUT_SEGMENTS)
    try:
        assert [store.read(ref.sha256) for ref in refs] == [f"backup payload {i}".encode() for i in range(3)]
    finally:
        store.close()


def test_segment_store_known_shas_matches_index(root: Path) -> None:
    with SegmentStore(root) as store:
        refs = [store.put(payload)[0] for payload in payloads(3)]
        assert store.known_shas() == set(refs)
        conn = connect(root / "warehouse.sqlite", create_schema=True)
        try:
            assert conn.execute("SELECT COUNT(*) FROM raw_objects").fetchone()[0] == 0
        finally:
            conn.close()
