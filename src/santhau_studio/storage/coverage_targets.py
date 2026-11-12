"""Read-only planner: which day/family windows actually need reconciliation.

The desktop coverage page labels a cell "Cần đối chiếu" when the stored count
exceeds the trusted source total, and it discards legacy/drifted proofs. This
planner feeds the very same CoverageRow set into the very same summarizer, so
"what the UI flags" and "what gets audited" cannot drift apart. An open
HASH_DRIFT / COUNT_DRIFT gap is the second, complementary reason to re-check.
No network, no writes: the caller turns the result into RECONCILE tasks.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from typing import Any

from santhau_studio.desktop.collection_dates import VN
from santhau_studio.desktop.collection_totals import load_counts, load_totals, summarize
from santhau_studio.domain.coverage import Interval
from santhau_studio.storage.collection_scope import windows as scope_windows
from santhau_studio.storage.coverage import DRIFT_KINDS, load_rows


def _open_drift_gaps(conn: sqlite3.Connection, family: str) -> list[tuple[datetime, datetime]]:
    placeholders = ",".join("?" for _ in DRIFT_KINDS)
    rows = conn.execute(
        "SELECT interval_from_utc, interval_to_utc FROM reconcile_gaps "
        f"WHERE source_key='egp_home' AND family=? AND kind IN ({placeholders}) "
        "AND acknowledged_at_utc IS NULL",
        (family, *DRIFT_KINDS),
    ).fetchall()
    return [
        (
            datetime.fromisoformat(r[0].replace("Z", "+00:00")),
            datetime.fromisoformat(r[1].replace("Z", "+00:00")),
        )
        for r in rows
    ]


def targeted_reconcile_windows(
    conn: sqlite3.Connection, scope: dict, *, limit: int | None = None
) -> list[dict[str, Any]]:
    """Day/family VN windows the UI flags for re-check, capped by `limit`.

    Fail-safe: an unreadable/incomplete serving snapshot yields no targets, so
    the caller falls back to the ordinary repair path instead of guessing.
    """
    try:
        first = datetime.combine(date.fromisoformat(scope["date_from"]), time.min, tzinfo=VN)
        last = datetime.combine(
            date.fromisoformat(scope["date_to"]), time.min, tzinfo=VN
        ) + timedelta(days=1)
        counts = load_counts(conn, first, last)
        if counts is None:
            return []
        all_rows: dict[str, tuple] = {}
        all_gaps: dict[str, list[tuple[datetime, datetime]]] = {}
        window = Interval(first, last)
        for source in scope["sources"]:
            family = source["family"]
            if family not in all_rows:
                all_rows[family] = load_rows(conn, "egp_home", family, window)
                all_gaps[family] = _open_drift_gaps(conn, family)
        targets: list[dict[str, Any]] = []
        for _day, source, span in scope_windows(scope):
            family = source["family"]
            rows = all_rows.get(family, ())
            totals = load_totals(conn, family, rows)
            summary = summarize(family, span, rows, totals=totals, counts=counts)
            drifting_gap = any(
                gap_from < span.to_utc and gap_to > span.from_utc
                for gap_from, gap_to in all_gaps.get(family, ())
            )
            if not (summary["count_drift"] or drifting_gap):
                continue
            targets.append(
                {
                    "family": family,
                    "from": span.from_utc.isoformat(),
                    "to": span.to_utc.isoformat(),
                    "stored": summary["stored"],
                    "source_total": summary["source_total"],
                    "reason": "COUNT_DRIFT" if summary["count_drift"] else "NEEDS_RECONCILE",
                }
            )
            if limit is not None and len(targets) >= limit:
                break
        return targets
    except (sqlite3.Error, ValueError, KeyError, TypeError):
        return []
