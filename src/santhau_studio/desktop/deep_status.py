"""Read model for public deep states, including absence of a capture."""

import json

LANES = ("DETAIL", "HSMT", "REOFFER")


def states(conn, record_id):
    present = conn.execute("SELECT 1 FROM sqlite_master WHERE name='deep_tasks'").fetchone()
    rows = (
        {
            r["lane"]: dict(r)
            for r in conn.execute("SELECT * FROM deep_tasks WHERE source_record_id=?", (record_id,))
        }
        if present
        else {}
    )
    record = conn.execute(
        "SELECT current_record_object_sha FROM source_records WHERE id=?", (record_id,)
    ).fetchone()
    for lane in LANES:
        row = rows.setdefault(lane, {"lane": lane, "state": "NOT_COLLECTED"})
        if row.get("capture_id") and row["state"] in ("FETCHED", "SOURCE_EMPTY"):
            capture = conn.execute(
                "SELECT stale,refresh_due_at_utc FROM deep_captures WHERE id=?",
                (row["capture_id"],),
            ).fetchone()
            from datetime import UTC, datetime

            if record and (
                row.get("listing_object_sha") != record[0]
                or (
                    capture
                    and (
                        capture[0]
                        or (
                            capture[1]
                            and capture[1] < datetime.now(UTC).isoformat().replace("+00:00", "Z")
                        )
                    )
                )
            ):
                row["state"] = "STALE"
        row["provenance"] = json.loads(row.get("provenance_json") or "{}")
        from santhau_studio.storage.deep_metadata import task_metadata
        metadata = task_metadata(conn, record_id, lane)
        row.update(capability=metadata["capability"], warnings=metadata["warnings"], parser_version=metadata["parser_version"])
        if metadata["capability"].startswith("UNSUPPORTED"):
            row["state"] = "UNSUPPORTED"
    return list(rows.values())


def queue(conn):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='deep_tasks'").fetchone()
    rows = []
    if exists:
        for r in conn.execute(
            "SELECT t.*,s.family,s.natural_key FROM deep_tasks t JOIN source_records s ON s.id=t.source_record_id ORDER BY t.updated_at_utc DESC LIMIT 500"
        ):
            item = dict(r)
            item["state"] = next(
                s["state"]
                for s in states(conn, item["source_record_id"])
                if s["lane"] == item["lane"]
            )
            item.update(
                completeness=item["state"],
                stale=item["state"] == "STALE",
                captured_at_utc=item["updated_at_utc"],
            )
            rows.append(item)
    eligible = conn.execute(
        "SELECT COUNT(*) FROM source_records r JOIN source_record_versions v ON v.source_record_id=r.id AND v.content_version_seq=r.current_content_version WHERE r.family='TBMT' AND json_extract(v.normalized_json,'$.bidForm')='CGTTRG'"
    ).fetchone()[0]
    summary = dict.fromkeys(
        ("FETCHED", "SOURCE_EMPTY", "PARSE_ERROR", "BLOCKED", "FAILED", "STALE", "NOT_COLLECTED"), 0
    )
    if exists:
        for r in conn.execute(
            "SELECT CASE WHEN t.state IN ('FETCHED','SOURCE_EMPTY') AND "
            "(t.listing_object_sha<>s.current_record_object_sha OR c.stale=1 OR c.refresh_due_at_utc<strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
            "THEN 'STALE' ELSE t.state END AS display_state,COUNT(*) "
            "FROM deep_tasks t JOIN source_records s ON s.id=t.source_record_id "
            "LEFT JOIN deep_captures c ON c.id=t.capture_id GROUP BY display_state"
        ):
            summary[r[0]] = r[1]
        attempted = conn.execute("SELECT COUNT(*) FROM deep_tasks").fetchone()[0]
    else:
        attempted = 0
    summary["NOT_COLLECTED"] = max(0, eligible * len(LANES) - attempted)
    from santhau_studio.storage.deep_metadata import state_counts
    display_counts = state_counts(conn)
    summary["UNSUPPORTED"] = display_counts.get("UNSUPPORTED", 0)
    summary["BLOCKED"] = display_counts.get("BLOCKED", 0)
    # Preserve visibility of pre-migration captures, which have no operational task row.
    for legacy in conn.execute(
        "SELECT c.*,s.family,s.natural_key FROM deep_captures c JOIN source_records s ON s.id=c.source_record_id"
    ):
        if any(
            r["source_record_id"] == legacy["source_record_id"] and r["lane"] == legacy["lane"]
            for r in rows
        ):
            continue
        item = dict(legacy)
        item["state"] = (
            "STALE"
            if item["stale"]
            else ("FETCHED" if item["completeness"] == "FULL" else "PARSE_ERROR")
        )
        item["needs_refresh"] = bool(item["stale"]) or item["completeness"] != "FULL"
        rows.append(item)
    summary["FULL"] = conn.execute(
        "SELECT COUNT(*) FROM deep_captures WHERE completeness='FULL'"
    ).fetchone()[0]
    summary["PARTIAL"] = conn.execute(
        "SELECT COUNT(*) FROM deep_captures WHERE completeness='PARTIAL'"
    ).fetchone()[0]
    summary["stale"] = conn.execute("SELECT COUNT(*) FROM deep_captures WHERE stale=1").fetchone()[
        0
    ]
    return {
        "present": True,
        "rows": rows,
        "summary": summary,
        "scope": "Chỉ CGTTRG/TBMT đã xác minh; số trạng thái theo hồ sơ / lane",
        "lanes": [{"lane": lane, "state": "PUBLIC_CGTTRG"} for lane in LANES]
        + [{"lane": lane, "state": "BLOCKED"} for lane in ("BOQ", "KQLCNT_FULL")],
    }
