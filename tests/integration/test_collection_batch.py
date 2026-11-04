"""A single submission queues each permitted family, without inventing source proof."""
import json
import sqlite3
from contextlib import closing

import pytest

from santhau_studio.collectors.offline_acceptance import initialize
from santhau_studio.collectors.source_registry import FAMILY_ORDER
from santhau_studio.desktop.backend import StudioBackend
from santhau_studio.storage.serving import build_serving_db
from santhau_studio.workers.jobs import JobRequest


@pytest.fixture
def backend(tmp_path, monkeypatch):
    initialize(tmp_path)
    monkeypatch.setenv("SANTHAU_OFFLINE_ACCEPTANCE_ROOT", str(tmp_path.resolve()))
    return StudioBackend(tmp_path)


def payload(**extra):
    return {"family": "ALL", "date_from": "01/11/2026", "date_to": "03/11/2026", **extra}


def requests(backend):
    return [JobRequest.from_json(p.read_text(encoding="utf-8"))
            for p in (backend.data_root / "jobs/queued").glob("*.json")]


def keep_tbmt_only(backend):
    with closing(sqlite3.connect(backend.data_root / "warehouse.sqlite")) as conn:
        conn.execute("UPDATE filter_boundary_rules SET canary_verified=0 WHERE family!='TBMT'")
        conn.commit()
    build_serving_db(backend.data_root / "warehouse.sqlite", backend.serving_path)


def test_all_queues_six_independent_jobs_with_same_exact_scope(backend):
    result = backend.enqueue_collection(payload())
    assert result["ok"], result
    jobs = requests(backend)
    assert len(jobs) == 6
    assert {j.sources[0]["family"] for j in jobs} == set(FAMILY_ORDER)
    assert all(len(j.sources) == 1 and j.interval().duration.days == 3 for j in jobs)
    assert len(result["jobs"]) == 6 and not result["blocked"]


def test_all_reports_five_blockers_without_claiming_six_started(backend):
    keep_tbmt_only(backend)
    result = backend.enqueue_collection(payload())
    assert result["ok"] and len(result["jobs"]) == 1, result
    assert result["status"] == "PARTIAL"
    assert {r["family"] for r in result["blocked"]} == set(FAMILY_ORDER) - {"TBMT"}
    assert len(requests(backend)) == 1
    assert all(r["error_code"] == "BOUNDARY_RULE_UNVERIFIED" for r in result["blocked"])


def test_source_refusal_names_family_instead_of_invalid_calendar(backend):
    keep_tbmt_only(backend)
    result = backend.enqueue_collection(payload(family="KHLCNT"))
    assert not result["ok"]
    assert result["error_code"] == "BOUNDARY_RULE_UNVERIFIED"
    assert "Kế hoạch" in result["error"] and "nguồn" in result["error"]
    assert "lọc thời gian" not in result["error"]


@pytest.mark.parametrize("changes", [
    {"date_from": "04/11/2026", "date_to": "01/11/2026"},
    {"request_limits": {"max_requests": 0}},
    {"family": "not-a-family"},
])
def test_batch_validates_before_any_queue_write(backend, changes):
    result = backend.enqueue_collection(payload(**changes))
    assert not result["ok"]
    assert not requests(backend)


def test_start_batch_starts_worker_once_and_retains_diagnostics(backend, monkeypatch):
    from santhau_studio.desktop.supervisor import WorkerSupervisor
    starts = []
    monkeypatch.setattr(WorkerSupervisor, "ensure_running", lambda _: starts.append(1) or {"ok": True})
    result = backend.start_collection(payload())
    assert result["ok"] and len(requests(backend)) == 6
    assert starts == [1]
    report = json.loads((backend.data_root / "logs/collection-last-result.json").read_text(encoding="utf-8"))
    assert report["input"]["date_from"] == "01/11/2026"
    assert len(report["result"]["jobs"]) == 6


def test_overview_keeps_readiness_and_missing_coverage_separate(backend):
    keep_tbmt_only(backend)
    result = backend.collection_overview()
    rows = {r["family"]: r for r in result["families"]}
    assert len(rows) == 6
    assert rows["TBMT"]["readiness"] == "RULE_VERIFIED"
    assert rows["TBMT"]["yesterday_status"] == "NOT_ACCESSED"
    assert rows["KHLCNT"]["readiness"] == "BLOCKED"
    assert rows["KHLCNT"]["reason"]
