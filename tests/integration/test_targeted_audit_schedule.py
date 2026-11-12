"""RC3-6: audits must target drifted windows while backfill is still unfinished."""
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

from santhau_studio.workers import scoped_maintenance as sm

TASKS = """
CREATE TABLE maintenance_tasks (
    task_key TEXT PRIMARY KEY, kind TEXT, source_key TEXT, family TEXT,
    interval_from_utc TEXT, interval_to_utc TEXT, context_json TEXT,
    contract_signature TEXT, parent_job_id TEXT, state TEXT,
    next_attempt_at TEXT, approved INTEGER, created_at_utc TEXT,
    updated_at_utc TEXT, attempts INTEGER DEFAULT 0);
"""


def _scope():
    return {
        "date_from": "2026-11-01",
        "date_to": "2026-11-01",
        "sources": [{
            "source_key": "egp_home", "family": "TBMT", "signature": "sig",
            "endpoint": "endpoint", "adapter_sha": "sha",
        }],
        "policy": {
            "reconcile_enabled": True, "reconcile_quota_per_day": 2,
            "reconcile_every_days": 7, "overlap_days": 3,
        },
    }


def _drift_target():
    return [{
        "family": "TBMT",
        "from": "2026-10-31T17:00:00+00:00",
        "to": "2026-11-01T17:00:00+00:00",
        "stored": 12, "source_total": 10,
    }]


def _run(tmp_path, monkeypatch, targets):
    conn = sqlite3.connect(":memory:")
    conn.executescript(TASKS)
    (tmp_path / "serving.sqlite").write_bytes(b"")
    monkeypatch.setattr(
        sm.scope_store, "resolve",
        lambda *_a, **_k: SimpleNamespace(can_skip=False),
    )
    monkeypatch.setattr(
        "santhau_studio.storage.coverage_targets.targeted_reconcile_windows",
        lambda *_a, **_k: targets,
    )
    sm._schedule_audits(conn, _scope(), datetime(2026, 11, 2, tzinfo=UTC), root=tmp_path)
    rows = conn.execute("SELECT kind, family FROM maintenance_tasks ORDER BY task_key").fetchall()
    conn.close()
    return rows


def test_drift_windows_are_audited_even_with_unproven_days(tmp_path, monkeypatch):
    assert _run(tmp_path, monkeypatch, _drift_target()) == [("RECONCILE", "TBMT")]


def test_no_drift_creates_no_audit_tasks(tmp_path, monkeypatch):
    assert _run(tmp_path, monkeypatch, []) == []
