from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from legacy_fixture import make_legacy


def counts(root: Path) -> tuple:
    with sqlite3.connect(root / "warehouse.sqlite") as conn:
        return tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                     for table in ("source_records", "source_record_versions", "raw_revisions", "observations"))


def test_three_imports_preserve_history_without_duplicate_observations(tmp_path: Path) -> None:
    from santhau_studio.storage.import_legacy import import_legacy

    source = make_legacy(tmp_path / "legacy")
    before = hashlib.sha256((source / "warehouse.sqlite").read_bytes()).hexdigest()
    target = tmp_path / "studio"
    first = import_legacy(source, target)
    assert first["status"] == "IMPORTED", first
    assert counts(target) == (1, 3, 4, 5)
    assert import_legacy(source, target)["status"] == "ALREADY_IMPORTED"
    moved = tmp_path / "moved"
    shutil.copytree(source, moved)
    assert import_legacy(moved, target)["status"] == "ALREADY_IMPORTED"
    assert counts(target) == (1, 3, 4, 5)
    assert hashlib.sha256((source / "warehouse.sqlite").read_bytes()).hexdigest() == before
    with sqlite3.connect(target / "warehouse.sqlite") as conn:
        assert conn.execute("SELECT first_seen_at_utc,last_seen_at_utc FROM source_records").fetchone() == (
            "2026-10-01T03:00:00Z", "2026-10-05T03:00:00Z")
        assert conn.execute("SELECT COUNT(*) FROM coverage_proofs").fetchone()[0] == 0
        row = conn.execute("SELECT value_json FROM settings WHERE key LIKE 'legacy:coverage:%'").fetchone()
        assert json.loads(row[0])["classification"] == "LEGACY_COMPLETE_UNVERIFIED"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_newer_archive_then_older_overlap_keeps_latest_and_full_history(tmp_path):
    from santhau_studio.storage.import_legacy import import_legacy
    full = make_legacy(tmp_path / 'full')
    newer = tmp_path / 'newer'
    shutil.copytree(full,newer)
    with sqlite3.connect(newer / 'warehouse.sqlite') as conn:
        conn.execute('DELETE FROM record_versions WHERE version_no < 4')
        conn.execute('DELETE FROM observations WHERE id < 4')
    target = tmp_path / 'studio'
    assert import_legacy(newer,target)['status'] == 'IMPORTED'
    result = import_legacy(full,target)
    assert result['status'] == 'IMPORTED', result
    assert counts(target) == (1,3,4,5)
    with sqlite3.connect(target/'warehouse.sqlite') as conn:
        row = conn.execute('SELECT v.normalized_json,v.fetched_at_utc FROM source_records r JOIN source_record_versions v ON v.source_record_id=r.id AND v.content_version_seq=r.current_content_version').fetchone()
        assert json.loads(row[0])['title'] == 'A'
        assert row[1] == '2026-10-04T03:00:00Z'
    assert import_legacy(full,target)['status'] == 'ALREADY_IMPORTED'


def test_unknown_legacy_family_retains_readable_raw_without_invented_records(tmp_path):
    from santhau_studio.storage.import_legacy import import_legacy
    from santhau_studio.storage.rawstore import RawObjectStore
    source = make_legacy(tmp_path / 'legacy')
    with sqlite3.connect(source / 'warehouse.sqlite') as conn:
        conn.execute("UPDATE records SET record_type='UNMAPPED_LEGACY'")
        conn.execute("UPDATE observations SET record_type='UNMAPPED_LEGACY'")
    root = tmp_path / 'studio'
    result = import_legacy(source,root)
    assert result['status'] == 'PARTIAL'
    assert counts(root) == (0,0,0,0)
    with sqlite3.connect(root/'warehouse.sqlite') as conn:
        rows = conn.execute("SELECT value_json FROM settings WHERE key LIKE 'legacy:rejected:%'").fetchall()
        assert len(rows) == 9
        store = RawObjectStore(root,read_only=True)
        try:
            for row in rows:
                receipt = json.loads(row[0])
                assert receipt['classification'] == 'LEGACY_UNVERIFIED'
                assert 'raw_payload' in json.loads(store.read(receipt['response_sha']))
        finally:
            store.close()


