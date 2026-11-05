import hashlib
import json

import pytest
from test_weekly_collection import queue

from santhau_studio.collectors.offline_acceptance import initialize
from santhau_studio.desktop.backend import StudioBackend
from santhau_studio.storage.warehouse import connect_read_only
from santhau_studio.workers.collection_setup import configure_collection
from santhau_studio.workers.jobs import JobRequest


def setup(root, monkeypatch):
    initialize(root)
    monkeypatch.setenv("SANTHAU_OFFLINE_ACCEPTANCE_ROOT", str(root.resolve()))
    studio = StudioBackend(root)
    jobs = [queue(studio)["job_id"] for _ in range(2)]
    return jobs


def test_setup_archives_duplicate_jobs_without_losing_documents_or_proof(tmp_path, monkeypatch):
    ids = setup(tmp_path, monkeypatch)
    before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (tmp_path / "jobs" / "queued").glob("*.json")
    }
    c = connect_read_only(tmp_path / "warehouse.sqlite")
    counts = [
        c.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
        for t in ("source_records", "coverage_proofs")
    ]
    c.close()
    result = configure_collection(tmp_path, {"date_from": "2022-01-01", "replace_job_ids": ids})
    assert result["ok"] and set(result["superseded"]) == set(ids)
    assert len(list((tmp_path / "jobs" / "queued").glob("*.json"))) == 1
    assert {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (tmp_path / "jobs" / "superseded").glob("*.json")
    } == before
    request = JobRequest.from_json(
        next((tmp_path / "jobs" / "queued").glob("*.json")).read_text(encoding="utf-8")
    )
    assert len(request.sources) == 6 and request.scope["cadence"] == "WEEK"
    c = connect_read_only(tmp_path / "warehouse.sqlite")
    assert [
        c.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
        for t in ("source_records", "coverage_proofs")
    ] == counts
    assert json.loads(
        c.execute("SELECT value_json FROM settings WHERE key='automatic_public_deep'").fetchone()[0]
    )["enabled"]
    c.close()


@pytest.mark.parametrize(
    "extra", [{"batch_requests": 31}, {"replace_job_ids": ["missing"]}, {"date_from": "2026-11-04"}]
)
def test_setup_refuses_invalid_or_narrowed_replacement_without_queue_changes(
    tmp_path, monkeypatch, extra
):
    ids = setup(tmp_path, monkeypatch)
    files = list((tmp_path / "jobs" / "queued").glob("*.json"))
    before = {p.name: p.read_bytes() for p in files}
    with pytest.raises(ValueError):
        configure_collection(tmp_path, {"date_from": "2022-01-01", "replace_job_ids": ids, **extra})
    assert {p.name: p.read_bytes() for p in (tmp_path / "jobs" / "queued").glob("*.json")} == before
