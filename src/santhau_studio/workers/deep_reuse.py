"""Reuse verified response evidence across consumers of the same public endpoint."""

import json
from dataclasses import dataclass
from datetime import datetime

from santhau_studio.collectors.deep import DeepResponse
from santhau_studio.collectors.public_deep import identity_problem
from santhau_studio.storage.deep_metadata import capture_metadata
from santhau_studio.storage.rawstore import ObjectCorruptError


@dataclass(frozen=True)
class ReusedCapture:
    response: DeepResponse
    observed_at: datetime
    refresh_due: str | None
    receipt: dict


def reusable_capture(writer, record, spec, now, *, refresh_expired=False):
    rows = writer.conn.execute(
        "SELECT c.*,t.state,t.provenance_json FROM deep_tasks t JOIN deep_captures c ON c.id=t.capture_id "
        "WHERE t.source_record_id=? AND c.source_record_id=t.source_record_id "
        "AND c.endpoint_kind=? AND t.listing_object_sha=? "
        "ORDER BY c.captured_at_utc DESC,c.id DESC",
        (record["id"], spec.endpoint_kind, record["current_record_object_sha"]),
    )
    for row in rows:
        metadata = capture_metadata(writer.conn, row["id"], row["latest_capture_seq"])
        parser_upgrade = metadata["parser_version"] < spec.parser_version
        if row["state"] not in ("FETCHED", "SOURCE_EMPTY") and not (row["state"] == "PARSE_ERROR" and parser_upgrade):
            continue
        if row["stale"] and not parser_upgrade:
            continue
        if row["lane"] == spec.lane and not parser_upgrade:
            continue
        try:
            due = datetime.fromisoformat(row["refresh_due_at_utc"]) if row["refresh_due_at_utc"] else None
            if refresh_expired and due is not None and due <= now:
                continue
            response = json.loads(writer.store.read(row["response_object_sha"]))
            detail = json.loads(writer.store.read(row["record_object_sha"]))
            old = json.loads(row["provenance_json"] or "{}")
            if identity_problem(detail, record, spec.lane, old.get("request", {}).get("id")):
                continue
            receipt = {k: old[k] for k in ("endpoint", "request", "http_status", "expected_notice", "expected_version", "source_record_id") if k in old}
            receipt.update(
                attempts=[], requests_used=0,
                original_attempts=old.get("original_attempts", old.get("attempts", [])),
                reused_from={"capture_id": row["id"], "capture_seq": row["latest_capture_seq"],
                             "lane": row["lane"], "observed_at": row["captured_at_utc"],
                             "response_object_sha": row["response_object_sha"]},
            )
            return ReusedCapture(DeepResponse(response, detail), datetime.fromisoformat(row["captured_at_utc"]), row["refresh_due_at_utc"], receipt)
        except (ValueError, TypeError, OSError, ObjectCorruptError):
            # A damaged/unreadable cache never authorizes projecting guessed content.
            continue
    return None
