"""Durable bounded repairs/audits, executed on the sole warehouse writer thread.

No cross-run pagination cursor is trusted as proof. Each attempt walks the remaining
day/gaps with a fresh bounded budget, using Runner's verified adaptive splitting.
Budget reservations are committed before HTTP and remain charged after a crash.
"""

import hashlib
import json
import random
import time
import uuid
from datetime import UTC, datetime, timedelta

from santhau_studio.domain.coverage import Interval
from santhau_studio.storage import collection_scope as scope_store
from santhau_studio.storage import coverage
from santhau_studio.storage.coverage_contract import current


def _key(kind, source, span, cycle="initial"):
    value = [
        kind,
        source["source_key"],
        source["family"],
        scope_store.serialize(span),
        source["signature"],
        cycle,
    ]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:48]


def _table(conn):
    return bool(
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='maintenance_tasks'").fetchone()
    )


def _insert(conn, kind, source, span, now, *, approved, parent=None, cycle="initial"):
    key = _key(kind, source, span, cycle)
    stamp = coverage._iso(now)
    due = max(now, span.to_utc) if kind == "BACKFILL" else now
    cur = conn.execute(
        "INSERT OR IGNORE INTO maintenance_tasks(task_key,kind,source_key,family,interval_from_utc,interval_to_utc,context_json,contract_signature,parent_job_id,state,next_attempt_at,approved,created_at_utc,updated_at_utc) VALUES(?,?,?,?,?,?,?,?,?,'WAITING',?,?,?,?)",
        (
            key,
            kind,
            source["source_key"],
            source["family"],
            coverage._iso(span.from_utc),
            coverage._iso(span.to_utc),
            json.dumps(source, sort_keys=True),
            source["signature"],
            parent,
            coverage._iso(due),
            int(approved),
            stamp,
            stamp,
        ),
    )
    if approved:
        # Approval never resets exhausted, paused or intentionally cancelled work.
        conn.execute(
            "UPDATE maintenance_tasks SET approved=1 WHERE task_key=? AND state='WAITING'", (key,)
        )
    return cur.rowcount


def discover(conn, root, *, execute=False, parent=None, approve=True, now=None):
    del root  # scope belongs to this connection; never infer it from other directories
    now = now or datetime.now(UTC)
    scope = scope_store.load(conn)
    candidates, created = [], 0
    for day, source, span in scope_store.windows(scope):
        resolved = scope_store.resolve(conn, source, span)
        if resolved.can_skip:
            continue
        # One stable task per day; its next attempt resolves the precise remaining gaps.
        candidates.append(
            {
                "date": str(day),
                "family": source["family"],
                "task_key": _key("BACKFILL", source, span),
                "gaps": [scope_store.serialize(g) for g in resolved.gaps],
            }
        )
        if execute:
            created += _insert(conn, "BACKFILL", source, span, now, approved=approve, parent=parent)
    if execute:
        conn.commit()
    states = (
        [
            dict(r)
            for r in conn.execute(
                "SELECT state,COUNT(*) count FROM maintenance_tasks GROUP BY state"
            )
        ]
        if _table(conn)
        else []
    )
    return {
        "dry_run": not execute,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "created": created,
        "task_states": states,
        "network_requests": 0,
    }


def control(conn, parent, command, request=None):
    if not _table(conn):
        return
    state = {
        "STOP_GRACEFUL": "CANCELLED",
        "PAUSE": "PAUSED",
        "RESUME": "WAITING",
    }.get(command)
    if state is None:
        return
    old = "('PAUSED')" if command == "RESUME" else "('WAITING','RUNNING','PAUSED')"
    conn.execute(
        f"UPDATE maintenance_tasks SET state=?,last_reason=? WHERE state IN {old} AND (? IS NULL OR parent_job_id=? OR task_key=?)",
        (state, command, parent, parent, (parent or "").removeprefix("maintenance-").split(".")[0]),
    )
    if state in ("PAUSED", "CANCELLED"):
        conn.execute(
            "INSERT OR REPLACE INTO maintenance_holds SELECT COALESCE(parent_job_id,'maintenance-'||task_key),source_key,family,interval_from_utc,interval_to_utc,? FROM maintenance_tasks WHERE (? IS NULL OR parent_job_id=? OR task_key=?)",
            (state, parent, parent, (parent or "").removeprefix("maintenance-")),
        )
        if request is not None and request.job_kind in ("BACKFILL", "RECONCILE"):
            for source in request.sources:
                span = request.interval()
                conn.execute(
                    "INSERT OR REPLACE INTO maintenance_holds VALUES(?,?,?,?,?,?)",
                    (
                        request.job_id,
                        source["source_key"],
                        source["family"],
                        coverage._iso(span.from_utc),
                        coverage._iso(span.to_utc),
                        state,
                    ),
                )
    elif command == "RESUME":
        conn.execute(
            "DELETE FROM maintenance_holds WHERE state='PAUSED' AND (? IS NULL OR parent_job_id=?)",
            (parent, parent),
        )
    conn.commit()


