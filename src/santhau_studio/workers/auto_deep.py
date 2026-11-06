"""Opt-in deep backfill on the existing writer, between index children and when idle."""

import json
import time
import uuid
from datetime import UTC, datetime, timedelta

KEY = "automatic_public_deep"
LANES = ("HSMT", "REOFFER", "DETAIL")


def validated_configuration(value):
    if type(value.get("enabled")) is not bool:
        raise ValueError("enabled must be boolean")
    count = value.get("batch_requests", 30)
    if type(count) is not int or not 1 <= count <= 30:
        raise ValueError("batch_requests must be 1..30")
    config = {
        "enabled": value["enabled"],
        "batch_requests": count,
        "scan_interval_seconds": 60,
        "failure_cooldown_minutes": 60,
        "lanes": list(LANES),
        "pacing_seconds": 3,
    }
    return config


def configure(conn, value):
    config = validated_configuration(value)
    conn.execute(
        "INSERT OR REPLACE INTO settings(key,value_json,updated_at_utc) VALUES(?,?,?)",
        (KEY, json.dumps(config), datetime.now(UTC).isoformat().replace("+00:00", "Z")),
    )
    return config


def configuration(conn):
    row = conn.execute("SELECT value_json FROM settings WHERE key=?", (KEY,)).fetchone()
    return json.loads(row[0]) if row else {"enabled": False}


def candidates(conn, limit):
    config = configuration(conn)
    if not config.get("enabled"):
        return []
    from santhau_studio.collectors.cgttrg import ENDPOINTS, PREFIX
    from santhau_studio.collectors.public_deep import PublicDeepFetcher

    lanes = [
        lane
        for lane in LANES
        if PREFIX + ENDPOINTS[lane] not in PublicDeepFetcher.blocked_endpoints
    ]
    if not lanes:
        return []
    now = datetime.now(UTC)
    cutoff = (now - timedelta(minutes=config["failure_cooldown_minutes"])).isoformat()
    placeholders = " UNION ALL ".join("SELECT ? AS lane" for _ in lanes)
    query = """SELECT r.id,r.family,r.natural_key,lane.lane FROM source_records r
      JOIN source_record_versions v ON v.source_record_id=r.id AND v.content_version_seq=r.current_content_version
      CROSS JOIN (LANES_SQL) lane
      LEFT JOIN deep_tasks t ON t.source_record_id=r.id AND t.lane=lane.lane
      LEFT JOIN deep_captures c ON c.id=t.capture_id
      WHERE r.source_key='egp_home' AND r.family='TBMT' AND json_extract(v.normalized_json,'$.bidForm')='CGTTRG'
      AND (t.source_record_id IS NULL
        OR (t.state IN ('FETCHED','SOURCE_EMPTY') AND
            (t.listing_object_sha<>r.current_record_object_sha OR c.stale=1 OR c.refresh_due_at_utc<?))
        OR (t.state NOT IN ('FETCHED','SOURCE_EMPTY') AND t.updated_at_utc<?))
      ORDER BY r.id,CASE lane.lane WHEN 'HSMT' THEN 0 WHEN 'REOFFER' THEN 1 ELSE 2 END LIMIT ?""".replace(
        "LANES_SQL", placeholders
    )
    return [
        dict(r)
        for r in conn.execute(
            query,
            (
                *lanes,
                now.isoformat().replace("+00:00", "Z"),
                cutoff,
                min(limit, config["batch_requests"]),
            ),
        )
    ]


def run_batch(loop, parent_run=None):
    from santhau_studio.workers.jobs import JobRequest

    if loop.flag.interrupt_requested or loop._writer is None:
        return 0
    config = configuration(loop._writer.conn)
    if not config.get("enabled") or time.monotonic() < getattr(loop, "_deep_next_scan", 0):
        return 0
    loop._deep_next_scan = time.monotonic() + config["scan_interval_seconds"]
    selected = candidates(loop._writer.conn, config["batch_requests"])
    previous_phase, previous_progress = loop._phase, loop.progress
    completed = 0
    requests_used = 0
    try:
        for target in selected:
            if loop.flag.interrupt_requested or requests_used >= config["batch_requests"]:
                break
            request = JobRequest(
                job_id="auto-deep-" + uuid.uuid4().hex[:16],
                job_kind=target["lane"],
                requested_by="automatic-public-deep",
                sources=({"source_key": "egp_home", "family": "TBMT"},),
                scope={"record_ids": [target["id"]]},
                request_limits={
                    "max_requests": min(4, config["batch_requests"] - requests_used),
                    "max_attempts": 4,
                    "max_seconds": 90,
                    "pacing_seconds": 3,
                },
                created_at=datetime.now(UTC).isoformat(),
            )
            run_id = "run-" + uuid.uuid4().hex
            loop._writer.register_run(
                run_id, request.job_id, request.job_kind, requested_by=request.requested_by
            )
            loop._set_phase("DEEP_COLLECTING")
            loop._set_progress(
                {
                    "family": "TBMT",
                    "lane": target["lane"],
                    "natural_key": target["natural_key"],
                    "automatic": True,
                    "completed_in_batch": completed,
                    "batch_size": len(selected),
                }
            )
            try:
                status, detail = loop._dispatch(request, run_id)
            except Exception as exc:
                from santhau_studio.collectors.network_policy import scrub_exception
                from santhau_studio.storage.public_deep import task_state

                status, detail = "FAILED", {"error": scrub_exception(exc)}
                task_state(
                    loop._writer.conn, target["id"], target["lane"], "FAILED", error=detail["error"]
                )
            loop._writer.finish_run(run_id, status)
            loop._writer.conn.execute(
                "INSERT INTO run_tasks(run_id,task_kind,status,detail_json) VALUES(?,?,?,?)",
                (
                    run_id,
                    request.job_kind,
                    status,
                    json.dumps({**detail, "automatic": True, "parent_run": parent_run}),
                ),
            )
            completed += 1
            requests_used += max(1, detail.get("requests_used", 1))
    finally:
        loop._set_phase(previous_phase)
        loop.progress = previous_progress
    return completed


def enqueue_idle_batch(loop):
    """An idle sweep is a real queue job, so normal pause/resume/stop still apply."""
    from santhau_studio.workers.jobs import JobRequest, enqueue_job

    if loop._paused_jobs or loop.flag.interrupt_requested:
        return False
    if time.monotonic() < getattr(loop, "_deep_next_enqueue_scan", 0):
        return False
    loop._deep_next_enqueue_scan = time.monotonic() + 60
    if not candidates(loop._writer.conn, 1):
        return False
    request = JobRequest(
        "auto-deep-batch-" + uuid.uuid4().hex[:12],
        "HSMT",
        "automatic-public-deep",
        ({"source_key": "egp_home", "family": "TBMT"},),
        {"automatic_deep_batch": True},
        created_at=datetime.now(UTC).isoformat(),
    )
    enqueue_job(loop.data_root, request)
    return True
