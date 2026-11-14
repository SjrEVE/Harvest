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
            "scope": "TBMT/CGTTRG only; document references are not downloaded files",
        }
