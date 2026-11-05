"""Explicit, locked maintenance to consolidate unfinished collection and enable deep followup."""

import json
import uuid
from contextlib import closing
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from santhau_studio.collectors.source_registry import FAMILY_ORDER, query_contract_matches
from santhau_studio.domain.coverage import vn_calendar_day
from santhau_studio.storage.warehouse import WarehouseWriter
from santhau_studio.workers.auto_deep import configure, validated_configuration
from santhau_studio.workers.jobs import JobRequest, enqueue_job


def configure_collection(root, payload):
    """No raw/proof changes. Archive immutable superseded queue documents, never delete."""
    validated_configuration({"enabled": True, "batch_requests": payload.get("batch_requests", 30)})
    first = date.fromisoformat(payload["date_from"])
    last = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
    if first > last:
        raise ValueError("date_from is in the future")
    ids = payload.get("replace_job_ids", [])
    if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
        raise ValueError("replace_job_ids must be explicit ids")
    with closing(
        WarehouseWriter.open(root, create_schema=True, role="collection-configuration")
    ) as writer:
        sources = []
        for family in FAMILY_ORDER:
            rows = writer.conn.execute(
                "SELECT * FROM filter_boundary_rules WHERE source_key='egp_home' AND family=? AND canary_verified=1 ORDER BY rule_version DESC,verified_at_utc DESC",
                (family,),
            ).fetchall()
            rule = next(
                (r for r in rows if query_contract_matches(family, r["conversion_params"])), None
            )
            if rule is None:
                raise ValueError("Verified rule missing: " + family)
            sources.append(
                {k: rule[k] for k in ("source_key", "family", "endpoint", "adapter_sha")}
            )
        selected = []
        for state in ("active", "queued"):
            for path in (root / "jobs" / state).glob("*.json"):
                request = JobRequest.from_json(path.read_text(encoding="utf-8"))
                if request.job_id not in ids:
                    continue
                if (
                    request.job_kind != "BACKFILL"
                    or request.scope.get("cadence") != "WEEK"
                    or {s.get("family") for s in request.sources} != set(FAMILY_ORDER)
                ):
                    raise ValueError("Refusing to supersede unrelated task: " + request.job_id)
                if (
                    request.interval().from_utc < vn_calendar_day(first).from_utc
                    or request.interval().to_utc > vn_calendar_day(last).to_utc
                ):
                    raise ValueError("New scope must contain old scope")
                if state == "active":
                    row = writer.conn.execute(
                        "SELECT run_id,lifecycle FROM runs WHERE job_id=? ORDER BY rowid DESC LIMIT 1",
                        (request.job_id,),
                    ).fetchone()
                    if row and row["lifecycle"] not in ("PAUSED", "TERMINATED", "FINISHED"):
                        raise ValueError("Task must pause cleanly first")
                selected.append((path, request))
        if set(ids) != {request.job_id for _, request in selected}:
            raise ValueError("Expected unfinished tasks changed; refusing to modify queue")
        if any((root / "jobs" / "superseded" / path.name).exists() for path, _ in selected):
            raise ValueError("Superseded destination already exists")
        replacement = JobRequest(
            "full-update-" + uuid.uuid4().hex[:12],
            "BACKFILL",
            "owner-full-update",
            tuple(sources),
            {
                "cadence": "WEEK",
                "from": vn_calendar_day(first).from_utc.isoformat(),
                "to": vn_calendar_day(last).to_utc.isoformat(),
            },
            request_limits={
                "max_pages": 1000,
                "max_requests": 3000,
                "max_seconds": 3600,
                "pacing_seconds": 3,
            },
            created_at=datetime.now(UTC).isoformat(),
        )
        # Publish replacement first, so a crash cannot leave an empty queue.
        queued = enqueue_job(root, replacement)
        archived = []
        folder = root / "jobs" / "superseded"
        folder.mkdir(parents=True, exist_ok=True)
        for path, request in selected:
            destination = folder / path.name
            if destination.exists():
                raise ValueError("Superseded destination already exists")
            path.replace(destination)
            for row in writer.conn.execute(
                "SELECT run_id FROM runs WHERE job_id=? AND lifecycle='PAUSED'", (request.job_id,)
            ).fetchall():
                writer.finish_run(row["run_id"], "INTERRUPTED")
            archived.append(request.job_id)
        auto = configure(
            writer.conn, {"enabled": True, "batch_requests": payload.get("batch_requests", 30)}
        )
        result = {
            "ok": True,
            "job_id": replacement.job_id,
            "queued": queued.name,
            "date_from": str(first),
            "date_to": str(last),
            "superseded": archived,
            "automatic_deep": auto,
            "coverage_preserved": True,
        }
        writer.conn.execute(
            "INSERT OR REPLACE INTO settings(key,value_json,updated_at_utc) VALUES('last_collection_setup',?,?)",
            (json.dumps(result), datetime.now(UTC).isoformat().replace("+00:00", "Z")),
        )
        return result