@pytest.mark.parametrize("problem", ["missing", "corrupt", "traversal"])
def test_bad_raw_is_reported_without_fabricating_records(tmp_path: Path, problem: str) -> None:
    from santhau_studio.storage.import_legacy import import_legacy

    source = make_legacy(tmp_path / "legacy")
    raw = source / "raw/old-run/egp_home.jsonl.gz"
    if problem == "missing":
        raw.unlink()
    elif problem == "corrupt":
        raw.write_bytes(b"bad gzip")
    else:
        with sqlite3.connect(source / "warehouse.sqlite") as conn:
            conn.execute("UPDATE observations SET raw_file='../outside.gz'")
            conn.execute("UPDATE record_versions SET raw_file='../outside.gz'")
    result = import_legacy(source, tmp_path / "studio")
    assert result["status"] == "PARTIAL" and result["rejected"] > 0
    assert counts(tmp_path / "studio") == (0, 0, 0, 0)
    assert Path(result["receipt"]).is_file()


def test_interrupted_import_resumes_by_event_identity(tmp_path: Path) -> None:
    from santhau_studio.storage.import_legacy import import_legacy

    source = make_legacy(tmp_path / "legacy")
    target = tmp_path / "studio"
    progress = []
    first = import_legacy(source, target, progress=progress.append, stop=lambda: len(progress) >= 3)
    assert first["status"] == "INTERRUPTED"
    assert import_legacy(source, target)["status"] == "IMPORTED"
    assert counts(target) == (1, 3, 4, 5)


def test_export_bundle_requires_real_raw_and_checks_manifest(tmp_path: Path) -> None:
    from santhau_studio.storage.import_legacy import inspect_legacy

    root = tmp_path / "bundle"
    root.mkdir()
    (root / "records.jsonl").write_text('{}\n', encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({"export_schema_version": 1, "record_count": 1,
       "files": [{"path": "records.jsonl", "sha256": "0" * 64}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        inspect_legacy(root)


def test_valid_export_bundle_imports_versions_without_inventing_observations(tmp_path: Path) -> None:
    from santhau_studio.storage.import_legacy import import_legacy

    root = make_legacy(tmp_path / "old")
    with sqlite3.connect(root / "warehouse.sqlite") as conn:
        conn.row_factory = sqlite3.Row
        versions = [dict(r) for r in conn.execute("SELECT * FROM record_versions")]
    bundle = tmp_path / "bundle"
    shutil.copytree(root / "raw", bundle / "raw")
    data = bundle / "records.jsonl"
    data.write_text(json.dumps({"source": "egp_home", "source_record_id": "LEGACY-001:00",
                               "record_type": "TBMT", "versions": versions}) + "\n", encoding="utf-8")
    (bundle / "manifest.json").write_text(json.dumps({"export_schema_version": 1, "record_count": 1,
        "files": [{"path": "records.jsonl", "sha256": hashlib.sha256(data.read_bytes()).hexdigest()}]}), encoding="utf-8")
    result = import_legacy(bundle, tmp_path / "studio")
    assert result["status"] == "IMPORTED", result
    assert counts(tmp_path / "studio") == (1, 3, 4, 0)


def test_catalog_survives_backup_restore_without_original_archive(tmp_path: Path) -> None:
    from santhau_studio.storage.backup import create_backup, restore_backup
    from santhau_studio.storage.import_legacy import import_legacy
    from santhau_studio.storage.rawstore import RawObjectStore

    root = tmp_path / "studio"
    result = import_legacy(make_legacy(tmp_path / "old"), root)
    saved = create_backup(root)
    fresh = tmp_path / "restored"
    assert restore_backup(saved.path, fresh).restored
    with sqlite3.connect(fresh / "warehouse.sqlite") as conn:
        chunks = json.loads(conn.execute("SELECT value_json FROM settings WHERE key=?",
                            ("legacy:catalog:" + result["batch"],)).fetchone()[0])["chunks"]
    store = RawObjectStore(fresh, read_only=True)
    try:
        rows = [json.loads(line) for sha in chunks for line in store.read(sha).decode().splitlines()]
        assert sum(row["table"] == "observations" for row in rows) == 5
        assert any(row["table"] == "runs" and row["row"]["run_id"] == "old-run" for row in rows)
    finally:
        store.close()
