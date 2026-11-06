"""Explicit, writer-locked deep configuration and read-only operational status."""

from contextlib import closing

from santhau_studio.storage.deep_metadata import package_counts, state_counts
from santhau_studio.storage.deep_metadata import present as has_metadata
from santhau_studio.storage.warehouse import WarehouseWriter, connect_read_only
from santhau_studio.workers.auto_deep import configuration, configure, validated_configuration


def configure_deep(root, *, enabled, batch_requests=30, refresh_expired=None):
    if not (root / "warehouse.sqlite").is_file():
        raise ValueError("Chạy init trước khi cấu hình thu thập chi tiết.")
    with closing(
        WarehouseWriter.open(root, create_schema=True, role="deep-configuration")
    ) as writer:
        current = configuration(writer.conn)
        if refresh_expired is None:
            refresh_expired = current.get("refresh_expired", False) is True
        value = validated_configuration({
            "enabled": enabled,
            "batch_requests": batch_requests,
            "refresh_expired": refresh_expired,
        })
        saved = configure(writer.conn, value)
        from santhau_studio.storage.policy_notice import policy_notice

        return {
            "ok": True,
            "configuration": saved,
            "policy": policy_notice(writer.conn),
            "network_requests": 0,
        }


def deep_status(root):
    with closing(connect_read_only(root / "warehouse.sqlite")) as conn:
        present = bool(
            conn.execute("SELECT 1 FROM sqlite_master WHERE name='deep_tasks'").fetchone()
        )
        return {
            "ok": True,
            "configuration": configuration(conn),
            "checkpoint_schema_available": present,
            "states": state_counts(conn),
            "packages_by_state": package_counts(conn),
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
            "policy": _policy(conn),
            "parser_replay": _parser_replay(conn),
        }


def _policy(conn):
    from santhau_studio.storage.policy_notice import policy_notice

    return policy_notice(conn)


def _parser_replay(conn):
    from santhau_studio.collectors.cgttrg import PARSER_VERSION
    from santhau_studio.storage.deep_metadata import present as has_metadata

    behind = 0
    if has_metadata(conn, "deep_capture_parse_metadata"):
        behind = conn.execute(
            "SELECT COUNT(*) FROM deep_tasks t JOIN deep_captures c ON c.id=t.capture_id "
            "LEFT JOIN deep_capture_parse_metadata pm ON pm.deep_capture_id=c.id "
            "AND pm.capture_seq=c.latest_capture_seq WHERE t.response_object_sha IS NOT NULL "
            "AND COALESCE(pm.parser_version,1) < ?",
            (PARSER_VERSION,),
        ).fetchone()[0]
    return {
        "captures_behind_parser": behind,
        "automatic": False,
        "command": "replay-deep",
        "network_requests": 0,
        "note_vi": (
            "Nâng parser không tự đọc lại toàn bộ kho và không gọi mạng. "
            "Dùng replay-deep: mặc định chỉ xem trước, có --limit. "
            "Lịch sử capture chỉ thêm khi kết quả đọc thay đổi."
        ),
        "note_en": (
            "A parser upgrade does not replay the corpus and does not call the network. "
            "replay-deep is offline, dry-run by default, and bounded by --limit. "
            "Capture history grows only when the parsed projection changes."
        ),
    }
