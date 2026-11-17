"""Explicit, writer-locked deep configuration and read-only operational status."""

import json
from contextlib import closing

from santhau_studio.storage.deep_metadata import package_counts, state_counts
from santhau_studio.storage.deep_metadata import present as has_metadata
from santhau_studio.storage.deep_retry import cleared_retry_state
from santhau_studio.storage.warehouse import WarehouseWriter, connect_read_only
from santhau_studio.workers.auto_deep import (
    LANES,
    configuration,
    configure,
    validated_configuration,
)


def configure_deep(root, *, enabled, batch_requests=30):
    value = validated_configuration({"enabled": enabled, "batch_requests": batch_requests})
    if not (root / "warehouse.sqlite").is_file():
        raise ValueError("Chạy init trước khi cấu hình thu thập chi tiết.")
    with closing(
        WarehouseWriter.open(root, create_schema=True, role="deep-configuration")
    ) as writer:
        return {"ok": True, "configuration": configure(writer.conn, value), "network_requests": 0}


def reset_deep_retry(root, *, record_id, lane):
    """Manually release one deep task from its retry backoff / quarantine.

    The next auto-deep scan may retry it immediately: the retry counter is
    cleared and ``updated_at_utc`` is rewound past the cooldown, so the
    release is not silently undone by the 60-minute first step. This is the
    explicit manual command that re-enables a quarantined task; it touches no
    capture, listing or parser state, and it spends no request. Stop the
    worker first, as with deep-config.
    """
    if type(record_id) is not int or record_id <= 0:
        raise ValueError("record_id must be a positive integer")
    if lane not in LANES:
        raise ValueError(f"lane must be one of {list(LANES)}")
    if not (root / "warehouse.sqlite").is_file():
        raise ValueError("Chạy init trước khi cấu hình thu thập chi tiết.")
    with closing(
        WarehouseWriter.open(root, create_schema=True, role="deep-retry-reset")
    ) as writer:
        row = writer.conn.execute(
            "SELECT provenance_json FROM deep_tasks WHERE source_record_id=? AND lane=?",
            (record_id, lane),
        ).fetchone()
        if row is None:
            return {
                "ok": False,
                "reason": "NO_SUCH_DEEP_TASK",
                "record_id": record_id,
                "lane": lane,
                "network_requests": 0,
            }
        provenance = json.loads(row[0] or "{}")
        previous = provenance.get("deep_retry") or {}
        provenance["deep_retry"] = cleared_retry_state()
        writer.conn.execute(
            "UPDATE deep_tasks SET provenance_json=?, updated_at_utc='2000-01-01T00:00:00Z'"
            " WHERE source_record_id=? AND lane=?",
            (json.dumps(provenance, ensure_ascii=False, sort_keys=True), record_id, lane),
        )
        return {
            "ok": True,
            "record_id": record_id,
            "lane": lane,
            "was_quarantined": bool(previous.get("quarantined")),
            "previous_consecutive_failures": int(previous.get("consecutive_failures") or 0),
            "network_requests": 0,
        }


# (lane, family, extra WHERE fragment) — which records a sweep lane may
# enqueue. Mirrors auto_deep.candidates' applicability: M1 needs an
# inputResultId; REOFFER only runs on CGTTRG packages; DETAIL/HSMT run on
# every record of their families (the fetcher routes each record to its
# endpoint by bidForm). The missing-capture condition is the auto queue's:
# no FETCHED/SOURCE_EMPTY task against the current listing.
_SWEEP_TARGETS = {
    "M1": (("KQLCNT", " AND json_extract(v.normalized_json,'$.inputResultId') IS NOT NULL"),),
    "DETAIL": (
        ("TBMT", ""),
        ("YCBG", ""),
        ("KHLCNT", ""),
        ("DU_AN", ""),
        ("HOP_DONG", ""),
        ("ESHOP", ""),
    ),
    "HSMT": (("TBMT", ""),),
    "REOFFER": (("TBMT", " AND json_extract(v.normalized_json,'$.bidForm')='CGTTRG'"),),
}
_SWEEP_LANES = ("M1", "DETAIL", "HSMT", "REOFFER")


