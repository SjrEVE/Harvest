"""Version-bound parser capabilities, without overloading closed lifecycle enums."""

import json
from datetime import UTC, datetime


def present(conn, name):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone())


def capture_metadata(conn, capture_id, seq):
    if not present(conn, "deep_capture_parse_metadata"):
        return {"parser_version": 1, "capability": "UNKNOWN", "warnings": []}
    row = conn.execute(
        "SELECT * FROM deep_capture_parse_metadata WHERE deep_capture_id=? AND capture_seq=?",
        (capture_id, seq),
    ).fetchone()
    return (
        {**dict(row), "warnings": json.loads(row["warnings_json"])}
        if row
        else {"parser_version": 1, "capability": "UNKNOWN", "warnings": []}
    )


def write_capture(conn, capture_id, seq, version, parsed):
    conn.execute(
        "INSERT INTO deep_capture_parse_metadata VALUES(?,?,?,?,?,?)",
        (
            capture_id,
            seq,
            version,
            parsed.capability,
            json.dumps(parsed.warnings),
            datetime.now(UTC).isoformat(),
        ),
    )


def task_metadata(conn, record_id, lane):
    if not present(conn, "deep_task_metadata"):
        return {"parser_version": 1, "adapter_version": 1, "capability": "UNKNOWN", "warnings": []}
    row = conn.execute(
        "SELECT m.* FROM deep_task_metadata m JOIN deep_tasks t ON t.source_record_id=m.source_record_id AND t.lane=m.lane "
        "WHERE m.source_record_id=? AND m.lane=? AND m.task_updated_at_utc=t.updated_at_utc",
        (record_id, lane),
    ).fetchone()
    return (
        {**dict(row), "warnings": json.loads(row["warnings_json"])}
        if row
        else {"parser_version": 1, "adapter_version": 1, "capability": "UNKNOWN", "warnings": []}
    )


def write_task(conn, record_id, lane, *, parser_version, adapter_version, capability, warnings=()):
    stamp = conn.execute(
        "SELECT updated_at_utc FROM deep_tasks WHERE source_record_id=? AND lane=?",
        (record_id, lane),
    ).fetchone()[0]
    conn.execute(
        "INSERT OR REPLACE INTO deep_task_metadata VALUES(?,?,?,?,?,?,?)",
        (record_id, lane, parser_version, adapter_version, capability, json.dumps(warnings), stamp),
    )


def state_counts(conn):
    if not present(conn, "deep_tasks"):
        return {}
    if not present(conn, "deep_task_metadata"):
        return dict(conn.execute("SELECT state,COUNT(*) FROM deep_tasks GROUP BY state"))
    return dict(
        conn.execute(
            "SELECT CASE WHEN COALESCE(m.capability,'') LIKE 'UNSUPPORTED%' THEN 'UNSUPPORTED' ELSE t.state END display_state,COUNT(*) "
            "FROM deep_tasks t LEFT JOIN deep_task_metadata m ON t.source_record_id=m.source_record_id AND t.lane=m.lane "
            "AND t.updated_at_utc=m.task_updated_at_utc GROUP BY display_state"
        )
    )


def package_counts(conn):
    """Package counts are separate from the three public lane task counts."""
    if not present(conn, "deep_tasks"):
        return {}
    if not present(conn, "deep_task_metadata"):
        return dict(conn.execute("SELECT state,COUNT(DISTINCT source_record_id) FROM deep_tasks GROUP BY state"))
    return dict(conn.execute(
        "SELECT CASE WHEN COALESCE(m.capability,'') LIKE 'UNSUPPORTED%' THEN 'UNSUPPORTED' ELSE t.state END display_state,COUNT(DISTINCT t.source_record_id) "
        "FROM deep_tasks t LEFT JOIN deep_task_metadata m ON t.source_record_id=m.source_record_id AND t.lane=m.lane "
        "AND t.updated_at_utc=m.task_updated_at_utc GROUP BY display_state"))
