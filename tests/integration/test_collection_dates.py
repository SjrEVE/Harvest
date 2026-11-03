"""A calendar range means whole Vietnamese days, including the selected last day."""
from datetime import timedelta

import pytest

from santhau_studio.collectors.offline_acceptance import initialize
from santhau_studio.desktop.backend import StudioBackend
from santhau_studio.workers.jobs import JobRequest


@pytest.fixture
def calendar_backend(tmp_path, monkeypatch):
    root = tmp_path / "calendar-data"
    initialize(root)
    monkeypatch.setenv("SANTHAU_OFFLINE_ACCEPTANCE_ROOT", str(root.resolve()))
    return StudioBackend(root)


def queued(backend, result):
    assert result["ok"], result
    path = next((backend.data_root / "jobs/queued").glob(f"*-{result['job_id']}.json"))
    return JobRequest.from_json(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(("first", "last", "start", "end", "days"), [
    ("01/11/2026", "03/11/2026", "2026-10-31T17:00:00+00:00", "2026-11-03T17:00:00+00:00", 3),
    ("2026-11-01", "2026-11-01", "2026-10-31T17:00:00+00:00", "2026-11-01T17:00:00+00:00", 1),
    ("28/02/2024", "01/03/2024", "2024-02-27T17:00:00+00:00", "2024-03-01T17:00:00+00:00", 3),
    ("31/12/2025", "01/01/2026", "2025-12-30T17:00:00+00:00", "2026-01-01T17:00:00+00:00", 2),
])
def test_calendar_range_queues_exact_inclusive_vietnam_days(calendar_backend, first, last, start, end, days):
    result = calendar_backend.enqueue_collection({"job_kind":"BACKFILL", "family":"TBMT",
                                                  "date_from":first, "date_to":last})
    request = queued(calendar_backend, result)
    assert request.scope == {"from":start, "to":end}
    assert request.interval().duration == timedelta(days=days)


@pytest.mark.parametrize(("first", "last"), [
    ("", "03/11/2026"), ("01/11/2026", ""), ("31/02/2026", "03/03/2026"),
    ("03/11/2026", "01/11/2026"), ("29/02/2025", "01/03/2025"),
    ("01/01/2099", "02/01/2099"),
])
def test_invalid_calendar_range_explained_before_source_gate(tmp_path, first, last):
    backend = StudioBackend(tmp_path)
    result = backend.enqueue_collection({"family":"TBMT", "date_from":first, "date_to":last})
    assert result["ok"] is False
    assert result["error_code"] == "DATE_RANGE_INVALID"
    assert "ngày" in result["error"].lower()
    assert not list((tmp_path / "jobs/queued").glob("*.json"))


def test_explicit_timestamp_api_keeps_its_exclusive_endpoint(calendar_backend):
    scope = {"from":"2026-11-01T09:15:00+07:00", "to":"2026-11-01T11:30:00+07:00"}
    request = queued(calendar_backend, calendar_backend.enqueue_collection({"family":"TBMT", **scope}))
    assert request.scope == scope
    assert request.interval().duration == timedelta(hours=2, minutes=15)


def test_automatic_incremental_needs_no_dummy_dates(calendar_backend):
    result = calendar_backend.enqueue_collection({"job_kind":"INCREMENTAL", "family":"TBMT", "auto_range":True})
    assert queued(calendar_backend, result).job_kind == "INCREMENTAL"


def test_manual_dates_are_never_silently_ignored_by_incremental(calendar_backend):
    result = calendar_backend.enqueue_collection({"job_kind":"INCREMENTAL", "family":"TBMT",
                                                  "date_from":"01/11/2026", "date_to":"03/11/2026"})
    assert not result["ok"]
    assert "tự động" in result["error"]


def test_normal_launch_cannot_enqueue_jobs_in_a_synthetic_root(calendar_backend, monkeypatch):
    monkeypatch.delenv("SANTHAU_OFFLINE_ACCEPTANCE_ROOT")
    result = calendar_backend.enqueue_collection({"family":"TBMT", "date_from":"01/11/2026", "date_to":"01/11/2026"})
    assert not result["ok"] and result["error_code"] == "SYNTHETIC_ROOT"
    assert calendar_backend.jobs()["collection_blocked_reason"]
    assert not list((calendar_backend.data_root / "jobs/queued").glob("*.json"))


def test_start_collection_queues_and_explicitly_starts_worker(calendar_backend, monkeypatch):
    from santhau_studio.desktop.supervisor import WorkerSupervisor
    roots = []
    def start(supervisor):
        roots.append(supervisor.backend.data_root)
        return {"ok":True, "spawned":True}
    monkeypatch.setattr(WorkerSupervisor, "ensure_running", start)
    result = calendar_backend.start_collection({"family":"TBMT", "date_from":"01/11/2026", "date_to":"01/11/2026"})
    assert queued(calendar_backend, result).interval().duration == timedelta(days=1)
    assert roots == [calendar_backend.data_root]
    assert result["worker_started"]


def test_start_failure_keeps_request_and_explains_it(calendar_backend, monkeypatch):
    from santhau_studio.desktop.supervisor import WorkerSupervisor
    monkeypatch.setattr(WorkerSupervisor, "ensure_running", lambda _: {"ok":False,"error":"không tìm thấy bộ thu thập"})
    result = calendar_backend.start_collection({"family":"TBMT", "date_from":"01/11/2026", "date_to":"01/11/2026"})
    assert not result["ok"] and result["job_id"]
    assert "hàng đợi" in result["error"]
    assert len(list((calendar_backend.data_root / "jobs/queued").glob("*.json"))) == 1
