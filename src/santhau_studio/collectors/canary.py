"""Canary ladder: PROBE_UNVERIFIED → RULE_VERIFIED → COLLECTION_PROVEN (§4.1, §6).

The ladder exists so a reachability probe can never be mistaken for proof of collection:

* **PROBE_UNVERIFIED** — no successful probe is recorded for this family/adapter.
* **RULE_VERIFIED** — a probe succeeded (shape + reachability, no date filter sent) *and* a
  boundary rule for the adapter is marked verified. Nothing has been collected yet.
* **COLLECTION_PROVEN** — at least one coverage interval is COMPLETE and carries a proof whose
  boundary rule is verified. Only the runner can create that, and only through `_record`.

A probe deliberately runs `EgpHomeLane` one page at a time and never touches `Runner`, so the
frozen DDL's "COMPLETE needs a proof" rule cannot be satisfied by a probe: there is no code path
from here to `coverage_intervals`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from santhau_studio.collectors.egp_home import EgpHomeLane, Page, SearchWindow
from santhau_studio.collectors.http_client import AccessBlocked, FetchFailed, FetchLane
from santhau_studio.collectors.source_registry import SOURCES, source_for
from santhau_studio.storage.warehouse import WarehouseWriter

PROBE_UNVERIFIED = "PROBE_UNVERIFIED"
RULE_VERIFIED = "RULE_VERIFIED"
COLLECTION_PROVEN = "COLLECTION_PROVEN"
LADDER = (PROBE_UNVERIFIED, RULE_VERIFIED, COLLECTION_PROVEN)

ACCESS_PUBLIC = "PUBLIC"
ACCESS_SESSION = "SESSION"
ACCESS_DISABLED = "DISABLED"


def access_policy(source_key: str) -> str:
    """Public e-GP home versus session-gated v2 versus quarantined hosts, in one place."""
    source = SOURCES.get(source_key)
    if source is None:
        return ACCESS_DISABLED
    if source.requires_user_session:
        return ACCESS_SESSION  # blocked until B7 proves header auth, not quarantined
    if not source.enabled_by_default:
        return ACCESS_DISABLED
    return ACCESS_PUBLIC


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _ensure_registry_row(conn, source_key: str, display_name: str, access_state: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO source_registry (source_key, display_name, endpoint_family, access_state)"
        " VALUES (?,?,?,?)",
        (source_key, display_name, "egp", access_state),
    )


def record_probe_success(
    conn,
    *,
    source_key: str,
    family: str,
    adapter_sha: str,
    rows: int,
    total: int | None,
    shape_ok: bool,
    now: str | None = None,
) -> None:
    """Evidence of a probe = reachability + readable shape, never a collection claim."""
    stamp = now or _now()
    _ensure_registry_row(conn, source_key, source_key, "ALLOWED_AND_VERIFIED")
    conn.execute(
        "INSERT INTO adapter_health (source_key, family, adapter_sha, last_canary_ok_at_utc,"
        " consecutive_failures, last_error_kind, updated_at_utc) VALUES (?,?,?,?,0,NULL,?)"
        " ON CONFLICT(source_key, family, adapter_sha) DO UPDATE SET"
        " last_canary_ok_at_utc=excluded.last_canary_ok_at_utc, consecutive_failures=0,"
        " last_error_kind=NULL, updated_at_utc=excluded.updated_at_utc",
        (source_key, family, adapter_sha, stamp, stamp),
    )
    value = {
        "at": stamp,
        "adapter_sha": adapter_sha,
        "rows": rows,
        "total": total,
        "shape_ok": shape_ok,
        "state": PROBE_UNVERIFIED,
    }
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value_json, updated_at_utc) VALUES (?,?,?)",
        (f"canary:{source_key}:{family}", _json(value), stamp),
    )
    conn.commit()


def record_probe_failure(
    conn,
    *,
    source_key: str,
    family: str,
    adapter_sha: str,
    reason_kind: str,
    detail: str,
    now: str | None = None,
) -> None:
    stamp = now or _now()
    _ensure_registry_row(conn, source_key, source_key, "UNVERIFIED")
    conn.execute(
        "INSERT INTO adapter_health (source_key, family, adapter_sha, last_canary_ok_at_utc,"
        " consecutive_failures, last_error_kind, updated_at_utc) VALUES (?,?,?,NULL,1,?,?)"
        " ON CONFLICT(source_key, family, adapter_sha) DO UPDATE SET"
        " consecutive_failures=adapter_health.consecutive_failures+1,"
        " last_error_kind=excluded.last_error_kind, updated_at_utc=excluded.updated_at_utc",
        (source_key, family, adapter_sha, reason_kind, stamp),
    )
    conn.execute(
        "INSERT INTO source_access_events (source_key, family, from_state, to_state, reason_kind,"
        " detail_scrubbed, observed_at_utc) VALUES (?,?,?,?,?,?,?)",
        (source_key, family, None, "BLOCKED", reason_kind if reason_kind in _REASON_KINDS else "CANARY_FAILED",
         detail[:200], stamp),
    )
    conn.commit()


_REASON_KINDS = {
    "HTTP_401", "HTTP_403", "CAPTCHA", "WAF", "RATE_LIMIT_429", "TIMEOUT", "TLS_FAILURE",
    "REDIRECT_OFF_ALLOWLIST", "USER_DISABLED", "CANARY_FAILED", "POLICY",
}


def canary_state(conn, source_key: str, family: str, adapter_sha: str | None = None) -> dict[str, Any]:
    """Derive the ladder rung from rows, not from a caller's memory."""
    params: tuple[Any, ...] = (source_key, family)
    sha_clause = ""
    if adapter_sha is not None:
        sha_clause = " AND adapter_sha=?"
        params = (*params, adapter_sha)
    proven = conn.execute(
        "SELECT 1 FROM coverage_intervals ci"
        " JOIN coverage_proofs cp ON cp.id = ci.proof_id"
        " JOIN filter_boundary_rules r ON r.id = cp.boundary_rule_id"
        " WHERE ci.status='COMPLETE' AND ci.family=? AND ci.source_key=?"
        "   AND r.canary_verified=1" + (" AND cp.adapter_sha=?" if adapter_sha is not None else "") + " LIMIT 1",
        (family, source_key, adapter_sha) if adapter_sha is not None else (family, source_key),
    ).fetchone()
    if proven:
        return {"state": COLLECTION_PROVEN, "rule_verified": True, "probe_ok": True}
    health = conn.execute(
        "SELECT last_canary_ok_at_utc FROM adapter_health"
        " WHERE source_key=? AND family=?" + sha_clause,
        params,
    ).fetchone()
    rule = conn.execute(
        "SELECT 1 FROM filter_boundary_rules WHERE source_key=? AND family=? AND canary_verified=1"
        + (" AND adapter_sha=?" if adapter_sha is not None else "") + " LIMIT 1",
        params,
    ).fetchone()
    probe_ok = health is not None and health["last_canary_ok_at_utc"] is not None
    rule_verified = rule is not None
    state = RULE_VERIFIED if (probe_ok and rule_verified) else PROBE_UNVERIFIED
    return {"state": state, "rule_verified": rule_verified, "probe_ok": probe_ok,
            "probe_at": health["last_canary_ok_at_utc"] if health else None}


