"""Portable activity descriptions. IPC reads use their own read-only connection."""

import json


def describe_job(request):
    if request is None:
        return None
    return {
        "job_id": request.job_id,
        "job_kind": request.job_kind,
        "requested_by": request.requested_by,
        "families": [s.get("family") for s in request.sources],
        "scope": dict(request.scope),
        "background": request.requested_by in ("scoped-maintenance", "automatic-public-deep"),
    }


def maintenance_job(conn, job_id):
    if not job_id.startswith("maintenance-"):
        return None
    row = conn.execute("SELECT * FROM maintenance_tasks WHERE task_key=?", (job_id.removeprefix("maintenance-"),)).fetchone()
    if row is None:
        return None
    return {
        "job_id": job_id, "job_kind": row["kind"], "requested_by": "scoped-maintenance",
        "families": [row["family"]], "background": True,
        "scope": {"from": row["interval_from_utc"], "to": row["interval_to_utc"]},
    }


def maintenance_summary(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='maintenance_tasks'").fetchone():
        return {"present": False}
    counts = dict(conn.execute("SELECT state,COUNT(*) FROM maintenance_tasks GROUP BY state"))
    next_due = conn.execute("SELECT MIN(next_attempt_at) FROM maintenance_tasks WHERE approved=1 AND state='WAITING'").fetchone()[0]
    policy = conn.execute("SELECT value_json FROM settings WHERE key='collection_scope_v1'").fetchone()
    return {"present": True, "counts": counts, "next_attempt_at": next_due,
            "policy": json.loads(policy[0]).get("policy", {}) if policy else {}}