def record_request_gaps(loop, request, detail):
    """Persist the assigned request's gaps even before an operator enables repairs."""
    if request.scope.get("maintenance_task") or request.job_kind != "BACKFILL" or not _soft(detail):
        return 0
    from santhau_studio.workers.runner import _vn_days

    conn = loop._writer.conn
    configured = scope_store.load(conn, required=False)
    created = 0
    for src in request.sources:
        source = current(
            conn,
            src["source_key"],
            src["family"],
            endpoint=src.get("endpoint"),
            adapter_sha=src.get("adapter_sha"),
        )
        if source is None:
            continue
        for span in _vn_days(request.interval()):
            if not scope_store.resolve(conn, source, span).can_skip:
                approved = bool(configured and configured["policy"]["repair_enabled"])
                created += _insert(
                    conn,
                    "BACKFILL",
                    source,
                    span,
                    loop._clock(),
                    approved=approved,
                    parent=request.job_id,
                )
                if not any("BUDGET_EXHAUSTED" in b for b in detail.get("blockers", [])):
                    conn.execute(
                        "UPDATE maintenance_tasks SET next_attempt_at=MAX(next_attempt_at,?) WHERE task_key=? AND state='WAITING'",
                        (
                            coverage._iso(loop._clock() + timedelta(seconds=_delay(1))),
                            _key("BACKFILL", source, span),
                        ),
                    )
    conn.commit()
    return created


def _delay(attempt):
    return min(3600, 60 * 2 ** min(attempt, 6)) * random.uniform(0.85, 1.15)


def recover(conn, *, now=None):
    now = now or datetime.now(UTC)
    if not _table(conn):
        return
    for row in conn.execute("SELECT * FROM maintenance_tasks WHERE state='RUNNING'").fetchall():
        command = conn.execute(
            "SELECT command FROM job_commands WHERE job_id IN (?,?) ORDER BY issued_at_utc DESC,seq DESC LIMIT 1",
            (row["parent_job_id"], "maintenance-" + row["task_key"]),
        ).fetchone()
        if command and command[0] in ("PAUSE", "STOP_GRACEFUL"):
            control(conn, row["parent_job_id"] or "maintenance-" + row["task_key"], command[0])
            continue
        conn.execute(
            "UPDATE maintenance_tasks SET state='WAITING',last_reason='CRASH_RESERVATION_CHARGED',next_attempt_at=? WHERE task_key=?",
            (coverage._iso(now + timedelta(seconds=_delay(row["attempts"]))), row["task_key"]),
        )
    conn.commit()


def _schedule_audits(conn, scope, now):
    if not scope["policy"]["reconcile_enabled"]:
        return
    windows = list(scope_store.windows(scope))
    if any(not scope_store.resolve(conn, source, span).can_skip for _, source, span in windows):
        return
    for _, source, span in windows:
        _insert(conn, "RECONCILE", source, span, now, approved=True)
    existing = conn.execute(
        "SELECT state,updated_at_utc FROM maintenance_tasks WHERE kind='RECONCILE'"
    ).fetchall()
    if existing and all(r["state"] == "COMPLETE" for r in existing):
        latest = max(coverage._parse(r["updated_at_utc"]) for r in existing)
        if now >= latest + timedelta(days=scope["policy"]["reconcile_every_days"]):
            end = max(span.to_utc for _, _, span in windows)
            for _, source, span in windows:
                if span.to_utc > end - timedelta(days=scope["policy"]["overlap_days"]):
                    _insert(
                        conn,
                        "RECONCILE",
                        source,
                        span,
                        now,
                        approved=True,
                        cycle=coverage._iso(latest),
                    )
    conn.commit()


