"""Bounded public-detail inspection; counts and rows share one eligibility contract."""

import json
import sqlite3
from datetime import UTC, datetime

from santhau_studio.collectors.cgttrg import ENDPOINTS

LANES = ("DETAIL", "HSMT", "REOFFER")
STATES = (
    "FETCHED",
    "SOURCE_EMPTY",
    "PARTIAL",
    "PARSE_ERROR",
    "BLOCKED",
    "FAILED",
    "STALE",
    "NOT_COLLECTED",
)


def _model(conn, record_id=None, *, honor_refresh_due=None):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='deep_tasks'").fetchone()
    tasks = (
        "SELECT * FROM deep_tasks"
        if exists
        else """SELECT NULL source_record_id,
        NULL lane,NULL state,NULL capture_id,NULL listing_object_sha,NULL updated_at_utc,
        NULL error,NULL provenance_json WHERE 0"""
    )
    lanes = ",".join(f"('{lane}','public-cgttrg-{ENDPOINTS[lane]}')" for lane in LANES)
    fields = conn.execute("SELECT 1 FROM sqlite_master WHERE name='record_fields'").fetchone()
    shortlist = (
        "AND r.id IN (SELECT record_id FROM record_fields WHERE procurement_type='CGTTRG')"
        if fields
        else ""
    )
    restriction = "AND r.id=:record_id" if record_id is not None else ""
    sql = f"""WITH tasks AS ({tasks}), lanes(lane,endpoint) AS (VALUES {lanes}),
    eligible AS (
      SELECT r.id FROM source_records r JOIN source_record_versions v
      ON v.source_record_id=r.id AND v.content_version_seq=r.current_content_version
      WHERE r.source_key='egp_home' AND r.family='TBMT' {restriction} {shortlist}
        AND json_extract(v.normalized_json,'$.bidForm')='CGTTRG'
    ), ids AS (
      SELECT id FROM eligible UNION SELECT source_record_id FROM tasks
      UNION SELECT source_record_id FROM deep_captures UNION SELECT :record_id WHERE :record_id IS NOT NULL
    ),
    records AS (
      SELECT r.id,r.source_key,r.family,r.natural_key,r.current_record_object_sha,
        (r.id IN (SELECT id FROM eligible)) supported
      FROM ids JOIN source_records r ON r.id=ids.id WHERE 1=1 {restriction}
    ), pairs AS (
      SELECT r.*,l.lane,l.endpoint FROM records r CROSS JOIN lanes l
      WHERE r.supported=1 OR :record_id IS NOT NULL
      UNION ALL
      SELECT r.*,t.lane,NULL endpoint FROM tasks t JOIN records r ON r.id=t.source_record_id
      WHERE r.supported=0 AND :record_id IS NULL
    ), current AS (
      SELECT p.id source_record_id,p.family,p.natural_key,p.lane,p.supported,0 legacy,
        'task:'||p.id||':'||p.lane row_key,
        CASE WHEN t.state IN ('FETCHED','SOURCE_EMPTY') AND
          (c.id IS NULL OR t.listing_object_sha IS NOT p.current_record_object_sha
           OR c.stale=1  -- PARTIAL/DOCUMENT_ONLY is valid v5, not stale
           OR (:honor_refresh_due=1 AND c.refresh_due_at_utc IS NOT NULL AND
               COALESCE(julianday(c.refresh_due_at_utc),0)<=julianday('now'))) THEN 'STALE'
          WHEN t.state IN ('FETCHED','SOURCE_EMPTY') AND c.completeness='PARTIAL'
            AND COALESCE(t.error,'') NOT IN ('','[]','null') THEN 'PARTIAL'
          WHEN t.state IS NOT NULL THEN t.state
          WHEN c.id IS NOT NULL THEN CASE WHEN c.stale=1 OR
            (:honor_refresh_due=1 AND c.refresh_due_at_utc IS NOT NULL AND COALESCE(julianday(c.refresh_due_at_utc),0)<=julianday('now'))
            THEN 'STALE' WHEN c.completeness='FULL' THEN 'FETCHED' ELSE 'PARSE_ERROR' END
          WHEN p.supported=1 THEN 'NOT_COLLECTED' ELSE 'UNSUPPORTED' END state,
        c.id capture_id,c.completeness,c.captured_at_utc,c.refresh_due_at_utc,c.stale_reason,
        COALESCE(t.updated_at_utc,c.captured_at_utc) updated_at_utc,t.error,
        COALESCE(t.provenance_json,'{{}}') provenance_json,
        CASE WHEN c.refresh_due_at_utc IS NOT NULL AND COALESCE(julianday(c.refresh_due_at_utc),0)<=julianday('now')
          THEN 1 ELSE 0 END refresh_due_elapsed
      FROM pairs p LEFT JOIN tasks t ON t.source_record_id=p.id AND t.lane=p.lane
      LEFT JOIN deep_captures c ON c.source_record_id=p.id AND c.lane=p.lane AND
        ((t.source_record_id IS NOT NULL AND c.id=t.capture_id) OR
         (t.source_record_id IS NULL AND c.endpoint_kind=p.endpoint))
    ), legacy AS (
      SELECT r.id source_record_id,r.family,r.natural_key,c.lane,0 supported,1 legacy,
        'capture:'||c.id row_key,
        CASE WHEN c.stale=1 OR (:honor_refresh_due=1 AND c.refresh_due_at_utc IS NOT NULL AND
          COALESCE(julianday(c.refresh_due_at_utc),0)<=julianday('now')) THEN 'STALE'
          WHEN c.completeness='FULL' THEN 'FETCHED' ELSE 'PARSE_ERROR' END state,
        c.id capture_id,c.completeness,c.captured_at_utc,c.refresh_due_at_utc,c.stale_reason,
        c.captured_at_utc updated_at_utc,NULL error,'{{}}' provenance_json,
        CASE WHEN c.refresh_due_at_utc IS NOT NULL AND COALESCE(julianday(c.refresh_due_at_utc),0)<=julianday('now')
          THEN 1 ELSE 0 END refresh_due_elapsed
      FROM deep_captures c JOIN records r ON r.id=c.source_record_id
      WHERE NOT EXISTS (SELECT 1 FROM current q WHERE q.capture_id=c.id)
        AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.source_record_id=c.source_record_id AND t.lane=c.lane)
    ), entries AS (SELECT * FROM current UNION ALL SELECT * FROM legacy)
    """
    # One instant for counts and page rows, including captures expiring during the read.
    if honor_refresh_due is None:
        honor_refresh_due = _refresh_expired(conn)
    return sql.replace("julianday('now')", "julianday(:now)"), {
        "record_id": record_id,
        "now": datetime.now(UTC).isoformat(),
        "honor_refresh_due": 1 if honor_refresh_due else 0,
    }


