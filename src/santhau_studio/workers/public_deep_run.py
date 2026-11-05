"""Checkpointed public deep dispatch; raw evidence survives parser failures."""

import json
from dataclasses import asdict

from santhau_studio.collectors.deep import DeepResponse, DetailShapeError, parse_detail
from santhau_studio.collectors.http_client import AccessBlocked, FetchFailed
from santhau_studio.collectors.network_policy import scrub_exception
from santhau_studio.collectors.public_deep import identity_problem
from santhau_studio.storage.deep import record_deep_capture
from santhau_studio.storage.public_deep import task_state


def collect_public(writer, run_id, lane, scope, fetcher, spec, records, clock, stop_requested):
    from santhau_studio.workers.deep_run import DeepBatchResult

    counts = {
        "lane": lane,
        "captured": 0,
        "skipped": 0,
        "failed": 0,
        "blocked": 0,
        "source_empty": 0,
    }
    conn = writer.conn
    for record in records:
        rid = record["id"]
        if stop_requested():
            return DeepBatchResult("PARTIAL", {**counts, "reason": "STOP_REQUESTED"})
        previous = conn.execute(
            "SELECT t.*,c.refresh_due_at_utc,c.stale FROM deep_tasks t LEFT JOIN deep_captures c ON c.id=t.capture_id WHERE t.source_record_id=? AND t.lane=?",
            (rid, lane),
        ).fetchone()
        if (
            not scope.get("force_refresh")
            and previous
            and previous["state"] in ("FETCHED", "SOURCE_EMPTY")
            and not previous["stale"]
            and previous["listing_object_sha"] == record["current_record_object_sha"]
            and previous["refresh_due_at_utc"]
            and previous["refresh_due_at_utc"] > clock().isoformat().replace("+00:00", "Z")
        ):
            counts["skipped"] += 1
            counts["after_record_id"] = rid
            continue
        try:
            fetched = (
                fetcher.fetch_record(record)
                if hasattr(fetcher, "fetch_record")
                else fetcher(record["family"], record["natural_key"])
            )
        except (AccessBlocked, FetchFailed, DetailShapeError, ValueError, KeyError, OSError) as exc:
            blocked = isinstance(exc, (AccessBlocked, DetailShapeError))
            counts["blocked" if blocked else "failed"] += 1
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
            )
            counts["last_error"] = scrub_exception(exc)
            if isinstance(exc, (AccessBlocked, FetchFailed)) or stop_requested():
                break  # Stop this endpoint on an access refusal or exhausted budget.
            continue
        payload = fetched.response_payload if isinstance(fetched, DeepResponse) else fetched
        detail = fetched.detail if isinstance(fetched, DeepResponse) else fetched
        receipt = dict(getattr(fetcher, "receipt", {}))
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
            )
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
            now=clock(),
        )
        state = "PARSE_ERROR" if problems or capture.stale else "FETCHED"
        if parsed:
            amounts = {
                "lots": len(parsed.lots),
                "goods": sum(len(lot.goods) for lot in parsed.lots) + len(parsed.unallotted_goods),
                "offerors": len(parsed.members),
                "document_references": len(parsed.documents),
            }
            receipt["projected"] = amounts
            if not problems and not capture.stale and not any(amounts.values()):
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
            error=json.dumps(problems) if problems else capture.stale_reason,
            provenance=receipt,
        )
        counts["captured"] += 1
        counts["failed"] += int(state == "PARSE_ERROR")
        counts["after_record_id"] = rid
    counts["requests_used"] = getattr(getattr(fetcher, "budget", None), "used", 0)
    status = "COMPLETED"
    if counts["failed"] or counts["blocked"]:
        status = (
            "PARTIAL"
            if counts["captured"] or counts["skipped"]
            else ("BLOCKED" if counts["blocked"] else "FAILED")
        )
    return DeepBatchResult(status, counts)
