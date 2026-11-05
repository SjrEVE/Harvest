"""Explicit, writer-locked deep configuration and read-only operational status."""

from contextlib import closing

from santhau_studio.storage.warehouse import WarehouseWriter, connect_read_only
from santhau_studio.workers.auto_deep import configuration, configure, validated_configuration


def configure_deep(root, *, enabled, batch_requests=30):
    value = validated_configuration({"enabled": enabled, "batch_requests": batch_requests})
    if not (root / "warehouse.sqlite").is_file():
        raise ValueError("Chạy init trước khi cấu hình thu thập chi tiết.")
    with closing(
        WarehouseWriter.open(root, create_schema=True, role="deep-configuration")
    ) as writer:
        return {"ok": True, "configuration": configure(writer.conn, value), "network_requests": 0}


def deep_status(root):
    with closing(connect_read_only(root / "warehouse.sqlite")) as conn:
        present = bool(
            conn.execute("SELECT 1 FROM sqlite_master WHERE name='deep_tasks'").fetchone()
        )
        return {
            "ok": True,
            "configuration": configuration(conn),
            "checkpoint_schema_available": present,
            "states": dict(conn.execute("SELECT state,COUNT(*) FROM deep_tasks GROUP BY state"))
            if present
            else {},
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
