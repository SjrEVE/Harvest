"""Explicit isolated offline acceptance. Never installed as an active source profile."""

import hashlib
import json
import time
from contextlib import closing
from pathlib import Path

from santhau_studio.collectors.egp_home import Page
from santhau_studio.collectors.source_registry import FAMILY_ORDER, SOURCES
from santhau_studio.storage.warehouse import WarehouseWriter, connect_read_only

PROFILE = "RC3_SYNTHETIC_OFFLINE"
ADAPTER = hashlib.sha256(b"linux-offline-acceptance").hexdigest()


def initialize_fixture(root):
    root = Path(root).resolve()
    if (root / "warehouse.sqlite").exists():
        raise ValueError("Chạy thử cần thư mục dữ liệu mới.")
    root.mkdir(parents=True, exist_ok=True)
    (root / "linux-offline.json").write_text(
        json.dumps({"root": str(root), "class": PROFILE}), encoding="utf-8"
    )
    with closing(WarehouseWriter.open(root, create_schema=True)) as writer:
        writer.conn.execute(
            "INSERT INTO source_registry(source_key,display_name,endpoint_family,access_state) VALUES('egp_home','OFFLINE TEST','egp_home','ALLOWED_AND_VERIFIED')"
        )
        writer.conn.execute(
            "INSERT INTO settings VALUES('evidence_class',?,'2026-01-01T00:00:00Z')",
            (json.dumps({"class": PROFILE, "live_proof": False}),),
        )
        for family in FAMILY_ORDER:
            writer.conn.execute(
                "INSERT INTO filter_boundary_rules(rule_key,rule_version,source_key,family,endpoint,adapter_sha,boundary_kind,precision,source_timezone,offset_minutes,conversion_params,canary_verified,verified_at_utc) VALUES(?,1,'egp_home',?,?,?,'CLOSED_LOCAL','DAY','Asia/Ho_Chi_Minh',420,?,1,'2026-01-01T00:00:00Z')",
                (
                    "SYNTHETIC-LINUX-" + family,
                    family,
                    SOURCES["egp_home"].endpoint,
                    ADAPTER,
                    json.dumps(
                        {
                            "query_contract": "KQLCNT_RESULT_DATE_V2",
                            "date_field": "publicDateKqlcnt",
                        }
                    ),
                ),
            )
        writer.conn.commit()


def enabled(root):
    try:
        value = json.loads((Path(root) / "linux-offline.json").read_text(encoding="utf-8"))
        with closing(connect_read_only(Path(root) / "warehouse.sqlite")) as conn:
            row = conn.execute(
                "SELECT value_json FROM settings WHERE key='evidence_class'"
            ).fetchone()
        return (
            value == {"root": str(Path(root).resolve()), "class": PROFILE}
            and json.loads(row[0])["class"] == PROFILE
        )
    except (OSError, ValueError, TypeError, KeyError):
        return False


def builder(_writer, request):
    family = request.sources[0]["family"]
    key, version = {
        "TBMT": ("notifyNo", "notifyVersion"),
        "KQLCNT": ("notifyNo", "notifyVersion"),
        "HOP_DONG": ("contractCode", "contractVersion"),
        "KHLCNT": ("planNo", "planVersion"),
        "DU_AN": ("pno", "pversion"),
        "YCBG": ("notifyNo", "notifyVersion"),
    }[family]

    def pages(interval, _granularity, _window):
        for page in range(4):
            time.sleep(0.025)
            rows = [
                {
                    key: f"SYNTH-{family}-{interval.from_utc:%Y%m%d}-{page}-{i}",
                    version: "00",
                    "publicDate": interval.from_utc.isoformat(),
                    "publicDateKqlcnt": interval.from_utc.isoformat(),
                    "bidName": "Dữ liệu thử ngoại tuyến",
                    "pname": "Dự án thử",
                    "ctName": "Hợp đồng thử",
                }
                for i in range(2)
            ]
            yield Page(
                payload={"page": {"content": rows, "totalElements": 8}},
                page_no=page,
                rows=2,
                has_more=page < 3,
                total_hint=8,
                total_hint_trusted=True,
                http_status=200,
                attempt_log=(),
            )

    return pages
