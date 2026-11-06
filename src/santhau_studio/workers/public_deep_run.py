"""Checkpointed public deep dispatch; raw evidence survives parser failures."""

import json
from dataclasses import asdict

from santhau_studio.collectors.deep import DeepResponse, DetailShapeError, parse_detail
from santhau_studio.collectors.http_client import AccessBlocked, FetchFailed
from santhau_studio.collectors.network_policy import scrub_exception
from santhau_studio.collectors.public_deep import (
    ADAPTER_VERSION,
    UnsupportedAdapter,
    identity_problem,
)
from santhau_studio.storage.deep import record_deep_capture
from santhau_studio.storage.deep_metadata import capture_metadata, write_task
from santhau_studio.storage.public_deep import task_state
from santhau_studio.workers.deep_reuse import reusable_capture


def collect_public(writer, run_id, lane, scope, fetcher, spec, records, clock, stop_requested):
    from santhau_studio.workers.deep_run import DeepBatchResult

    counts = {
        "lane": lane,
        "captured": 0,
        "skipped": 0,
        "failed": 0,
        "blocked": 0,
        "source_empty": 0,
        "reused": 0,
        "unsupported": 0,
    }
    conn = writer.conn
    local_fetches = 0
    for record in records:
        rid = record["id"]
        if stop_requested():
            return DeepBatchResult("PARTIAL", {**counts, "reason": "STOP_REQUESTED"})
        previous = conn.execute(
            "SELECT t.*,c.id AS existing_capture,c.latest_capture_seq,c.refresh_due_at_utc,c.stale,"
            "(julianday(c.refresh_due_at_utc)>julianday(?)) AS fresh "
            "FROM deep_tasks t LEFT JOIN deep_captures c ON c.id=t.capture_id WHERE t.source_record_id=? AND t.lane=?",
            (clock().isoformat(), rid, lane),
        ).fetchone()
        if (
            not scope.get("force_refresh")
            and previous
            and previous["existing_capture"]
            and capture_metadata(conn, previous["capture_id"], previous["latest_capture_seq"])["parser_version"] >= spec.parser_version
            and previous["state"] in ("FETCHED", "SOURCE_EMPTY")
            and not previous["stale"]
            and previous["listing_object_sha"] == record["current_record_object_sha"]
            and (not scope.get("refresh_expired") or previous["fresh"])
        ):
            counts["skipped"] += 1
            counts["after_record_id"] = rid
            continue
        try:
            reused = None if scope.get("force_refresh") else reusable_capture(writer, record, spec, clock(), refresh_expired=bool(scope.get("refresh_expired")))
            if reused:
                fetched = reused.response
                counts["reused"] += 1
            else:
                local_fetches += 1
                fetched = fetcher.fetch_record(record) if hasattr(fetcher, "fetch_record") else fetcher(record["family"], record["natural_key"])
        except (AccessBlocked, FetchFailed, DetailShapeError, ValueError, KeyError, OSError) as exc:
            if stop_requested() and "STOP_REQUESTED" in str(exc):
                return DeepBatchResult("PARTIAL", {**counts, "reason": "STOP_REQUESTED",
                    "requests_used": getattr(getattr(fetcher,"budget",None),"used",0),
                    "interrupted_attempts": [asdict(a) for a in getattr(exc,"attempt_log",())]})
            blocked = isinstance(exc, (AccessBlocked, DetailShapeError))
            unsupported = isinstance(exc, UnsupportedAdapter)
            counts["unsupported" if unsupported else ("blocked" if blocked else "failed")] += 1
            receipt = {
                **getattr(fetcher, "receipt", {}),
                "attempts": [asdict(a) for a in getattr(exc, "attempt_log", ())],
            }
            task_state(
                conn,
                rid,
                lane,
                "BLOCKED" if blocked else "FAILED",
                error=scrub_exception(exc),
                provenance=receipt,
                listing_sha=record["current_record_object_sha"],
            )
            write_task(conn, rid, lane, parser_version=spec.parser_version, adapter_version=ADAPTER_VERSION,
                       capability=exc.capability if unsupported else ("ACCESS_DENIED" if isinstance(exc, AccessBlocked) else "UNKNOWN"))
            counts["last_error"] = scrub_exception(exc)
            if isinstance(exc, (AccessBlocked, FetchFailed)) or stop_requested():
                break  # Stop this endpoint on an access refusal or exhausted budget.
            continue
        payload = fetched.response_payload if isinstance(fetched, DeepResponse) else fetched
        detail = fetched.detail if isinstance(fetched, DeepResponse) else fetched
        receipt = dict(reused.receipt if reused else getattr(fetcher, "receipt", {}))
        # Store and register the complete response before checking its shape/identity.
        ref = writer.store_response(payload)
        writer.register_object(ref)
        receipt["raw_object_sha"] = ref.sha256
        issue = identity_problem(detail, record, lane, receipt.get("request", {}).get("id"))
        if issue:
            invalid = record_deep_capture(
                writer,
                run_id=run_id,
                source_record_id=rid,
                spec=spec,
                completeness="PARTIAL",
                response_payload=payload,
                detail=detail,
                now=clock(),
                parse_error=issue,
            )
            task_state(
                conn,
                rid,
                lane,
                "PARSE_ERROR",
                response_sha=ref.sha256,
                capture_id=invalid.capture_id,
                error=issue,
                provenance=receipt,
                listing_sha=record["current_record_object_sha"],
            )
            write_task(conn, rid, lane, parser_version=spec.parser_version, adapter_version=ADAPTER_VERSION,
                       capability="IDENTITY_QUARANTINE")
            counts["failed"] += 1
            continue
        problems = []
        parsed = None
        try:
            parsed = parse_detail(spec, detail)
            problems = list(parsed.problems)
        except (DetailShapeError, ValueError, TypeError) as exc:
            problems = [str(exc)]
        capture = record_deep_capture(
            writer,
            run_id=run_id,
            source_record_id=rid,
            spec=spec,
            completeness="PARTIAL" if problems else "FULL",
            response_payload=payload,
            detail=detail,
            now=reused.observed_at if reused else clock(),
        )
        state = "PARSE_ERROR" if problems or capture.stale else "FETCHED"
        if reused:
            conn.execute("UPDATE deep_captures SET refresh_due_at_utc=? WHERE id=?", (reused.refresh_due, capture.capture_id))
        if parsed:
            amounts = {
                "lots": len(parsed.lots),
                "goods": sum(len(lot.goods) for lot in parsed.lots) + len(parsed.unallotted_goods),
                "offerors": len(parsed.members),
                "document_references": len(parsed.documents),
            }
            receipt["parsed"] = amounts
            receipt["projected"] = amounts if capture.projection_applied else dict.fromkeys(amounts, 0)
            receipt["projection_applied"] = capture.projection_applied
            receipt["capability"] = parsed.capability
            receipt["warnings"] = list(parsed.warnings)
            if not problems and not capture.stale and not any(amounts.values()) and parsed.capability not in ("DOCUMENT_ONLY", "STRUCTURED_ITEMS_UNAVAILABLE", "UNVERIFIED_FORM"):
                state = "SOURCE_EMPTY"
                counts["source_empty"] += 1
        task_state(
            conn,
            rid,
            lane,
            state,
            listing_sha=record["current_record_object_sha"],
            response_sha=ref.sha256,
            capture_id=capture.capture_id,
            error=json.dumps(problems or capture.problems) if problems or capture.problems else capture.stale_reason,
            provenance=receipt,
        )
        write_task(conn, rid, lane, parser_version=spec.parser_version, adapter_version=ADAPTER_VERSION,
                   capability=parsed.capability if parsed else "UNKNOWN", warnings=parsed.warnings if parsed else ())
        counts["captured"] += 1
        counts["failed"] += int(state == "PARSE_ERROR")
        counts["after_record_id"] = rid
    counts["requests_used"] = getattr(getattr(fetcher, "budget", None), "used", local_fetches)
    status = "COMPLETED"
    if counts["failed"] or counts["blocked"] or counts["unsupported"]:
        status = (
            "PARTIAL"
            if counts["captured"] or counts["skipped"]
            else ("BLOCKED" if counts["blocked"] or counts["unsupported"] else "FAILED")
        )
    return DeepBatchResult(status, counts)
