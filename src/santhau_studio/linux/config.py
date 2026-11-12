"""Source configuration and immutable requests, shared warehouse/proof contracts."""

import hashlib
import json
import re
import uuid
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from santhau_studio.collectors.source_registry import FAMILY_ORDER, SOURCES, query_contract_matches
from santhau_studio.domain.coverage import vn_calendar_day
from santhau_studio.storage.warehouse import WarehouseWriter, connect_read_only
from santhau_studio.workers.jobs import JobRequest

PROFILE_COLUMNS = (
    "rule_key",
    "rule_version",
    "source_key",
    "family",
    "endpoint",
    "adapter_sha",
    "boundary_kind",
    "precision",
    "source_timezone",
    "offset_minutes",
    "conversion_params",
    "canary_verified",
    "verified_at_utc",
)


def data_file(name):
    for parent in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
        for relative in (Path("config") / name, Path("packaging/linux/config") / name):
            candidate = parent / relative
            if candidate.is_file():
                return candidate
    raise ValueError(f"Không tìm thấy cấu hình đóng gói: {name}")


def adapter_code_sha():
    from santhau_studio.collectors import egp_home

    source = Path(egp_home.__file__)
    if source.is_file() and source.suffix == ".py":
        return hashlib.sha256(source.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    return json.loads(data_file("build-info.json").read_text(encoding="utf-8"))[
        "adapter_code_sha256"
    ]


def read_profile(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("schema") != 1
        or value.get("level") != "RULE_VERIFIED"
        or value.get("adapter_code_sha256") != adapter_code_sha()
    ):
        raise ValueError("Cấu hình nguồn chưa được xác minh hoặc không khớp mã thu thập.")
    rows = value.get("rules")
    if (
        not isinstance(rows, list)
        or len(rows) != 6
        or not all(isinstance(r, dict) for r in rows)
        or {r.get("family") for r in rows} != set(FAMILY_ORDER)
    ):
        raise ValueError("Cấu hình cần đủ sáu loại hồ sơ.")
    for r in rows:
        if (
            set(r) != set(PROFILE_COLUMNS)
            or r["source_key"] != "egp_home"
            or r["endpoint"] != SOURCES["egp_home"].endpoint
            or r["canary_verified"] != 1
            or r["precision"] != "MILLISECOND"
            or r["boundary_kind"] != "CLOSED_LOCAL"
            or r["source_timezone"] != "Asia/Ho_Chi_Minh"
            or r["offset_minutes"] != 420
            or not re.fullmatch(r"[a-f0-9]{64}", r["adapter_sha"])
            or not query_contract_matches(r["family"], r["conversion_params"])
        ):
            raise ValueError("Cấu hình nguồn không đúng hợp đồng đã xác minh.")
        params = json.loads(r["conversion_params"])
        if (
            params.get("date_field")
            != ("publicDateKqlcnt" if r["family"] == "KQLCNT" else "publicDate")
            or params.get("wire_format") != "ISO_LOCAL_T"
        ):
            raise ValueError("Trường ngày hoặc định dạng gửi nguồn không hợp lệ.")
        stamp = datetime.fromisoformat(r["verified_at_utc"])
        if stamp.tzinfo is None:
            raise ValueError("Thời điểm xác minh phải có múi giờ.")
    return rows


def initialize(root, profile=None):
    rows = read_profile(profile) if profile else []  # validate before taking a write lock
    with closing(WarehouseWriter.open(Path(root), create_schema=True, role="configure")) as writer:
        if rows:
            writer.conn.execute(
                "INSERT OR IGNORE INTO source_registry(source_key,display_name,endpoint_family,access_state,notes) VALUES('egp_home','E-GP công khai','egp_home','ALLOWED_AND_VERIFIED','Imported verified rule profile; no collection proof imported')"
            )
        for row in rows:
            writer.conn.execute(
                "INSERT OR IGNORE INTO filter_boundary_rules ("
                + ",".join(PROFILE_COLUMNS)
                + ") VALUES("
                + ",".join("?" for _ in PROFILE_COLUMNS)
                + ")",
                tuple(row[c] for c in PROFILE_COLUMNS),
            )
        writer.conn.commit()


def make_jobs(root, *, mode="BACKFILL", family="ALL", first=None, last=None, limits=None):
    if family not in (*FAMILY_ORDER, "ALL") or mode not in ("BACKFILL", "INCREMENTAL", "RECONCILE"):
        raise ValueError("Loại hoặc chế độ thu thập không hợp lệ.")
    if mode == "INCREMENTAL":
        if first or last:
            raise ValueError("Cập nhật tự động dùng mốc đã lưu, không dùng khoảng ngày.")
        now = datetime.now(UTC)
        scope = {"from": (now - timedelta(days=3)).isoformat(), "to": now.isoformat()}
    else:
        a, b = date.fromisoformat(first or ""), date.fromisoformat(last or "")
        from zoneinfo import ZoneInfo

        if a > b or b > datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date():
            raise ValueError("Khoảng ngày bị đảo ngược hoặc nằm trong tương lai.")
        scope = {
            "from": vn_calendar_day(a).from_utc.isoformat(),
            "to": vn_calendar_day(b).to_utc.isoformat(),
        }
    selected = FAMILY_ORDER if family == "ALL" else (family,)
    sources = []
    with closing(connect_read_only(Path(root) / "warehouse.sqlite")) as conn:
        state = conn.execute(
            "SELECT access_state FROM source_registry WHERE source_key='egp_home'"
        ).fetchone()
        if not state or state[0] != "ALLOWED_AND_VERIFIED":
            raise ValueError("Nguồn chưa được xác minh hoặc đang bị chặn.")
        for f in selected:
            rules = conn.execute(
                "SELECT * FROM filter_boundary_rules WHERE source_key='egp_home' AND family=? AND canary_verified=1 ORDER BY rule_version DESC,verified_at_utc DESC",
                (f,),
            ).fetchall()
            rule = next(
                (r for r in rules if query_contract_matches(f, r["conversion_params"])), None
            )
            if rule is None:
                raise ValueError(f"{f}: chưa có quy tắc ngày đã xác minh.")
            sources.append(
                {k: rule[k] for k in ("source_key", "family", "endpoint", "adapter_sha")}
            )
    stamp = datetime.now(UTC).isoformat()
    job_id = "linux-" + uuid.uuid4().hex[:16]
    request_limits = {
        "max_pages": 1000,
        "max_requests": 3000,
        "max_seconds": 3600,
        "pacing_seconds": 3,
        "connect_timeout_seconds": 15,
        "max_attempts": 4,
        **(limits or {}),
    }
    if mode == "BACKFILL" and family == "ALL":
        return [
            JobRequest(
                job_id=job_id,
                job_kind=mode,
                requested_by="linux-cli",
                sources=tuple(sources),
                scope={**scope, "cadence": "WEEK"},
                request_limits=request_limits,
                created_at=stamp,
            )
        ]
    return [
        JobRequest(
            job_id=job_id + "-" + s["family"],
            job_kind=mode,
            requested_by="linux-cli",
            sources=(s,),
            scope=scope,
            request_limits=request_limits,
            created_at=stamp,
        )
        for s in sources
    ]
