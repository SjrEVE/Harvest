"""Coverage persistence and the single resolver entry point (§4.1, §4.5).

`resolve` is the only function any consumer may use to decide "may I skip this range?". The
contract tests in G0-B proved the *pure* resolver in domain/coverage.py; this module is the
loader that feeds it real rows, plus the one-transaction writer that keeps a proof and its
interval inseparable (§4.1 rule 8).

STALE_NEEDS_RECONCILE is derived from unacknowledged drift gaps rather than from a timestamp
comparison over every record version. Two reasons: the mechanism that *discovers* post-proof
revision is RECONCILE (§4.4), and per-resolution "did any row change under this interval" would
be an unindexed scan over `source_record_versions` on the UI's hot path.
"""

from __future__ import annotations

import contextlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from santhau_studio.domain.coverage import (
    CoverageResolution,
    CoverageRow,
    Interval,
    resolve_coverage,
)

# The kinds that mean "the data under this proof moved", i.e. §4.5's STALE_NEEDS_RECONCILE.
# MISSING_FROM_SOURCE_IN_VERIFIED_WINDOW is deliberately absent: an absence claim does not make
# a proven interval stale, it makes it a finding.
DRIFT_KINDS = ("HASH_DRIFT", "COUNT_DRIFT", "GRANULARITY_DRIFT")
_GRANULARITY_RANK = {"DAY": 0, "HOUR": 1, "MINUTE": 2, "SECOND": 3, "MILLISECOND": 4}


@dataclass(frozen=True)
class ProofRecord:
    """The evidence a COMPLETE claim rests on. Every field lands in coverage_proofs."""

    proof_version: int
    source_key: str
    family: str
    interval: Interval
    granularity: str
    boundary_rule_id: int
    source_query_fingerprint: str
    adapter_sha: str
    request_count: int
    attempt_count: int
    attempt_error_count: int
    unrecovered_request_count: int
    malformed_count: int
    distinct_ids: int
    ids_union_hash: str
    total_first: int | None
    total_last: int | None
    total_trusted: int
    terminal_evidence: dict[str, Any]
    page_size: int
    max_pages_hit: int
    max_records_hit: int
    verified_at_utc: str

    def complete_blockers(self) -> tuple[str, ...]:
        """Why this evidence cannot support a COMPLETE claim (§4.1, enumerated, not guessed)."""
        blockers: list[str] = []
        if self.unrecovered_request_count:
            blockers.append(f"UNRECOVERED_REQUESTS={self.unrecovered_request_count}")
        if self.malformed_count:
            blockers.append(f"MALFORMED_ROWS={self.malformed_count}")
        if self.max_pages_hit:
            blockers.append(f"MAX_PAGES_HIT={self.max_pages_hit}")
        if self.max_records_hit:
            blockers.append(f"MAX_RECORDS_HIT={self.max_records_hit}")
        if not self.total_trusted:
            blockers.append("TOTAL_UNTRUSTED")
        if self.total_first is None or self.total_last is None:
            blockers.append("NO_TOTAL_OBSERVED")
        elif self.total_first != self.total_last:
            blockers.append(f"TOTAL_CHANGED {self.total_first}->{self.total_last}")
        elif self.total_first < 10_000 and self.distinct_ids != self.total_first:
            blockers.append(f"DISTINCT_VS_TOTAL {self.distinct_ids}!={self.total_first}")
        if not self.terminal_evidence:
            blockers.append("NO_TERMINAL_EVIDENCE")
        return tuple(blockers)


def load_rows(
    conn: sqlite3.Connection,
    source_key: str,
    family: str,
    window: Interval,
    context=None,
) -> tuple[CoverageRow, ...]:
    """Interval rows overlapping `window`, with proof version and drift flags attached."""
    from santhau_studio.collectors.source_registry import query_contract_matches
    from santhau_studio.storage.coverage_contract import compatible, current
    if context is None:
        context = current(conn, source_key, family)

    sql = (
        " SELECT ci.id, ci.interval_from_utc, ci.interval_to_utc, ci.granularity, ci.status,"
        "        ci.proof_id, cp.proof_version, cp.boundary_rule_id,br.conversion_params,"
        "        cp.source_key proof_source,cp.family proof_family,cp.interval_from_utc proof_from,cp.interval_to_utc proof_to,"
        "        EXISTS (SELECT 1 FROM reconcile_gaps g"
        "                WHERE g.source_key = ci.source_key AND g.family = ci.family"
        "                  AND g.kind IN ({})"
        "                  AND g.acknowledged_at_utc IS NULL"
        "                  AND g.interval_from_utc < ci.interval_to_utc"
        "                  AND g.interval_to_utc > ci.interval_from_utc) AS drifted"
        "   FROM coverage_intervals ci"
        "   LEFT JOIN coverage_proofs cp ON cp.id = ci.proof_id"
        "   LEFT JOIN filter_boundary_rules br ON br.id = cp.boundary_rule_id"
        "  WHERE ci.source_key = ? AND ci.family = ?"
        "    AND ci.interval_from_utc < ? AND ci.interval_to_utc > ?"
        "  ORDER BY ci.interval_from_utc".format(",".join("?" for _ in DRIFT_KINDS))
    )
    rows = conn.execute(sql, (*DRIFT_KINDS, source_key, family, _iso(window.to_utc), _iso(window.from_utc)))
    compatibility={}
    def matches(row):
        if row['proof_id'] is None:
            return False
        key=row['boundary_rule_id']
        if key not in compatibility:
            compatibility[key]=compatible(conn,key,context)
        return (row['proof_version']==1 and row['proof_source']==source_key and row['proof_family']==family
            and _parse(row['proof_from']) <= _parse(row['interval_from_utc']) and _parse(row['proof_to']) >= _parse(row['interval_to_utc']) and compatibility[key])
    return tuple(
        CoverageRow(
            id=int(row["id"]),
            source_key=source_key,
            family=family,
            interval=Interval(_parse(row["interval_from_utc"]), _parse(row["interval_to_utc"])),
            status=row["status"],
            granularity=row["granularity"],
            proof_id=row["proof_id"],
            proof_version=row["proof_version"],
            data_revised_after_proof=bool(row["drifted"]) or not query_contract_matches(family, row["conversion_params"]) or (row['proof_version'] is not None and not matches(row)),
            compatible_context=matches(row),
        )
        for row in rows
    )


