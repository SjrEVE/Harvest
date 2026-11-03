"""Deep capture persistence and refresh decisions (§10.4, B6, FAULT-17).

Every capture lands in ``deep_capture_history`` in the same transaction that moves
``deep_captures.latest_capture_seq``, so a refresh can never delete evidence: the raw
objects and the full history stay, only the *derived* projections (memberships, lots,
goods rows) are re-cut from the newest parse.

Staleness is a decision, not a timestamp read: SOURCE_VERSION_CHANGED when the source's
version axis moved under a capture (FAULT-17), REFRESH_INTERVAL_ELAPSED when the capture's
deadline passed, RECONCILE_MISMATCH when a re-scan disagreed, QUARANTINED_MEMBER when a
member of the payload could not be parsed. A stale capture is refreshable, never skipped
forever.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from santhau_studio.collectors.deep import (
    DeepLaneSpec,
    ParsedDetail,
    parse_detail,
)
from santhau_studio.domain.identity import canonical_bytes, content_hash
from santhau_studio.storage.warehouse import WarehouseWriter

STALE_SOURCE_VERSION_CHANGED = "SOURCE_VERSION_CHANGED"
STALE_REFRESH_INTERVAL_ELAPSED = "REFRESH_INTERVAL_ELAPSED"
STALE_RECONCILE_MISMATCH = "RECONCILE_MISMATCH"
STALE_QUARANTINED_MEMBER = "QUARANTINED_MEMBER"


@dataclass(frozen=True)
class DeepCaptureResult:
    capture_id: int
    capture_seq: int
    completeness: str
    stale: int
    stale_reason: str | None
    response_object_sha: str
    record_object_sha: str


def record_deep_capture(
    writer: WarehouseWriter,
    *,
    run_id: str,
    source_record_id: int,
    spec: DeepLaneSpec,
    completeness: str,
    response_payload: Any,
    detail: dict[str, Any],
    now: datetime,
    lane_status: str = "FULL",
    refresh_after_days: int | None = None,
) -> DeepCaptureResult:
    """Persist one deep capture atomically: objects, latest row, history, derived rows.

    ``detail`` is the parsed-record view of the response; both are hashed and stored, and
    the capture cites both shas, so a derivation row can always be re-cut from bytes that
    still exist (FAULT-16 posture).
    """
    parsed = parse_detail(spec, detail)
    conn = writer.conn
    days = spec.refresh_after_days if refresh_after_days is None else refresh_after_days
    due = _iso(now + timedelta(days=days)) if days is not None else None
    conn.execute("BEGIN IMMEDIATE")
    try:
        response_ref = writer.store.put(canonical_bytes(response_payload))
        detail_ref = writer.store.put(canonical_bytes(detail))
        writer.register_object(response_ref)
        writer.register_object(detail_ref)
        existing = conn.execute(
            "SELECT id, latest_capture_seq FROM deep_captures"
            " WHERE source_record_id=? AND lane=? AND endpoint_kind=?",
            (source_record_id, spec.lane, spec.endpoint_kind),
        ).fetchone()
        if existing is None:
            cur = conn.execute(
                "INSERT INTO deep_captures (source_record_id, lane, endpoint_kind, latest_capture_seq,"
                " completeness, captured_at_utc, refresh_due_at_utc, stale, stale_reason,"
                " source_version_json, response_object_sha, record_object_sha, content_hash, lane_status)"
                " VALUES (?,?,?,1,?,?,?,?,?,?,?,?,?,?)",
                (
                    source_record_id, spec.lane, spec.endpoint_kind, completeness, _iso(now), due,
                    0, None, parsed.source_version_json, response_ref.sha256, detail_ref.sha256,
                    content_hash(detail), lane_status,
                ),
            )
            capture_id, seq = int(cur.lastrowid), 1
        else:
            capture_id, seq = int(existing["id"]), int(existing["latest_capture_seq"]) + 1
            conn.execute(
                "UPDATE deep_captures SET latest_capture_seq=?, completeness=?, captured_at_utc=?,"
                " refresh_due_at_utc=?, stale=0, stale_reason=NULL, source_version_json=?,"
                " response_object_sha=?, record_object_sha=?, content_hash=?, lane_status=? WHERE id=?",
                (
                    seq, completeness, _iso(now), due, parsed.source_version_json,
                    response_ref.sha256, detail_ref.sha256, content_hash(detail), lane_status, capture_id,
                ),
            )
        conn.execute(
            "INSERT INTO deep_capture_history (deep_capture_id, capture_seq, captured_at_utc,"
            " response_object_sha, record_object_sha, content_hash, completeness, source_version_json,"
            " captured_by_run_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                capture_id, seq, _iso(now), response_ref.sha256, detail_ref.sha256,
                content_hash(detail), completeness, parsed.source_version_json, run_id,
            ),
        )
        _write_derived(conn, writer, source_record_id, spec, parsed)
        stale, stale_reason = 0, None
        if parsed.problems:
            stale, stale_reason = 1, STALE_QUARANTINED_MEMBER
            conn.execute(
                "UPDATE deep_captures SET stale=1, stale_reason=? WHERE id=?",
                (stale_reason, capture_id),
            )
        conn.execute("COMMIT")
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise
    return DeepCaptureResult(capture_id, seq, completeness, stale, stale_reason, response_ref.sha256, detail_ref.sha256)


def _write_derived(
    conn: sqlite3.Connection, writer: WarehouseWriter, source_record_id: int,
    spec: DeepLaneSpec, parsed: ParsedDetail,
) -> None:
    """Re-cut the derived projections from the newest parse.

    Deleting these rows is not destroying evidence — the raw objects and the capture history
    remain; the projections are rebuilt from bytes that still exist.
    """
    provenance = {
        "lane": spec.lane,
        "endpoint_kind": spec.endpoint_kind,
        "source_version_json": parsed.source_version_json,
    }
    if spec.hsmt_columns:
        # HSMT columns ride with the detail capture; the verbatim projection is provenance here.
        provenance["hsmt_columns"] = list(spec.hsmt_columns)
    provenance_json = _json(provenance)

    conn.execute("DELETE FROM goods_rows WHERE source_record_id=?", (source_record_id,))
    conn.execute("DELETE FROM lots WHERE source_record_id=?", (source_record_id,))
    conn.execute("DELETE FROM contractor_memberships WHERE source_record_id=?", (source_record_id,))

    for member in parsed.members:
        member_ref = writer.store.put(canonical_bytes(member.raw))
        writer.register_object(member_ref)
        conn.execute(
            "INSERT INTO contractor_memberships (source_record_id, party_object_sha, party_name,"
            " party_tax_id_presence, party_tax_id, role, membership_share_presence, membership_share,"
            " ranking, provenance_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                source_record_id, member.member_object_sha, member.party_name,
                member.party_tax_id_presence, member.party_tax_id, member.role,
                member.membership_share_presence, member.membership_share, member.ranking,
                provenance_json,
            ),
        )
    for lot in parsed.lots:
        cur = conn.execute(
            "INSERT INTO lots (source_record_id, lot_no_raw, lot_label, lot_version, goods_count,"
            " provenance_json) VALUES (?,?,?,?,?,?)",
            (
                source_record_id, lot.lot_no_raw, lot.lot_label, lot.lot_version,
                len(lot.goods), provenance_json,
            ),
        )
        lot_id = int(cur.lastrowid)
        for row in lot.goods:
            conn.execute(
                "INSERT INTO goods_rows (lot_id, source_record_id, row_ordinal, name, specification,"
                " unit, quantity_presence, quantity, quantity_raw, provenance_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    lot_id, source_record_id, row.row_ordinal, row.name, row.specification, row.unit,
                    row.quantity_presence, row.quantity, row.quantity_raw, provenance_json,
                ),
            )


# ------------------------------------------------------------------ staleness decisions

def mark_stale(conn: sqlite3.Connection, deep_capture_id: int, reason: str) -> bool:
    """Mark one capture stale. A capture already stale keeps its first reason."""
    cur = conn.execute(
        "UPDATE deep_captures SET stale=1, stale_reason=?, lane_status='STALE'"
        " WHERE id=? AND stale=0",
        (reason, deep_capture_id),
    )
    conn.commit()
    return cur.rowcount > 0


def mark_stale_on_version_change(
    conn: sqlite3.Connection, *, source_record_id: int, observed_version_json: str,
) -> int:
    """FAULT-17: a listing re-observation carrying a different version axis stales captures."""
    rows = conn.execute(
        "SELECT id FROM deep_captures WHERE source_record_id=? AND source_version_json<>?",
        (source_record_id, observed_version_json),
    ).fetchall()
    changed = 0
    for row in rows:
        if mark_stale(conn, int(row["id"]), STALE_SOURCE_VERSION_CHANGED):
            changed += 1
    return changed


def mark_reconcile_mismatch(conn: sqlite3.Connection, *, source_record_id: int) -> int:
    rows = conn.execute(
        "SELECT id FROM deep_captures WHERE source_record_id=? AND stale=0", (source_record_id,)
    ).fetchall()
    changed = 0
    for row in rows:
        if mark_stale(conn, int(row["id"]), STALE_RECONCILE_MISMATCH):
            changed += 1
    return changed


def apply_refresh_interval(conn: sqlite3.Connection, *, now: datetime) -> int:
    """Stale every fresh capture whose refresh deadline has passed (§10.4)."""
    cur = conn.execute(
        "UPDATE deep_captures SET stale=1, stale_reason=?, lane_status='STALE'"
        " WHERE stale=0 AND refresh_due_at_utc IS NOT NULL AND refresh_due_at_utc < ?",
        (STALE_REFRESH_INTERVAL_ELAPSED, _iso(now)),
    )
    conn.commit()
    return cur.rowcount


# ------------------------------------------------------------------ read models (UI-08)

def done_ids(
    conn: sqlite3.Connection, source_record_ids: Iterable[int], *, lane: str, endpoint_kind: str,
) -> dict[int, dict[str, Any]]:
    """For a candidate id set: what is captured, when, and when it is due (§10.4)."""
    ids = list(source_record_ids)
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    rows = conn.execute(
        " SELECT dc.source_record_id, dc.completeness, dc.stale, dc.stale_reason,"
        "        dc.refresh_due_at_utc, dc.lane_status, h.captured_at_utc"
        "   FROM deep_captures dc"
        "   JOIN deep_capture_history h ON h.deep_capture_id = dc.id"
        "    AND h.capture_seq = dc.latest_capture_seq"
        f"  WHERE dc.lane = ? AND dc.endpoint_kind = ? AND dc.source_record_id IN ({marks})",
        (lane, endpoint_kind, *ids),
    )
    return {
        int(row["source_record_id"]): {
            "completeness": row["completeness"],
            "stale": bool(row["stale"]),
            "stale_reason": row["stale_reason"],
            "lane_status": row["lane_status"],
            "captured_at_utc": row["captured_at_utc"],
            "refresh_due_at_utc": row["refresh_due_at_utc"],
        }
        for row in rows
    }


def refresh_queue(conn: sqlite3.Connection, *, now: datetime, limit: int = 200) -> list[dict[str, Any]]:
    """The UI-08 work queue: stale or incomplete captures first, then deadline-due ones."""
    rows = conn.execute(
        " SELECT dc.id, dc.source_record_id, dc.lane, dc.endpoint_kind, dc.completeness,"
        "        dc.stale, dc.stale_reason, dc.refresh_due_at_utc, h.captured_at_utc,"
        "        h.source_version_json"
        "   FROM deep_captures dc"
        "   JOIN deep_capture_history h ON h.deep_capture_id = dc.id"
        "    AND h.capture_seq = dc.latest_capture_seq"
        "  WHERE dc.stale = 1 OR dc.completeness <> 'FULL'"
        "     OR (dc.refresh_due_at_utc IS NOT NULL AND dc.refresh_due_at_utc <= ?)"
        "  ORDER BY dc.stale DESC, dc.refresh_due_at_utc, dc.id"
        "  LIMIT ?",
        (_iso(now), limit),
    )
    return [dict(row) for row in rows]


def _json(value: object) -> str:
    import json

    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