def _eligible(conn, row, scope, bounds=None, contexts=None):
    source = json.loads(row["context_json"])
    selected = next(
        (
            s
            for s in scope["sources"]
            if s["source_key"] == row["source_key"]
            and s["family"] == row["family"]
            and s["signature"] == row["contract_signature"]
        ),
        None,
    )
    context_key = (row["source_key"], row["family"], source["endpoint"], source["adapter_sha"])
    if contexts is None:
        contexts = {}
    if context_key not in contexts:
        contexts[context_key] = current(conn, *context_key[:2], endpoint=context_key[2], adapter_sha=context_key[3])
    actual = contexts[context_key]
    span = Interval(
        coverage._parse(row["interval_from_utc"]), coverage._parse(row["interval_to_utc"])
    )
    if bounds is None:
        bounds = [s for _, _, s in scope_store.windows(scope)]
        bounds = (min(s.from_utc for s in bounds), max(s.to_utc for s in bounds))
    return bool(
        selected
        and actual
        and actual["signature"] == source["signature"]
        and bounds[0]
        <= span.from_utc
        < span.to_utc
        <= bounds[1]
    )


def _soft(detail):
    blockers = detail.get("blockers", [])
    if not blockers or detail.get("error"):
        return False
    allowed = (
        "BUDGET_EXHAUSTED",
        "DEADLINE",
        "TIMEOUT",
        "CONNECTERROR",
        "READERROR",
        "WRITEERROR",
        "REMOTEPROTOCOLERROR",
        "TLS_EOF_TRANSIENT",
        "UNEXPECTED_EOF_WHILE_READING",
        "EOF_OCCURRED_IN_VIOLATION",
        "RATE_LIMIT_429",
        "SERVER_ERROR",
        "HTTP_429",
        "HTTP_5",
    )
    meaningful = [b for b in blockers if b != "TOTAL_UNTRUSTED"]
    return bool(meaningful) and all(any(t in b.upper() for t in allowed) for b in meaningful)


def _previous_refusal(conn, source, span):
    """Discovery cannot turn an already-known hard failure into an automatic retry."""
    rows = conn.execute(
        "SELECT * FROM coverage_intervals WHERE source_key=? AND family=? AND status<>'COMPLETE' AND interval_from_utc<? AND interval_to_utc>? ORDER BY id DESC",
        (
            source["source_key"],
            source["family"],
            coverage._iso(span.to_utc),
            coverage._iso(span.from_utc),
        ),
    )
    for row in rows:
        overlap = Interval(
            max(span.from_utc, coverage._parse(row["interval_from_utc"])),
            min(span.to_utc, coverage._parse(row["interval_to_utc"])),
        )
        if scope_store.resolve(conn, source, overlap).can_skip:
            continue
        if row["status"] == "BLOCKED":
            return "PREVIOUS_ACCESS_BLOCK"
        saved = conn.execute(
            "SELECT value_json FROM settings WHERE key=?", (f"coverage:evidence:{row['id']}",)
        ).fetchone()
        if saved:
            detail = json.loads(saved[0])
            blockers = detail.get("blockers", [])
            open_snapshot = blockers and all(
                b == "INTERVAL_NOT_CLOSED" or b.startswith("TOTAL_CHANGED ") for b in blockers
            )
            if blockers and not open_snapshot and not _soft(detail):
                return "PREVIOUS_HARD_FAILURE:" + str(blockers)[:200]
        # Only the latest unresolved evidence controls the decision.
        break
    return None