def resolve(
    conn: sqlite3.Connection,
    source_key: str,
    family: str,
    from_utc: datetime,
    to_utc: datetime,
    *, context=None,
) -> CoverageResolution:
    """The single resolver every consumer calls (§4.5). No caller joins coverage_intervals."""
    window = Interval(_as_utc(from_utc), _as_utc(to_utc))
    if context is None:
        from santhau_studio.storage.coverage_contract import current
        context=current(conn,source_key,family)
    return resolve_coverage(
        source_key=source_key,
        family=family,
        requested=window,
        rows=load_rows(conn, source_key, family, window, context=context),
    )


def boundary_rule(conn: sqlite3.Connection, *, source_key: str, family: str, endpoint: str, adapter_sha: str) -> dict | None:
    """The newest canary-verified boundary contract for this exact endpoint+adapter (§4.1 item 1)."""
    from santhau_studio.collectors.source_registry import query_contract_matches

    rows = conn.execute(
        "SELECT id, rule_key, rule_version, boundary_kind, precision, source_timezone, offset_minutes,"
        " canary_verified, verified_at_utc, conversion_params"
        " FROM filter_boundary_rules"
        " WHERE source_key=? AND family=? AND endpoint=? AND adapter_sha=? AND canary_verified=1"
        " ORDER BY rule_version DESC, verified_at_utc DESC",
        (source_key, family, endpoint, adapter_sha),
    )
    return next((dict(row) for row in rows if query_contract_matches(family, row["conversion_params"])), None)


def record_interval(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    status: str,
    proof: ProofRecord | None,
    source_key: str,
    family: str,
    interval: Interval,
    granularity: str,
) -> int:
    """Write proof then interval in one transaction, so a COMPLETE never stands without evidence.

    The forward reference in the DDL (coverage_intervals.proof_id points at a table declared
    later) means the proof row must be inserted first — the ordering the spec's §4.1 item 8
    implies and that a naive writer gets backwards.
    """
    if status == "COMPLETE" and proof is None:
        raise ValueError("a COMPLETE interval requires a proof row in the same transaction")
    if proof is not None and proof.interval != interval:
        raise ValueError("proof interval and coverage interval must be the same span")
    created = _now_utc()
    conn.execute("BEGIN IMMEDIATE")
    try:
        proof_id = None
        if proof is not None:
            cur = conn.execute(
                "INSERT INTO coverage_proofs (proof_version, source_key, family, interval_from_utc, interval_to_utc,"
                " granularity, boundary_rule_id, source_query_fingerprint, adapter_sha, request_count, attempt_count,"
                " attempt_error_count, unrecovered_request_count, malformed_count, distinct_ids, ids_union_hash,"
                " total_first, total_last, total_trusted, terminal_evidence_json, page_size, max_pages_hit,"
                " max_records_hit, verified_at_utc, verified_by_run_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    proof.proof_version, proof.source_key, proof.family, _iso(proof.interval.from_utc),
                    _iso(proof.interval.to_utc), proof.granularity, proof.boundary_rule_id,
                    proof.source_query_fingerprint, proof.adapter_sha, proof.request_count, proof.attempt_count,
                    proof.attempt_error_count, proof.unrecovered_request_count, proof.malformed_count,
                    proof.distinct_ids, proof.ids_union_hash, proof.total_first, proof.total_last,
                    proof.total_trusted, _json(proof.terminal_evidence), proof.page_size, proof.max_pages_hit,
                    proof.max_records_hit, proof.verified_at_utc, run_id,
                ),
            )
            proof_id = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO coverage_intervals (source_key, family, interval_from_utc, interval_to_utc, granularity,"
            " status, proof_id, run_id, created_at_utc) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                source_key, family, _iso(interval.from_utc), _iso(interval.to_utc), granularity,
                status, proof_id, run_id, created,
            ),
        )
        conn.execute("COMMIT")
    except sqlite3.Error:
        _rollback(conn)
        raise
    return int(cur.lastrowid)


