"""Opt-in deep backfill on the existing writer, between index children and when idle.

Retry policy (see ``storage.deep_retry``): a failed lane task is retried on an
escalating cooldown -- 1h, then 6h, then 24h, then 72h -- and after five
consecutive non-transient failures it is quarantined: auto-deep leaves it
alone until the listing changes, a parser/adapter bump could fix it, or an
operator releases it with ``deep-retry``. Transient failures (timeouts, host
blocks) never quarantine and keep recovering on their own.

Job shape: one job per lane per scan (at most 10 records per job), not one job
per (package, lane) pair. The fetcher budget caps a job at 30 requests, so 10
records leave headroom for retries; the shared ``batch_requests`` budget still
caps the whole scan.
"""

import json
import time
import uuid
from datetime import UTC, datetime

KEY = "automatic_public_deep"
LANES = ("HSMT", "REOFFER", "DETAIL", "M1")

# Records per batched lane job. PublicDeepFetcher caps a job at 30 requests;
# 10 records x 2 requests of retry headroom stays inside that cap.
MAX_RECORDS_PER_DEEP_JOB = 10


def validated_configuration(value):
    if type(value.get("enabled")) is not bool:
        raise ValueError("enabled must be boolean")
    count = value.get("batch_requests", 30)
    if type(count) is not int or not 1 <= count <= 30:
        raise ValueError("batch_requests must be 1..30")
    refresh_expired = value.get("refresh_expired", False)
    if type(refresh_expired) is not bool:
        raise ValueError("refresh_expired must be boolean")
    config = {
        "enabled": value["enabled"],
        "batch_requests": count,
        "scan_interval_seconds": 60,
        "failure_cooldown_minutes": 60,
        "lanes": list(LANES),
        "pacing_seconds": 3,
        "refresh_expired": refresh_expired,
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
    from santhau_studio.collectors.cgttrg import ENDPOINTS, PARSER_VERSION, PREFIX
    from santhau_studio.collectors.public_deep import ADAPTER_VERSION, PublicDeepFetcher
    from santhau_studio.storage.deep_retry import RETRY_COOLDOWN_MINUTES

    lanes = [
        lane
        for lane in LANES
        if PREFIX + ENDPOINTS[lane] not in PublicDeepFetcher.blocked_endpoints
    ]
    if not lanes:
        return []
    now = datetime.now(UTC)
    first_cooldown = config["failure_cooldown_minutes"]
    placeholders = " UNION ALL ".join("SELECT ? AS lane" for _ in lanes)
    query = """SELECT r.id,r.family,r.natural_key,lane.lane FROM source_records r
      JOIN source_record_versions v ON v.source_record_id=r.id AND v.content_version_seq=r.current_content_version
      CROSS JOIN (LANES_SQL) lane
      LEFT JOIN deep_tasks t ON t.source_record_id=r.id AND t.lane=lane.lane
      LEFT JOIN deep_captures c ON c.id=t.capture_id
      LEFT JOIN deep_task_metadata m ON m.source_record_id=t.source_record_id AND m.lane=t.lane AND m.task_updated_at_utc=t.updated_at_utc
      LEFT JOIN deep_capture_parse_metadata pm ON pm.deep_capture_id=c.id AND pm.capture_seq=c.latest_capture_seq
      WHERE r.source_key='egp_home'
      AND ((lane.lane='M1' AND r.family='KQLCNT' AND json_extract(v.normalized_json,'$.inputResultId') IS NOT NULL)
           OR (lane.lane<>'M1' AND r.family='TBMT' AND json_extract(v.normalized_json,'$.bidForm')='CGTTRG'))
      AND COALESCE(m.capability,'') NOT IN ('IDENTITY_QUARANTINE','UNVERIFIED_LISTING')
      AND NOT (COALESCE(m.capability,'') LIKE 'UNSUPPORTED%' AND COALESCE(m.adapter_version,0)=? AND COALESCE(m.parser_version,0)=?
               AND COALESCE(t.listing_object_sha,'')=r.current_record_object_sha)
      AND NOT (COALESCE(t.state,'')='PARSE_ERROR' AND t.response_object_sha IS NOT NULL AND
               COALESCE(pm.parser_version,1)>=? AND COALESCE(t.listing_object_sha,'')=r.current_record_object_sha)
      AND (t.source_record_id IS NULL
        OR (t.state IN ('FETCHED','SOURCE_EMPTY') AND
            (c.id IS NULL OR t.listing_object_sha IS NOT r.current_record_object_sha OR c.stale=1 OR COALESCE(pm.parser_version,1)<?
              OR (? AND c.refresh_due_at_utc IS NOT NULL AND COALESCE(julianday(c.refresh_due_at_utc),0)<=julianday(?))))
        OR (t.state NOT IN ('FETCHED','SOURCE_EMPTY') AND (
            -- The failure was against a different listing version: retry now.
            (t.listing_object_sha IS NOT NULL AND t.listing_object_sha <> r.current_record_object_sha)
            -- Quarantine release: a parser bump may fix a parse failure.
            OR (COALESCE(t.state,'')='PARSE_ERROR' AND COALESCE(pm.parser_version,1)<?)
            -- Quarantine release: an adapter/parser bump may lift an unsupported gate.
            OR (COALESCE(m.capability,'') LIKE 'UNSUPPORTED%' AND (COALESCE(m.adapter_version,0)<>? OR COALESCE(m.parser_version,0)<>?))
            -- Otherwise: escalating cooldown, then quarantine. Legacy rows have no
            -- deep_retry bookkeeping, so they start at the first cooldown step.
            OR (COALESCE(json_extract(t.provenance_json,'$.deep_retry.quarantined'),0)=0
                AND julianday(t.updated_at_utc) + (CASE COALESCE(CAST(json_extract(t.provenance_json,'$.deep_retry.consecutive_failures') AS INTEGER),0)
                    WHEN 0 THEN ? WHEN 1 THEN ? WHEN 2 THEN ? WHEN 3 THEN ? ELSE ? END) / 1440.0 <= julianday(?))
        )))
      ORDER BY r.id,CASE lane.lane WHEN 'HSMT' THEN 0 WHEN 'REOFFER' THEN 1 WHEN 'DETAIL' THEN 2 WHEN 'M1' THEN 3 ELSE 4 END LIMIT ?""".replace(
        "LANES_SQL", placeholders
    )
    return [
        dict(r)
        for r in conn.execute(
            query,
            (
                *lanes,
                ADAPTER_VERSION, PARSER_VERSION, PARSER_VERSION, PARSER_VERSION,
                int(config.get("refresh_expired", False)),
                now.isoformat().replace("+00:00", "Z"),
                PARSER_VERSION, ADAPTER_VERSION, PARSER_VERSION,
                first_cooldown, first_cooldown,
                RETRY_COOLDOWN_MINUTES[1], RETRY_COOLDOWN_MINUTES[2], RETRY_COOLDOWN_MINUTES[3],
                now.isoformat().replace("+00:00", "Z"),
                min(limit, config["batch_requests"]),
            ),
        )
    ]


def _lane_groups(selected):
    """Group selected (package, lane) targets into batched lane jobs.

    Returns [(lane, [targets...]), ...] in HSMT/REOFFER/DETAIL order, with at
    most MAX_RECORDS_PER_DEEP_JOB records per job. Grouping by lane keeps one
    fetcher/spec per job; chunking keeps each job inside the fetcher's
    30-request budget.
    """
    grouped = {}
    for target in selected:
        grouped.setdefault(target["lane"], []).append(target)
    jobs = []
    for lane in LANES:
        targets = grouped.get(lane, [])
        for start in range(0, len(targets), MAX_RECORDS_PER_DEEP_JOB):
            jobs.append((lane, targets[start:start + MAX_RECORDS_PER_DEEP_JOB]))
    return jobs


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
        for lane, chunk in _lane_groups(selected):
            if loop.flag.interrupt_requested or requests_used >= config["batch_requests"]:
                break
            record_ids = [target["id"] for target in chunk]
            family = "KQLCNT" if lane == "M1" else "TBMT"
            request = JobRequest(
                job_id="auto-deep-" + uuid.uuid4().hex[:16],
                job_kind=lane,
                requested_by="automatic-public-deep",
                sources=({"source_key": "egp_home", "family": family},),
                scope={"record_ids": record_ids, "refresh_expired": config.get("refresh_expired", False)},
                request_limits={
                    # Two requests of headroom per record (retries) inside the
                    # fetcher's 30-request budget; the shared batch budget
                    # still caps the whole scan.
                    "max_requests": min(2 * len(chunk), config["batch_requests"] - requests_used),
                    "max_attempts": 4,
                    "max_seconds": min(900, 120 + 30 * len(chunk)),
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
                    "family": family,
                    "lane": lane,
                    "record_ids": record_ids,
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
                # A dispatch-level failure is not evidence about any one
                # package: back it off as transient, never quarantine it.
                for target in chunk:
                    task_state(
                        loop._writer.conn, target["id"], lane, "FAILED",
                        error=detail["error"], failure_kind="TRANSIENT",
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
            completed += len(chunk)
            requests_used += max(0, detail.get("requests_used", len(chunk)))
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