def _policy(conn):
    from santhau_studio.storage.policy_notice import policy_notice

    return policy_notice(conn)


def _refresh_expired(conn):
    try:
        row = conn.execute(
            "SELECT value_json FROM settings WHERE key='automatic_public_deep'"
        ).fetchone()
    except sqlite3.Error:
        return False
    if not row or not row[0]:
        return False
    try:
        value = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(value, dict) and value.get("refresh_expired") is True


def _row(row):
    value = dict(row)
    value["stale"] = value["state"] == "STALE"
    value["needs_refresh"] = value["state"] in ("STALE", "FAILED", "PARSE_ERROR", "BLOCKED")
    return value


def states(conn, record_id):
    sql, args = _model(conn, record_id)
    rows = [_row(r) for r in conn.execute(sql + "SELECT * FROM current ORDER BY lane", args)]
    from santhau_studio.storage.deep_metadata import task_metadata
    for row in rows:
        row["provenance"] = json.loads(row["provenance_json"])
        metadata = task_metadata(conn, record_id, row["lane"])
        row.update(capability=metadata["capability"], warnings=metadata["warnings"],
                   parser_version=metadata["parser_version"])
        if metadata["capability"].startswith("UNSUPPORTED"):
            row["state"] = "UNSUPPORTED"
    return rows


