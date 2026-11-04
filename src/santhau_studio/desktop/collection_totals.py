"""Snapshot-local source totals: never extrapolate or add overlapping requests."""
import json
from datetime import datetime, timedelta

from santhau_studio.collectors.source_registry import query_contract_for
from santhau_studio.desktop.collection_dates import VN
from santhau_studio.domain.coverage import Interval, resolve_coverage


def load_counts(conn, first, last):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(record_fields)")}
    if "collection_date" not in columns:
        return None
    return {(r["family"], r["collection_date"]): r["n"] for r in conn.execute(
        "SELECT family,collection_date,COUNT(*) n FROM record_fields WHERE source_key='egp_home'"
        " AND collection_date>=? AND collection_date<? GROUP BY family,collection_date",
        (first.date().isoformat(), last.date().isoformat()))}


def load_totals(conn, family, rows):
    accepted = {row.proof_id for row in rows if row.is_verified_complete and not row.needs_reconcile}
    candidates = []
    for row in conn.execute("SELECT * FROM coverage_proofs WHERE source_key='egp_home' AND family=?", (family,)):
        if row["id"] not in accepted:
            continue
        candidates.append({"from": row["interval_from_utc"], "to": row["interval_to_utc"],
            "total_first": row["total_first"], "total_last": row["total_last"],
            "total_trusted": row["total_trusted"], "measured_at": row["verified_at_utc"]})
    # New runs also retain hints for incomplete windows. Legacy hints without the
    # family date-query contract remain unknown; reading this never calls E-GP.
    for row in conn.execute("SELECT value_json FROM settings WHERE key GLOB 'coverage:evidence:*'"):
        try:
            value = json.loads(row[0])
        except (ValueError, TypeError):
            continue
        if value.get("family") == family and value.get("source_key") == "egp_home" and value.get("query_contract") == query_contract_for(family):
            candidates.append(value)
    trusted = {}
    for value in candidates:
        total = value.get("total_first")
        if not value.get("total_trusted") or type(total) is not int or not 0 <= total < 10000 or total != value.get("total_last"):
            continue
        start, end = (datetime.fromisoformat(value[k]) for k in ("from", "to"))
        stamp = value.get("measured_at") or ""
        key = (start, end)
        if key not in trusted or trusted[key][1] < stamp:
            trusted[key] = (total, stamp)
    return [(a, b, n, stamp) for (a, b), (n, stamp) in trusted.items()]


def exact_total(candidates, window):
    """Find an exact tiling. A partial source window may not be prorated by time."""
    by_start = {}
    for start, end, total, stamp in candidates:
        if window.from_utc <= start < end <= window.to_utc:
            by_start.setdefault(start, []).append((end, total, stamp))
    paths = {window.to_utc: (0, None, None)}
    for start in sorted(by_start, reverse=True):
        for end, total, stamp in sorted(by_start[start], reverse=True):
            if end in paths:
                tail, oldest, newest = paths[end]
                paths[start] = (total + tail, min(stamp, oldest or stamp), max(stamp, newest or stamp))
                break
    return paths.get(window.from_utc, (None, None, None))


def summarize(family, window, rows, counts, totals):
    rows = tuple(row for row in rows if row.interval.intersects(window))
    source_total, measured_from, measured_to = exact_total(totals, window)
    day = window.from_utc.astimezone(VN)
    stored = 0 if counts is not None else None
    proven_days = total_days = 0
    while day < window.to_utc:
        end = min(day + timedelta(days=1), window.to_utc)
        total_days += 1
        if counts is not None:
            stored += counts.get((family, day.date().isoformat()), 0)
        if rows:
            resolved = resolve_coverage(source_key="egp_home", family=family, requested=Interval(day, end), rows=rows)
            proven_days += int(resolved.can_skip)
        day = end
    comparable = source_total is not None and stored is not None and stored <= source_total
    return {"stored": stored, "source_total": source_total,
        "missing": source_total - stored if comparable else None,
        "percent": round(100 * stored / source_total, 1) if comparable and source_total else (100 if comparable else None),
        "proven_days": proven_days, "total_days": total_days,
        "source_measured_from": measured_from, "source_measured_to": measured_to,
        "count_drift": source_total is not None and stored is not None and stored > source_total}