def run_batch(loop, parent_run=None):
    """At most one due task; no sleep, duplicate queue jobs or recursive dispatch."""
    if loop.flag.interrupt_requested or loop._writer is None:
        return False
    conn, now = loop._writer.conn, loop._clock()
    scope = scope_store.load(conn, required=False)
    if scope is None:
        return False
    policy = scope["policy"]
    if policy["repair_enabled"]:
        discover(conn, loop.data_root, execute=True, now=now)
    _schedule_audits(conn, scope, now)
    today = coverage._iso(now.replace(hour=0, minute=0, second=0, microsecond=0))
    used = dict(conn.execute(
        "SELECT t.kind,COUNT(*) FROM maintenance_attempts a JOIN maintenance_tasks t USING(task_key) WHERE a.started_at_utc>=? GROUP BY t.kind", (today,)))
    allowed = [kind for kind, quota in (("BACKFILL", policy["quota_per_day"]), ("RECONCILE", policy["reconcile_quota_per_day"]))
               if used.get(kind, 0) < quota and (kind != "RECONCILE" or policy["reconcile_enabled"])]
    if not allowed:
        conn.commit()
        return False
    windows = [span for _, _, span in scope_store.windows(scope)]
    bounds = (min(s.from_utc for s in windows), max(s.to_utc for s in windows))
    contexts = {}
    rows = conn.execute(
        "SELECT * FROM maintenance_tasks WHERE approved=1 AND state='WAITING' AND next_attempt_at<=? AND kind IN (SELECT value FROM json_each(?)) ORDER BY kind='RECONCILE',attempts,updated_at_utc,task_key",
        (coverage._iso(now), json.dumps(allowed)),
    )
    queued = scope_store.queue_windows(loop.data_root)
    for row in rows:
        if loop.flag.interrupt_requested:
            break
        key = row["task_key"]
        if not _eligible(conn, row, scope, bounds, contexts):
            conn.execute(
                "UPDATE maintenance_tasks SET state='MANUAL_REVIEW',last_reason='SCOPE_OR_CONTEXT_CHANGED' WHERE task_key=?",
                (key,),
            )
            continue
        source = json.loads(row["context_json"])
        span = Interval(
            coverage._parse(row["interval_from_utc"]), coverage._parse(row["interval_to_utc"])
        )
        if conn.execute(
            "SELECT 1 FROM maintenance_holds WHERE source_key=? AND family=? AND interval_from_utc<? AND interval_to_utc>? LIMIT 1",
            (row["source_key"], row["family"], row["interval_to_utc"], row["interval_from_utc"]),
        ).fetchone():
            continue
        if any(
            job.job_id != getattr(loop._current, "job_id", None)
            and job.interval().from_utc < span.to_utc
            and job.interval().to_utc > span.from_utc
            and any(
                s.get("family") == row["family"] and s.get("source_key") == row["source_key"]
                for s in job.sources
            )
            for _, job in queued
        ):
            continue
        if row["kind"] == "BACKFILL" and scope_store.resolve(conn, source, span).can_skip:
            conn.execute(
                "UPDATE maintenance_tasks SET state='COMPLETE',last_reason='PROOF_UNION' WHERE task_key=?",
                (key,),
            )
            continue
        if row["kind"] == "BACKFILL" and row["attempts"] == 0:
            refusal = _previous_refusal(conn, source, span)
            if refusal:
                conn.execute(
                    "UPDATE maintenance_tasks SET state='MANUAL_REVIEW',last_reason=? WHERE task_key=?",
                    (refusal, key),
                )
                continue
        requests = min(
            policy["day_max_requests"], policy["max_total_requests"] - row["requests_used"]
        )
        seconds = min(
            policy["day_max_seconds"], int(policy["max_total_seconds"] - row["seconds_used"])
        )
        if row["attempts"] >= policy["max_attempts"] or min(requests, seconds) < 1:
            conn.execute(
                "UPDATE maintenance_tasks SET state='EXHAUSTED',last_reason='CUMULATIVE_LIMIT' WHERE task_key=?",
                (key,),
            )
            continue
        _execute(loop, row, source, span, policy, requests, seconds, now, parent_run)
        return True
    conn.commit()
    return False


