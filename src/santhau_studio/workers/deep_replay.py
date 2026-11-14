"""Versioned, offline raw replay. Read-only preview is the default."""

import hashlib
import json
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path

from santhau_studio.collectors.cgttrg import public_spec
from santhau_studio.collectors.deep import DetailShapeError, parse_detail
from santhau_studio.collectors.public_deep import ADAPTER_VERSION, identity_problem
from santhau_studio.storage.deep import record_deep_capture
from santhau_studio.storage.deep_metadata import capture_metadata, write_task
from santhau_studio.storage.public_deep import task_state
from santhau_studio.storage.rawstore import RawObjectStore
from santhau_studio.storage.warehouse import WarehouseWriter, connect_read_only


def targets(conn, record_ids, limit):
    """Every capture still on an old parser, all three lanes.

    Parser v4 extracts prices, so a FETCHED capture is eligible too — the old
    target (HSMT PARSE_ERROR only) would leave every already-fetched price
    behind. Captures without a response (BLOCKED / unsupported) have no raw to
    re-parse and are excluded by the response_object_sha condition.
    """
    from santhau_studio.collectors.cgttrg import PARSER_VERSION

    found = []
    for row in conn.execute(
        "SELECT r.*,t.lane,t.response_object_sha AS replay_sha,t.capture_id,t.listing_object_sha AS replay_listing_sha,"
        "t.provenance_json AS task_provenance,c.latest_capture_seq,c.captured_at_utc,c.refresh_due_at_utc "
        "FROM deep_tasks t JOIN source_records r ON r.id=t.source_record_id JOIN deep_captures c ON c.id=t.capture_id "
        "WHERE r.source_key='egp_home' AND r.family='TBMT' AND t.lane IN ('HSMT','DETAIL','REOFFER') "
        "AND t.response_object_sha IS NOT NULL ORDER BY r.id, t.lane"
    ):
        if record_ids is not None and row["id"] not in record_ids:
            continue
        if (
            capture_metadata(conn, row["capture_id"], row["latest_capture_seq"])["parser_version"]
            >= PARSER_VERSION
        ):
            continue
        found.append(dict(row))
        if len(found) >= limit:
            break
    return found


def inspect(store, record):
    raw = store.read(record["replay_sha"])
    if hashlib.sha256(raw).hexdigest() != record["replay_sha"]:
        raise ValueError("REPLAY_RAW_HASH_MISMATCH")
    payload = json.loads(raw)
    receipt = json.loads(record["task_provenance"] or "{}")
    issue = identity_problem(payload, record, record["lane"], receipt.get("request", {}).get("id"))
    parsed = None
    if not issue:
        try:
            parsed = parse_detail(public_spec(record["family"], record["lane"]), payload)
        except (DetailShapeError, ValueError, TypeError) as exc:
            issue = str(exc) or type(exc).__name__
    return payload, parsed, issue, receipt


def replay(root, *, execute=False, record_ids=None, limit=100):
    root = Path(root)
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be 1..1000")
    if record_ids is not None and any(type(i) is not int or i <= 0 for i in record_ids):
        raise ValueError("record_ids must be positive integers")
    result = {
        "ok": True,
        "dry_run": not execute,
        "network_requests": 0,
        "eligible": 0,
        "applied": 0,
        "repaired": 0,
        "records": [],
    }
    with closing(connect_read_only(root / "warehouse.sqlite")) as conn:
        conn.execute("BEGIN")
        records = targets(conn, record_ids, limit)
    store = RawObjectStore(root, read_only=True)
    try:
        for record in records:
            _, parsed, issue, _ = inspect(store, record)
            result["records"].append(
                {
                    "record_id": record["id"],
                    "natural_key": record["natural_key"],
                    "response_sha": record["replay_sha"],
                    "capability": parsed.capability if parsed else "UNKNOWN",
                    "warnings": list(parsed.warnings) if parsed else [],
                    "problems": [issue] if issue else list(parsed.problems),
                    "goods": sum(len(lot.goods) for lot in parsed.lots)
                    + len(parsed.unallotted_goods)
                    if parsed
                    else 0,
                    "money": len(parsed.money) if parsed else 0,
                    "document_references": len(parsed.documents) if parsed else 0,
                }
            )
    finally:
        store.close()
    result["eligible"] = len(records)
    if not execute or not records:
        return result
    with closing(WarehouseWriter.open(root, create_schema=True, role="parser-replay")) as writer:
        # Re-evaluate under the writer lock; a stale preview never authorizes stale targets.
        records = targets(writer.conn, record_ids, limit)
        # One run per lane: runs.job_kind is a closed enum, so a single replay run
        # cannot honestly cover HSMT + DETAIL + REOFFER at once.
        by_lane: dict[str, list] = {}
        for record in records:
            by_lane.setdefault(record["lane"], []).append(record)
        for lane, lane_records in by_lane.items():
            run_id = "replay-" + lane.lower() + "-" + uuid.uuid4().hex
            writer.register_run(run_id, run_id, lane, requested_by="offline-parser-replay")
            applied = repaired = 0
            for record in lane_records:
                payload, parsed, issue, receipt = inspect(writer.store, record)
                spec = public_spec(record["family"], record["lane"])
                captured = datetime.fromisoformat(record["captured_at_utc"])
                result_capture = record_deep_capture(
                    writer,
                    run_id=run_id,
                    source_record_id=record["id"],
                    spec=spec,
                    completeness="PARTIAL" if issue or parsed.problems else "FULL",
                    response_payload=payload,
                    detail=payload,
                    now=captured,
                    parse_error=issue,
                )
                writer.conn.execute(
                    "UPDATE deep_captures SET refresh_due_at_utc=? WHERE id=?",
                    (record["refresh_due_at_utc"], result_capture.capture_id),
                )
                problems = [issue] if issue else list(result_capture.problems)
                state = "PARSE_ERROR" if problems or result_capture.stale else "FETCHED"
                capability = "IDENTITY_QUARANTINE" if issue and any(word in issue for word in ("IDENTITY", "UUID", "VERSION_MISMATCH")) else (parsed.capability if parsed else "UNKNOWN")
                warnings = parsed.warnings if parsed else ()
                task_state(
                    writer.conn,
                    record["id"],
                    record["lane"],
                    state,
                    listing_sha=record["replay_listing_sha"],
                    response_sha=record["replay_sha"],
                    capture_id=result_capture.capture_id,
                    error=json.dumps(problems) if problems else None,
                    provenance={
                        **receipt,
                        "replay": {
                            "parser_version": spec.parser_version,
                            "network_requests": 0,
                            "previous_capture_seq": record["latest_capture_seq"],
                        },
                        "capability": capability,
                        "warnings": list(warnings),
                    },
                    failure_kind="PARSE" if state == "PARSE_ERROR" else None,
                )
                write_task(
                    writer.conn,
                    record["id"],
                    record["lane"],
                    parser_version=spec.parser_version,
                    adapter_version=ADAPTER_VERSION,
                    capability=capability,
                    warnings=warnings,
                )
                applied += 1
                repaired += int(state == "FETCHED")
            result["applied"] += applied
            result["repaired"] += repaired
            writer.finish_run(
                run_id, "COMPLETED" if applied == repaired else "PARTIAL"
            )
    return result