def advance_watermark(
    conn: sqlite3.Connection,
    *,
    source_key: str,
    family: str,
    to_utc: datetime,
    complete_interval_id: int,
) -> bool:
    """§4.4: the watermark moves over a verified COMPLETE interval *contiguously*.

    Returns False when the move is not allowed. Refusing quietly rather than raising is right
    here because "this proven day is not adjacent to the horizon" is a normal condition during a
    backfill; silently *jumping* the horizon over an unproven gap is not, so that is what is
    blocked.
    """
    row = conn.execute(
        " SELECT ci.status, ci.interval_from_utc, ci.interval_to_utc, cp.proof_version"
        "   FROM coverage_intervals ci LEFT JOIN coverage_proofs cp ON cp.id = ci.proof_id"
        "  WHERE ci.id = ?",
        (complete_interval_id,),
    ).fetchone()
    if row is None or row["status"] != "COMPLETE" or row["proof_version"] is None:
        raise ValueError("watermark may only advance over a verified COMPLETE interval")
    existing = conn.execute(
        "SELECT watermark_to_utc FROM watermarks WHERE source_key=? AND family=?", (source_key, family)
    ).fetchone()
    target = _iso(to_utc)
    if existing is None:
        # Establishing the horizon is itself a contiguity claim: "everything before this
        # instant is proven". A non-COMPLETE interval row straddling or preceding the
        # candidate (and not superseded by a newer COMPLETE union) vetoes the move.
        stale = conn.execute(
            "SELECT interval_from_utc, interval_to_utc FROM coverage_intervals"
            " WHERE source_key=? AND family=? AND status<>'COMPLETE' AND interval_from_utc < ?",
            (source_key, family, target),
        ).fetchall()
        for stale_row in stale:
            span_to = min(_parse(stale_row["interval_to_utc"]), to_utc)
            if not resolve(conn, source_key, family, _parse(stale_row["interval_from_utc"]), span_to).can_skip:
                return False
        conn.execute(
            "INSERT INTO watermarks (source_key, family, watermark_from_utc, watermark_to_utc,"
            " last_complete_interval_id, updated_at_utc) VALUES (?,?,?,?,?,?)",
            (source_key, family, None, target, complete_interval_id, _now_utc()),
        )
        return True
    current = existing["watermark_to_utc"]
    if target <= current:
        return False
    if _iso(_parse(row["interval_from_utc"])) != current:
        return False  # not adjacent to the horizon: the days in between are unproven
    conn.execute(
        "UPDATE watermarks SET watermark_to_utc=?, last_complete_interval_id=?, updated_at_utc=?"
        " WHERE source_key=? AND family=?",
        (target, complete_interval_id, _now_utc(), source_key, family),
    )
    return True


def watermark_for(conn: sqlite3.Connection, source_key: str, family: str) -> datetime | None:
    row = conn.execute(
        "SELECT watermark_to_utc FROM watermarks WHERE source_key=? AND family=?", (source_key, family)
    ).fetchone()
    if not row:
        return None
    watermark = _parse(row["watermark_to_utc"])
    if family == "KQLCNT":
        start = conn.execute("SELECT MIN(interval_from_utc) FROM coverage_intervals WHERE source_key=? AND family=?",
                             (source_key, family)).fetchone()[0]
        if start is None or _parse(start) >= watermark:
            return None
        if resolve(conn, source_key, family, _parse(start), watermark).status != "COMPLETE":
            return None
    return watermark


def granularity_rank(granularity: str) -> int:
    return _GRANULARITY_RANK[granularity]


def finer_granularity(granularity: str) -> str | None:
    """The next split level for adaptive windowing (§4.3), or None when already at the bottom."""
    order = ("DAY", "HOUR", "MINUTE", "SECOND", "MILLISECOND")
    index = order.index(granularity)
    return order[index + 1] if index + 1 < len(order) else None


def _rollback(conn: sqlite3.Connection) -> None:
    with contextlib.suppress(sqlite3.Error):
        conn.execute("ROLLBACK")


def _json(value: object) -> str:
    import json

    return json.dumps(value, sort_keys=True, default=str)


def _iso(value: datetime) -> str:
    """Storage timestamps are UTC with a trailing Z; the DDL rejects anything else.

    Mixing the two ISO forms would also break range comparisons, which happen as text here.
    """
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("coverage windows must be timezone-aware; naive datetimes are not UTC by assumption")
    return value


def _now_utc() -> str:
    return _iso(datetime.now(UTC))
