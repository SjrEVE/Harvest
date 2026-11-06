"""Explicit per-warehouse collection scope and read-only effective coverage report."""

import json
from collections import Counter
from datetime import UTC, date, datetime, timedelta

from santhau_studio.collectors.source_registry import FAMILY_ORDER
from santhau_studio.domain.coverage import Interval, vn_calendar_day
from santhau_studio.storage import coverage
from santhau_studio.storage.coverage_contract import current

KEY = "collection_scope_v1"
DEFAULT_POLICY = {
    "repair_enabled": False,
    "reconcile_enabled": False,
    "max_attempts": 4,
    "max_total_requests": 6000,
    "max_total_seconds": 14400,
    "day_max_pages": 1200,
    "day_max_requests": 1500,
    "day_max_seconds": 3600,
    "quota_per_day": 24,
    "reconcile_quota_per_day": 2,
    "reconcile_every_days": 7,
    "overlap_days": 3,
}


def load(conn, *, required=True):
    row = conn.execute("SELECT value_json FROM settings WHERE key=?", (KEY,)).fetchone()
    if row is None:
        if required:
            raise ValueError("SCOPE_NOT_CONFIGURED")
        return None
    return json.loads(row[0])


def configure(conn, *, first, last, families=FAMILY_ORDER, policy=None):
    a, b = date.fromisoformat(first), date.fromisoformat(last)
    if a > b or (b - a).days > 3660 or vn_calendar_day(a).from_utc > datetime.now(UTC):
        raise ValueError("INVALID_SCOPE_DATES")
    families = tuple(dict.fromkeys(families))
    if not families or any(f not in FAMILY_ORDER for f in families):
        raise ValueError("INVALID_SCOPE_FAMILIES")
    settings = {**DEFAULT_POLICY, **(policy or {})}
    if set(settings) != set(DEFAULT_POLICY):
        raise ValueError("UNKNOWN_MAINTENANCE_POLICY")
    for key, maximum in (
        ("max_attempts", 10),
        ("max_total_requests", 30000),
        ("max_total_seconds", 86400),
        ("day_max_pages", 10000),
        ("day_max_requests", 30000),
        ("day_max_seconds", 86400),
        ("quota_per_day", 1000),
        ("reconcile_quota_per_day", 100),
        ("reconcile_every_days", 365),
        ("overlap_days", 3660),
    ):
        if type(settings[key]) is not int or not 1 <= settings[key] <= maximum:
            raise ValueError("INVALID_POLICY:" + key)
    for key in ("repair_enabled", "reconcile_enabled"):
        if type(settings[key]) is not bool:
            raise ValueError("INVALID_POLICY:" + key)
    contexts = []
    for family in families:
        context = current(conn, "egp_home", family)
        if context is None:
            raise ValueError("VERIFIED_CONTEXT_MISSING:" + family)
        contexts.append(context)
    value = {
        "version": 1,
        "date_from": str(a),
        "date_to": str(b),
        "sources": contexts,
        "policy": settings,
    }
    conn.execute(
        "INSERT OR REPLACE INTO settings VALUES(?,?,?)",
        (KEY, json.dumps(value, sort_keys=True), coverage._now_utc()),
    )
    return value


def windows(scope):
    day, last = date.fromisoformat(scope["date_from"]), date.fromisoformat(scope["date_to"])
    while day <= last:
        for source in scope["sources"]:
            yield day, source, vn_calendar_day(day)
        day += timedelta(days=1)


def resolve(conn, source, span):
    # None must not silently fall back to a different, newer query contract.
    actual = current(
        conn,
        source["source_key"],
        source["family"],
        endpoint=source["endpoint"],
        adapter_sha=source["adapter_sha"],
    )
    context = (
        source
        if actual and actual["signature"] == source["signature"]
        else {"signature": "INVALID"}
    )
    return coverage.resolve(
        conn, source["source_key"], source["family"], span.from_utc, span.to_utc, context=context
    )


def queue_windows(root):
    from santhau_studio.workers.jobs import JobRequest

    result = []
    for state in ("active", "queued"):
        for path in (root / "jobs" / state).glob("*.json"):
            try:
                job = JobRequest.from_json(path.read_text(encoding="utf-8"))
                if job.job_kind in ("BACKFILL", "RECONCILE"):
                    result.append((state, job))
            except (ValueError, KeyError, OSError):
                continue
    return result


def report(conn, root, scope=None):
    scope = scope or load(conn)
    queued = queue_windows(root)
    result, counts = [], Counter()
    for day, source, span in windows(scope):
        resolved = resolve(conn, source, span)
        state = (
            "proved"
            if resolved.can_skip
            else "never_attempted"
            if resolved.status == "NOT_ACCESSED"
            else "partial_uncovered"
        )
        flags = sorted(
            {
                state
                for state, job in queued
                if any(
                    s.get("source_key") == source["source_key"]
                    and s.get("family") == source["family"]
                    for s in job.sources
                )
                and job.interval().from_utc < span.to_utc
                and job.interval().to_utc > span.from_utc
            }
        )
        counts[state] += 1
        for flag in flags:
            counts[flag] += 1
        result.append(
            {
                "date": str(day),
                "source_key": source["source_key"],
                "family": source["family"],
                "state": state,
                "queue": flags,
                "proven_fraction": resolved.proven_fraction,
                "reasons": resolved.reasons,
                "gaps": [serialize(gap) for gap in resolved.gaps],
            }
        )
    historical = []
    start = vn_calendar_day(date.fromisoformat(scope["date_from"])).from_utc
    end = vn_calendar_day(date.fromisoformat(scope["date_to"])).to_utc
    for source in scope["sources"]:
        for row in conn.execute(
            "SELECT * FROM coverage_intervals WHERE source_key=? AND family=? AND status<>'COMPLETE' AND interval_from_utc<? AND interval_to_utc>?",
            (source["source_key"], source["family"], coverage._iso(end), coverage._iso(start)),
        ):
            span = Interval(
                max(start, coverage._parse(row["interval_from_utc"])),
                min(end, coverage._parse(row["interval_to_utc"])),
            )
            historical.append(
                {
                    "interval_id": row["id"],
                    "stored_status": row["status"],
                    "superseded_by_proof_union": resolve(conn, source, span).can_skip,
                }
            )
    checks = conn.execute("SELECT COUNT(*) FROM runs WHERE job_kind='RECONCILE'").fetchone()[0]
    return {
        "scope": scope,
        "windows": result,
        "summary": {
            **dict.fromkeys(
                ("proved", "partial_uncovered", "never_attempted", "active", "queued"), 0
            ),
            **counts,
            "day_family_windows": len(result),
        },
        "historical_intervals": historical,
        "reconcile": {
            "runs": checks,
            "status": "NOT_CHECKED" if checks == 0 else "SEE_SCOPED_AUDIT_RESULTS",
        },
        "network_requests": 0,
        "coverage_is_queue_independent": True,
    }


def serialize(span):
    return {"from": coverage._iso(span.from_utc), "to": coverage._iso(span.to_utc)}
