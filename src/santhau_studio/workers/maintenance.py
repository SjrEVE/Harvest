"""Local maintenance on the collector process; never blocks the Qt event loop."""

from __future__ import annotations

import csv
import json
import sqlite3
import sys
import uuid
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from santhau_studio.platform_win import scheduler
from santhau_studio.storage.backup import create_backup, restore_backup
from santhau_studio.storage.serving import build_serving_db
from santhau_studio.storage.warehouse import WarehouseWriter, connect_read_only

ACTIONS = ("backup", "restore", "verify", "export", "import-legacy", "inspect-legacy", "schedule-enable", "schedule-disable", "schedule-status")


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def perform(action: str, root: Path, source: str | None = None) -> dict:
    root = Path(root)
    try:
        if action in ("import-legacy", "inspect-legacy"):
            from santhau_studio.storage.import_legacy import import_legacy, inspect_legacy

            if not source:
                return {"ok": False, "error": "Chọn thư mục kho harvester hoặc export bundle."}
            if action == "inspect-legacy":
                return {"ok": True, **inspect_legacy(Path(source))}
            result = import_legacy(Path(source), root)
            published = build_serving_db(root / "warehouse.sqlite", root / "serving.sqlite")
            return {"ok": result["status"] in ("IMPORTED", "ALREADY_IMPORTED") and published.published,
                    **result, "snapshot_published": published.published}
        if action == "backup":
            result = create_backup(root)
            return {"ok": True, "path": str(result.path), "objects": result.objects}
        if action == "restore":
            if not source:
                return {"ok": False, "error": "Chọn thư mục backup cần khôi phục."}
            result = restore_backup(Path(source), root)
            if not result.restored:
                return {"ok": False, "error": result.reason}
            published = build_serving_db(root / "warehouse.sqlite", root / "serving.sqlite")
            return {"ok": published.published, "objects": result.objects_verified,
                    "error": published.reason, "path": str(root)}
        if action == "verify":
            if not (root / "warehouse.sqlite").exists():
                return {"ok": False, "error": "Chưa có warehouse để kiểm tra."}
            with closing(WarehouseWriter.open(root, role="verify")) as writer:
                result = writer.store.recover(writer.conn)
                integrity = writer.conn.execute("PRAGMA integrity_check").fetchone()[0]
                foreign_keys = len(writer.conn.execute("PRAGMA foreign_key_check").fetchall())
                publish = build_serving_db(root / "warehouse.sqlite", root / "serving.sqlite")
            return {"ok": result.clean and integrity == "ok" and foreign_keys == 0 and publish.published,
                    "recovery": asdict(result), "integrity": integrity, "foreign_key_errors": foreign_keys}
        if action == "export":
            return export_records(root)
        if action == "schedule-enable":
            executable = Path(sys.executable).parent / "SanThauCollector.exe"
            if not executable.is_file():
                return {"ok": False, "error": "Lập lịch cần bản ứng dụng đã đóng gói."}
            result = scheduler.create(root, executable)
            return {"ok": True, "task": result.task_name, "times": result.times}
        if action == "schedule-disable":
            return {"ok": True, "deleted": scheduler.delete(root)}
        if action == "schedule-status":
            return {"ok": True, **scheduler.query(root)}
        return {"ok": False, "error": f"Unsupported action: {action}"}
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def export_records(root: Path) -> dict:
    """Stream the published snapshot, retaining source identity and unknown values."""
    directory = root / "exports"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"records-{uuid.uuid4().hex[:12]}.csv"
    partial = target.with_suffix(".part")
    count = 0
    try:
        with closing(connect_read_only(root / "serving.sqlite")) as conn, partial.open(
            "x", encoding="utf-8-sig", newline=""
        ) as handle:
            output = csv.writer(handle)
            output.writerow(["source_key", "family", "natural_key", "version_state", "version_raw", "title", "normalized_json"])
            for row in conn.execute(
                "SELECT r.source_key, r.family, r.natural_key, r.version_state, r.version_raw, v.normalized_json"
                " FROM source_records r JOIN source_record_versions v ON v.source_record_id=r.id"
                " AND v.content_version_seq=r.current_content_version ORDER BY r.id"
            ):
                normalized = json.loads(row[5])
                output.writerow([*(_cell(value) for value in row[:5]), _cell(normalized.get("title")), _cell(row[5])])
                count += 1
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return {"ok": True, "path": str(target), "records": count}