def queue(conn, payload=None):
    payload = payload or {}
    try:
        lane, state = payload.get("lane", ""), payload.get("state", "")
        if lane not in ("", *LANES) or state not in ("", *STATES):
            raise ValueError
        limit = min(100, max(1, int(payload.get("limit", 50))))
        offset = max(0, int(payload.get("offset", 0)))
        text = str(payload.get("text", "")).strip()[:200]
    except (ValueError, TypeError, OverflowError):
        return {
            "present": True,
            "rows": [],
            "summary": {},
            "error_code": "INVALID_FILTER",
            "error": "Bộ lọc không hợp lệ. Hãy chọn lại trạng thái và loại nội dung.",
        }
    policy = _policy(conn)
    sql, args = _model(conn, honor_refresh_due=policy["refresh_expired"])
    args.update(lane=lane, state=state, text=text, limit=limit, offset=offset)
    where = "WHERE (:lane='' OR lane=:lane) AND (:state='' OR state=:state) AND (:text='' OR instr(lower(natural_key),lower(:text))>0)"
    summary = dict.fromkeys(STATES, 0)
    summary.update(FULL=0, PARTIAL=0, stale=0, UNSUPPORTED=0, partial_saved=0)
    # v5 capability authority is applied per record in states(); the queue
    # summary uses the same single CTE read, with no extra SQL round-trips.
    legacy_count = 0
    try:
        for row in conn.execute(
            sql
            + "SELECT state,supported,legacy,completeness,COUNT(*) n FROM entries GROUP BY state,supported,legacy,completeness",
            args,
        ):
            if row["completeness"] in ("FULL", "PARTIAL"):
                summary[row["completeness"]] += row["n"]
            if row["state"] == "STALE":
                summary["stale"] += row["n"]
            if row["state"] == "PARTIAL":
                # completeness PARTIAL already uses summary["PARTIAL"]. Keep the display count apart.
                summary["partial_saved"] += row["n"]
            elif row["state"] in ("UNSUPPORTED", "BLOCKED") or (row["supported"] and not row["legacy"]):
                summary[row["state"]] += row["n"]
            else:
                legacy_count += row["n"]
        total = conn.execute(sql + "SELECT COUNT(*) FROM entries " + where, args).fetchone()[0]
        rows = [
            _row(r)
            for r in conn.execute(
                sql
                + "SELECT * FROM entries "
                + where
                + """
          ORDER BY CASE state WHEN 'FAILED' THEN 0 WHEN 'PARSE_ERROR' THEN 1 WHEN 'BLOCKED' THEN 2
            WHEN 'STALE' THEN 3 WHEN 'NOT_COLLECTED' THEN 4 ELSE 5 END,
          source_record_id,lane,row_key LIMIT :limit OFFSET :offset""",
                args,
            )
        ]
    except sqlite3.Error:
        return {
            "present": True,
            "rows": [],
            "summary": {},
            "error_code": "READ_FAILED",
            "error": "Không đọc được trạng thái thu thập. Hãy tải lại hoặc kiểm tra kho dữ liệu.",
        }
    return {
        "present": True,
        "rows": rows,
        "summary": summary,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(rows) < total,
        "eligible_total": sum(summary[s] for s in STATES if s != "PARTIAL") + summary.get("partial_saved", 0),
        "legacy_count": legacy_count,
        "scope": "TBMT chào giá trực tuyến rút gọn · mỗi hồ sơ có 3 nội dung cần lấy",
        "lanes": [{"lane": lane, "state": "PUBLIC_CGTTRG"} for lane in LANES]
        + [{"lane": lane, "state": "BLOCKED"} for lane in ("BOQ", "KQLCNT_FULL")],
        "refresh_policy": policy,
    }
