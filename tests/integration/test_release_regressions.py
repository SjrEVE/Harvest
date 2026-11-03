"""User-visible release failures: real storage, queue, worker and Windows runtime."""

from __future__ import annotations

import json
import ssl
from contextlib import closing
from pathlib import Path

import pytest
from test_backup_restore import commit_page
from test_worker_ipc import job_request, portal_builder

from santhau_studio.collectors.http_client import build_ssl_context
from santhau_studio.platform_win.os_lock import DataRootLock
from santhau_studio.storage import backup, serving
from santhau_studio.storage.warehouse import WarehouseWriter
from santhau_studio.workers.jobs import JobAlreadyQueued, WorkerLoop, claim_next_job, enqueue_job


def seed(root: Path) -> None:
    with closing(WarehouseWriter.open(root, create_schema=True)) as writer:
        writer.register_run("run-1", "job-1", "BACKFILL")
        commit_page(writer, [{"notifyNo": "RC001", "notifyVersion": "00", "bidName": "First"}])


def test_worker_publishes_collected_data_without_manual_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "data"
    enqueue_job(root, job_request())
    loop = WorkerLoop(root, page_source_builder=portal_builder(1), idle_exit_seconds=0.2)
    loop.run()
    assert loop.exit_code == 0, loop.last_error
    state = serving.serving_state(root / "serving.sqlite")
    assert state["present"], "a finished collector must expose its data to the GUI"
    assert state["row_counts"]["source_records"] == 5


def test_malformed_queue_document_does_not_stop_next_job(tmp_path: Path) -> None:
    request = job_request()
    broken = json.loads(request.to_json())
    broken["request_limits"] = None
    queue = tmp_path / "jobs" / "queued"
    queue.mkdir(parents=True)
    (queue / "000-broken.json").write_text(json.dumps(broken), encoding="utf-8")
    enqueue_job(tmp_path, request)
    assert claim_next_job(tmp_path).job_id == request.job_id
    assert (tmp_path / "jobs" / "rejected" / "000-broken.json").exists()


def test_job_identity_cannot_be_requeued_by_changing_timestamp(tmp_path: Path) -> None:
    request = job_request(job_id="stable-id")
    enqueue_job(tmp_path, request)
    with pytest.raises(JobAlreadyQueued):
        enqueue_job(tmp_path, job_request(job_id="stable-id", created_at="2026-11-03T01:00:00Z"))


def test_backup_never_overwrites_an_existing_destination(tmp_path: Path) -> None:
    root = tmp_path / "data"
    seed(root)
    target = tmp_path / "existing"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("untouched", encoding="utf-8")
    with pytest.raises(backup.RestoreBlocked):
        backup.create_backup(root, target)
    assert marker.read_text(encoding="utf-8") == "untouched"
    assert not (target / "warehouse.sqlite").exists()


def test_backup_inventory_matches_the_snapshot_not_an_earlier_read(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "data"
    seed(root)
    original = backup._snapshot_into

    def capture_after_concurrent_commit(conn, destination):
        with closing(WarehouseWriter.open(root)) as writer:
            commit_page(writer, [{"notifyNo": "RC002", "notifyVersion": "00", "bidName": "Second"}], page_no=1)
        original(conn, destination)

    monkeypatch.setattr(backup, "_snapshot_into", capture_after_concurrent_commit)
    result = backup.create_backup(root)
    monkeypatch.setattr(backup, "_snapshot_into", original)
    report = backup.restore_backup(result.path, tmp_path / "restored")
    assert report.restored, report.reason


def test_backup_rejects_paths_outside_its_manifest_root(tmp_path: Path) -> None:
    root = tmp_path / "data"
    seed(root)
    result = backup.create_backup(root)
    path = result.path / backup.MANIFEST_NAME
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["objects"][0]["file"] = "../../outside.bin.gz"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(backup.RestoreBlocked):
        backup.load_manifest(result.path)


def test_restore_respects_the_writer_lock_before_writing_objects(tmp_path: Path) -> None:
    root = tmp_path / "data"
    seed(root)
    result = backup.create_backup(root)
    destination = tmp_path / "busy"
    with DataRootLock(destination):
        report = backup.restore_backup(result.path, destination)
        assert not report.restored
        assert not (destination / "warehouse.sqlite").exists()
        assert not (destination / "raw").exists()


def test_corrupt_snapshot_is_reported_as_unreadable_instead_of_crashing(tmp_path: Path) -> None:
    path = tmp_path / "serving.sqlite"
    path.write_bytes(b"not a database")
    result = serving.serving_state(path)
    assert result["stale"] is True and result["reason"]


def test_failed_publish_is_visible_to_a_reader_of_the_previous_snapshot(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "data"
    seed(root)
    target = root / "serving.sqlite"
    assert serving.build_serving_db(root / "warehouse.sqlite", target).published

    def reject(*_args):
        raise serving.SnapshotRejected("test publication failure")

    monkeypatch.setattr(serving, "_copy_schema_and_rows", reject)
    assert not serving.build_serving_db(root / "warehouse.sqlite", target).published
    state = serving.serving_state(target)
    assert state["stale"] is True
    assert "test publication failure" in state["reason"]


def test_live_tls_keeps_certificate_verification_and_security_floor() -> None:
    context = build_ssl_context("muasamcong.mof.gov.vn")
    assert context.security_level >= 2
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_selected_data_root_is_used_by_views_worker_and_next_launch(tmp_path: Path, monkeypatch) -> None:
    from santhau_studio.desktop.backend import StudioBackend, default_data_root
    from santhau_studio.desktop.bridge import BackendBridge

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "profile"))
    monkeypatch.delenv("SANTHAU_DATA_ROOT", raising=False)
    bridge = BackendBridge(StudioBackend(tmp_path / "before"))
    chosen = tmp_path / "chosen"
    result = json.loads(bridge.selectDataRoot(str(chosen)))
    assert result["ok"], result
    assert bridge.backend.data_root == chosen
    assert bridge.supervisor.backend.data_root == chosen
    assert default_data_root() == chosen