PageSourceFactory = Callable[[str], EgpHomeLane]


def probe_family(
    writer: WarehouseWriter,
    *,
    family: str,
    adapter_sha: str,
    lane_factory: PageSourceFactory | None = None,
) -> dict[str, Any]:
    """Fetch page 0 *without* a date filter and record reachability evidence.

    Returns the probe summary. On refusal the failure is recorded and the summary says so; no
    exception escapes into a job result because a probe is an operator action, not a run.
    """
    source = source_for(family)
    built = lane_factory(family) if lane_factory else None
    if isinstance(built, FetchLane):
        lane = EgpHomeLane(family=family, lane=built, window=SearchWindow(date_filter_verified=False))
    elif isinstance(built, EgpHomeLane):
        lane = built
    else:
        lane = EgpHomeLane(family=family, window=SearchWindow(date_filter_verified=False))
    try:
        page: Page = lane.fetch_page(0)
    except (AccessBlocked, FetchFailed) as exc:
        reason = "HTTP_403" if isinstance(exc, AccessBlocked) else "CANARY_FAILED"
        record_probe_failure(writer.conn, source_key=source.key, family=family,
                             adapter_sha=adapter_sha, reason_kind=reason,
                             detail=f"{type(exc).__name__}: {exc}")
        return {"ok": False, "reason": reason, "detail": str(exc)[:200]}
    finally:
        lane.close()
    record_probe_success(writer.conn, source_key=source.key, family=family,
                         adapter_sha=adapter_sha, rows=page.rows,
                         total=page.total_hint, shape_ok=page.payload is not None)
    return {"ok": True, "rows": page.rows, "total": page.total_hint,
            "state": canary_state(writer.conn, source.key, family, adapter_sha)["state"]}


def _json(value: object) -> str:
    import json

    return json.dumps(value, sort_keys=True)
