"""Escalating retry for deep-lane tasks: back off, then quarantine.

A deep task that fails is not retried on a flat 60-minute loop forever.
Each consecutive failure (same listing version) lengthens the cooldown
-- 1h, then 6h, then 24h, then 72h -- and after ``QUARANTINE_AFTER_FAILURES``
failures a non-transient task stops being auto-retried at all. A quarantined
task is only retried when something changed that could fix it (a new listing
version, a parser/adapter bump for parse/unsupported failures) or when an
operator releases it by hand (``deep-retry``).

Transient failures (network timeouts, host blocks, rate limits) never
quarantine: they cap at the 72h cooldown and can still recover on their own.

The state lives in ``deep_tasks.provenance_json`` under ``deep_retry`` so no
schema migration is needed and it survives the ``deep_task_metadata``
timestamp join: old rows without the key behave exactly as before (first
cooldown = ``failure_cooldown_minutes`` from the auto-deep config).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

FAILURE_STATES = ("BLOCKED", "FAILED", "PARSE_ERROR")
SUCCESS_STATES = ("FETCHED", "SOURCE_EMPTY")

# Cooldown (minutes) after consecutive failures 1, 2, 3, 4+. The first step
# is replaced at runtime by the configured ``failure_cooldown_minutes``
# (default 60); the ladder steps are fixed policy.
RETRY_COOLDOWN_MINUTES = (60, 360, 1440, 4320)
QUARANTINE_AFTER_FAILURES = 5

# Failure kinds. Only TRANSIENT is exempt from quarantine.
#   UNSUPPORTED -- gate refused the package (e.g. processApply=KHAC); no request spent.
#   FETCH      -- the endpoint answered but unusably (e.g. response is not JSON).
#   PARSE      -- a capture exists but this parser version cannot project it.
#   TRANSIENT  -- timeout / host block / rate limit / worker error; may clear alone.
NEVER_QUARANTINE_KINDS = frozenset({"TRANSIENT"})


def is_quarantined(consecutive_failures: int, failure_kind: str | None) -> bool:
    return (
        consecutive_failures >= QUARANTINE_AFTER_FAILURES
        and (failure_kind or "UNKNOWN") not in NEVER_QUARANTINE_KINDS
    )


def cooldown_minutes(
    consecutive_failures: int,
    failure_kind: str | None,
    first_cooldown_minutes: int = RETRY_COOLDOWN_MINUTES[0],
) -> int | None:
    """Minutes to wait before the next auto-retry, or None when quarantined."""
    if consecutive_failures <= 0:
        return first_cooldown_minutes
    if is_quarantined(consecutive_failures, failure_kind):
        return None
    step = RETRY_COOLDOWN_MINUTES[min(consecutive_failures, len(RETRY_COOLDOWN_MINUTES)) - 1]
    return first_cooldown_minutes if consecutive_failures == 1 else step


def read_retry_state(provenance_json) -> dict:
    """Tolerantly read the stored retry bookkeeping ({} for legacy rows)."""
    try:
        stored = (json.loads(provenance_json) or {}).get("deep_retry") or {}
    except (TypeError, ValueError):
        stored = {}
    try:
        consecutive = int(stored.get("consecutive_failures") or 0)
    except (TypeError, ValueError):
        consecutive = 0
    return {
        "consecutive_failures": max(0, consecutive),
        "failure_kind": stored.get("failure_kind"),
        "quarantined": bool(stored.get("quarantined")),
    }


def next_retry_state(
    previous: dict,
    *,
    state: str,
    failure_kind: str | None = None,
    listing_changed: bool = False,
) -> dict:
    """Retry bookkeeping to store after a task lands in ``state``."""
    if state in SUCCESS_STATES:
        return {"consecutive_failures": 0, "failure_kind": None, "quarantined": False}
    if state not in FAILURE_STATES:
        return dict(previous)
    consecutive = (
        1
        if listing_changed or previous["consecutive_failures"] == 0
        else previous["consecutive_failures"] + 1
    )
    kind = failure_kind or previous["failure_kind"] or "UNKNOWN"
    quarantined = is_quarantined(consecutive, kind)
    result = {
        "consecutive_failures": consecutive,
        "failure_kind": kind,
        "quarantined": quarantined,
    }
    if quarantined and not previous["quarantined"]:
        result["quarantined_at_utc"] = datetime.now(UTC).isoformat()
    return result


def cleared_retry_state() -> dict:
    """Bookkeeping after an operator manually releases a task (``deep-retry``)."""
    return {"consecutive_failures": 0, "failure_kind": None, "quarantined": False}