def test_empty_data_root_is_rejected_without_touching_current_directory(tmp_path: Path) -> None:
    from santhau_studio.desktop.backend import StudioBackend

    assert StudioBackend(tmp_path).validate_data_root("")["ok"] is False


def test_shutdown_signal_reaches_all_consumers_and_is_not_cleared_by_one(tmp_path: Path, monkeypatch) -> None:
    from santhau_studio.platform_win.shutdown_signal import (
        close_shutdown_event,
        open_shutdown_event,
        shutdown_requested,
        signal_shutdown,
    )

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    first = open_shutdown_event()
    second = open_shutdown_event()
    assert signal_shutdown()
    assert shutdown_requested(first)
    close_shutdown_event(first)
    assert shutdown_requested(second), "one GUI exiting must not consume the worker's signal"
    assert shutdown_requested(open_shutdown_event()), "a new process must not erase active installer shutdown"


def test_ingestion_scrubs_credentials_before_raw_and_normalized_storage(tmp_path: Path) -> None:
    fake_secret = "synthetic-credential-for-test"
    with closing(WarehouseWriter.open(tmp_path / "data", create_schema=True)) as writer:
        writer.register_run("run-1", "job-1", "BACKFILL")
        commit_page(writer, [{"notifyNo": "REDACT1", "notifyVersion": "00", "accessToken": fake_secret,
                              "bidName": "https://example.invalid/document?token=" + fake_secret}])
        for row in writer.conn.execute("SELECT sha256 FROM raw_objects"):
            assert fake_secret.encode() not in writer.store.read(row[0])
        assert fake_secret not in "\n".join(writer.conn.iterdump())


def test_blocked_source_never_reaches_the_page_builder(tmp_path: Path) -> None:
    root = tmp_path / "data"
    seed(root)
    with closing(WarehouseWriter.open(root)) as writer:
        writer.conn.execute("INSERT INTO source_registry (source_key,display_name,endpoint_family,access_state) VALUES ('egp_home','EGP','egp','BLOCKED')")
    called = []
    enqueue_job(root, job_request())
    loop = WorkerLoop(root, page_source_builder=lambda *_args: called.append(True), idle_exit_seconds=0.1)
    loop.run()
    assert loop.exit_code == 0 and called == []


def test_scheduled_launch_enqueues_only_verified_sources(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from test_worker_ipc import _insert_rule

    root = tmp_path / "data"
    with closing(WarehouseWriter.open(root, create_schema=True)) as writer:
        _insert_rule(writer, job_request())
        writer.conn.execute("INSERT INTO source_registry (source_key,display_name,endpoint_family,access_state) VALUES ('egp_home','EGP','egp','ALLOWED_AND_VERIFIED')")
    loop = WorkerLoop(root, page_source_builder=portal_builder(1), idle_exit_seconds=0.1,
                      scheduled=True, clock=lambda: datetime(2026, 11, 2, tzinfo=UTC))
    loop.run()
    assert loop.exit_code == 0, loop.last_error
    files = list((root / "jobs" / "done").glob("*scheduled*"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8"))["job_kind"] == "INCREMENTAL"