def _execute(loop, row, source, span, policy, requests, seconds, now, parent_run):
    from santhau_studio.workers.jobs import JobRequest

    conn, key, attempt = loop._writer.conn, row["task_key"], row["attempts"] + 1
    job = JobRequest(
        "maintenance-" + key,
        row["kind"],
        "scoped-maintenance",
        (source,),
        {**scope_store.serialize(span), "maintenance_task": key},
        request_limits={
            "max_pages": policy["day_max_pages"],
            "max_requests": requests,
            "max_seconds": seconds,
            "pacing_seconds": 3,
            "max_attempts": 4,
        },
        created_at=coverage._iso(now),
    )
    run_id = "run-" + uuid.uuid4().hex
    loop._writer.register_run(run_id, job.job_id, job.job_kind, requested_by=job.requested_by)
    conn.execute(
        "UPDATE maintenance_tasks SET state='RUNNING',attempts=?,requests_used=requests_used+?,seconds_used=seconds_used+?,updated_at_utc=? WHERE task_key=?",
        (attempt, requests, seconds, coverage._iso(now), key),
    )
    conn.execute(
        "INSERT INTO maintenance_attempts(task_key,attempt_no,run_id,started_at_utc,reserved_requests,reserved_seconds) VALUES(?,?,?,?,?,?)",
        (key, attempt, run_id, coverage._iso(now), requests, seconds),
    )
    conn.commit()
    previous = loop._current
    previous_phase, previous_progress = loop._phase, loop.progress
    if previous is None:
        loop._current = job
    loop._set_phase("COLLECTING")
    loop.progress = {}
    started = time.monotonic()
    try:
        status, detail = loop._dispatch(job, run_id)
    except Exception as exc:
        status, detail = "FAILED", {"error": type(exc).__name__}
    finally:
        loop._current = previous
        loop._set_phase(previous_phase)
        loop.progress = previous_progress
    elapsed = min(seconds, time.monotonic() - started)
    used = (
        requests
        if detail.get("error")
        else min(requests, max(detail.get("requests", 0), detail.get("attempts", 0)))
    )
    complete = (
        scope_store.resolve(conn, source, span).can_skip
        if job.job_kind == "BACKFILL"
        else detail.get("audit_status") == "CHECKED"
    )
    state = "COMPLETE" if complete else "WAITING" if _soft(detail) else "MANUAL_REVIEW"
    if loop.flag.interrupt_requested:
        state = (
            "WAITING"
            if loop.flag.shutdown_requested and loop._resume_on_shutdown
            else "CANCELLED"
            if loop.flag.stop_requested
            else "PAUSED"
        )
        status = "INTERRUPTED"
    elif state == "WAITING" and (
        attempt >= policy["max_attempts"]
        or row["requests_used"] + used >= policy["max_total_requests"]
        or row["seconds_used"] + elapsed >= policy["max_total_seconds"]
    ):
        state = "EXHAUSTED"
    finished = loop._clock()
    reason = json.dumps({**detail, "parent_run": parent_run}, sort_keys=True)
    conn.execute(
        "UPDATE maintenance_tasks SET state=?,requests_used=requests_used-?+?,seconds_used=seconds_used-?+?,next_attempt_at=?,last_reason=?,updated_at_utc=? WHERE task_key=?",
        (
            state,
            requests,
            used,
            seconds,
            elapsed,
            coverage._iso(finished + timedelta(seconds=_delay(attempt))),
            reason,
            coverage._iso(finished),
            key,
        ),
    )
    conn.execute(
        "UPDATE maintenance_attempts SET finished_at_utc=?,requests_used=?,seconds_used=?,result_json=? WHERE task_key=? AND attempt_no=?",
        (coverage._iso(finished), used, elapsed, reason, key, attempt),
    )
    if state == "PAUSED":
        conn.execute("UPDATE runs SET lifecycle='PAUSED' WHERE run_id=?", (run_id,))
        loop._paused_jobs.add(job.job_id)
        loop._paused_job = job.job_id
    else:
        loop._writer.finish_run(run_id, status)
    if state in ("PAUSED", "CANCELLED"):
        control(conn, job.job_id, "PAUSE" if state == "PAUSED" else "STOP_GRACEFUL", request=job)
    conn.execute(
        "INSERT INTO run_tasks(run_id,task_kind,status,detail_json) VALUES(?,?,?,?)",
        (run_id, job.job_kind, status, reason),
    )
    conn.commit()