def sweep_deep(root, *, lane="M1", limit=100, execute=False):
    """One-off deep sweep: enqueue batched jobs for records the lane has not
    yet captured (no FETCHED/SOURCE_EMPTY task against the current listing).

    Default is a dry-run preview — nothing is enqueued and no request is
    spent. With --execute the jobs are queued (at most 10 records per job, the
    fetcher's 30-request budget leaves retry headroom) and the worker picks
    them up; a later sweep only sees what is still missing, because
    collect_public skips fresh FETCHED/SOURCE_EMPTY captures itself. Stop the
    worker before enqueueing, as with deep-config.

    lane="ALL" sweeps every lane at once (RC3-12: one version scraping
    everything): one job per applicable (family, lane) pair, because a deep
    job dispatches a single family. limit applies per (lane, family) pair.
    """
    if lane not in (*_SWEEP_LANES, "ALL"):
        raise ValueError(f"lane must be one of {[*list(_SWEEP_LANES), 'ALL']}")
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    if not (root / "warehouse.sqlite").is_file():
        raise ValueError("Chạy init trước khi quét thu thập chi tiết.")
    from santhau_studio.collectors.cgttrg import PARSER_VERSION
    from santhau_studio.workers.auto_deep import MAX_RECORDS_PER_DEEP_JOB
    from santhau_studio.workers.jobs import JobRequest, enqueue_job

    lanes = _SWEEP_LANES if lane == "ALL" else (lane,)
    pairs = []
    with closing(connect_read_only(root / "warehouse.sqlite")) as conn:
        for target_lane in lanes:
            for family, extra in _SWEEP_TARGETS[target_lane]:
                rows = conn.execute(
                    "SELECT r.id FROM source_records r"
                    " JOIN source_record_versions v ON v.source_record_id=r.id AND v.content_version_seq=r.current_content_version"
                    " LEFT JOIN deep_tasks t ON t.source_record_id=r.id AND t.lane=?"
                    " WHERE r.source_key='egp_home' AND r.family=?" + extra +
                    " AND (t.source_record_id IS NULL"
                    " OR NOT (t.state IN ('FETCHED','SOURCE_EMPTY') AND t.listing_object_sha=r.current_record_object_sha))"
                    " ORDER BY r.id LIMIT ?",
                    (target_lane, family, limit),
                ).fetchall()
                pairs.append((target_lane, family, [int(r[0]) for r in rows]))
    job_ids = []
    if execute:
        import uuid
        from datetime import UTC, datetime

        for target_lane, family, record_ids in pairs:
            for start in range(0, len(record_ids), MAX_RECORDS_PER_DEEP_JOB):
                chunk = record_ids[start:start + MAX_RECORDS_PER_DEEP_JOB]
                request = JobRequest(
                    job_id="deep-sweep-" + uuid.uuid4().hex[:16],
                    job_kind=target_lane,
                    requested_by="deep-sweep",
                    sources=({"source_key": "egp_home", "family": family},),
                    scope={"record_ids": chunk},
                    request_limits={
                        "max_requests": min(2 * len(chunk), 30),
                        "max_attempts": 4,
                        "max_seconds": min(900, 120 + 30 * len(chunk)),
                        "pacing_seconds": 3,
                    },
                    created_at=datetime.now(UTC).isoformat(),
                )
                enqueue_job(root, request)
                job_ids.append(request.job_id)
    all_ids = [rid for _lane, _family, ids in pairs for rid in ids]
    breakdown = [
        {
            "lane": target_lane,
            "family": family,
            "records_matched": len(record_ids),
            "jobs": (len(record_ids) + MAX_RECORDS_PER_DEEP_JOB - 1) // MAX_RECORDS_PER_DEEP_JOB,
            "first_record_ids": record_ids[:10],
        }
        for target_lane, family, record_ids in pairs
    ]
    return {
        "ok": True,
        "lane": lane,
        "parser_version": PARSER_VERSION,
        "records_matched": len(all_ids),
        "jobs": sum(entry["jobs"] for entry in breakdown),
        "records_per_job": MAX_RECORDS_PER_DEEP_JOB,
        "executed": execute,
        "job_ids": job_ids,
        "first_record_ids": all_ids[:10],
        "lanes": breakdown,
        "network_requests": 0,
    }


def deep_status(root):
    with closing(connect_read_only(root / "warehouse.sqlite")) as conn:
        present = bool(
            conn.execute("SELECT 1 FROM sqlite_master WHERE name='deep_tasks'").fetchone()
        )
        quarantined = (
            dict(
                zip(
                    ("packages", "lane_tasks"),
                    conn.execute(
                        "SELECT COUNT(DISTINCT source_record_id),COUNT(*) FROM deep_tasks"
                        " WHERE CAST(json_extract(provenance_json,'$.deep_retry.quarantined') AS INTEGER)=1"
                    ).fetchone(),
                    strict=True,
                )
            )
            if present
            else {"packages": 0, "lane_tasks": 0}
        )
        return {
            "ok": True,
            "configuration": configuration(conn),
            "checkpoint_schema_available": present,
            "states": state_counts(conn),
            "packages_by_state": package_counts(conn),
            "quarantined": quarantined,
            "capabilities": dict(conn.execute("SELECT m.capability,COUNT(*) FROM deep_task_metadata m JOIN deep_tasks t "
                "ON t.source_record_id=m.source_record_id AND t.lane=m.lane AND t.updated_at_utc=m.task_updated_at_utc GROUP BY m.capability")) if has_metadata(conn,"deep_task_metadata") else {},
            "unsupported": dict(zip(("packages","lane_tasks"), conn.execute("SELECT COUNT(DISTINCT m.source_record_id),COUNT(*) FROM deep_task_metadata m JOIN deep_tasks t "
                "ON t.source_record_id=m.source_record_id AND t.lane=m.lane AND t.updated_at_utc=m.task_updated_at_utc WHERE m.capability LIKE 'UNSUPPORTED%'").fetchone(), strict=True)) if has_metadata(conn,"deep_task_metadata") else {"packages":0,"lane_tasks":0},
            "recent_errors": [
                dict(r)
                for r in conn.execute(
                    "SELECT r.natural_key,t.lane,t.state,t.error,t.updated_at_utc FROM deep_tasks t "
                    "JOIN source_records r ON r.id=t.source_record_id WHERE t.error IS NOT NULL "
                    "ORDER BY t.updated_at_utc DESC LIMIT 10"
                )
            ]
            if present
            else [],
            "scope": "TBMT + KQLCNT/M1 + YCBG/KHLCNT/DU_AN/HOP_DONG/ESHOP detail; document references are not downloaded files",
        }
