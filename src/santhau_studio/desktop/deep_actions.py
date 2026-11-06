"""Validate explicit deep requests and coalesce repeated desktop submissions."""

import json
import os
import threading
import uuid
from contextlib import closing
from datetime import UTC, datetime

from santhau_studio.collectors.offline_acceptance import enabled
from santhau_studio.storage.rawstore import ObjectCorruptError, RawObjectStore
from santhau_studio.workers.jobs import JobError, JobRequest, enqueue_job

_SUBMISSION_LOCK = threading.Lock()


def _pending(root, record_id, lane, force):
    # Recheck active after queued: a worker may claim a queued document while we scan.
    for directory in ("active", "queued", "active"):
        for path in (root / "jobs" / directory).glob("*.json"):
            try:
                request = JobRequest.from_json(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            if (
                request.job_kind == lane
                and request.scope.get("record_ids") == [record_id]
                and (not force or request.scope.get("force_refresh") is True)
            ):
                return request.job_id
    return None


def enqueue(backend, payload):
    offline = enabled(backend.data_root) and os.environ.get(
        "SANTHAU_OFFLINE_ACCEPTANCE_ROOT"
    ) == str(backend.data_root.resolve())
    try:
        value = payload.get("record_id")
        if (
            type(value) is not int
            or value <= 0
            or type(payload.get("force_refresh", False)) is not bool
        ):
            raise ValueError("Mã hồ sơ hoặc lựa chọn làm mới không hợp lệ.")
        record_id, force = value, payload.get("force_refresh", False)
        lane = payload.get("lane", "DETAIL")
        if lane not in ("DETAIL", "HSMT", "REOFFER"):
            raise ValueError("Loại nội dung chưa được hỗ trợ.")
        blocker = backend._collection_blocker()
        if blocker:
            raise ValueError(blocker)
        detail = backend.record_detail(record_id)
        record = detail.get("record")
        if not record:
            raise ValueError("Không tìm thấy hồ sơ trong bản dữ liệu hiển thị.")
        family = record["family"]
        if not offline:
            with closing(RawObjectStore(backend.data_root, read_only=True)) as store:
                raw = json.loads(store.read(record["current_record_object_sha"]))
            if (
                record["source_key"] != "egp_home"
                or family != "TBMT"
                or not isinstance(raw, dict)
                or raw.get("bidForm") != "CGTTRG"
                or raw.get("notifyNo") != record["natural_key"]
                or raw.get("processApply") not in (None, "LDT")
            ):
                raise ValueError(
                    "Chỉ xác minh nguồn công khai cho TBMT chào giá trực tuyến rút gọn."
                )
            worker = backend.worker_status()
            if worker.get("running") and "public-cgttrg-deep-v1" not in worker.get(
                "capabilities", []
            ):
                raise ValueError("Cần cập nhật bộ thu thập trước khi lấy nội dung chi tiết.")
        rule = backend._verified_rule("egp_home", family)
        if not rule:
            raise ValueError("Chưa có quy tắc nguồn được xác minh cho hồ sơ này.")
        profile = "SYNTHETIC_OFFLINE" if offline else "PUBLIC_CGTTRG"
        with _SUBMISSION_LOCK:
            pending = _pending(backend.data_root, record_id, lane, force)
            if pending:
                return {"ok": True, "job_id": pending, "already_queued": True, "profile": profile}
            request = JobRequest(
                job_id="deep-" + uuid.uuid4().hex[:12],
                job_kind=lane,
                requested_by="gui",
                sources=({**rule, "family": family},),
                scope={"record_ids": [record_id], "force_refresh": force},
                request_limits={"max_requests": 5, "max_seconds": 120, "pacing_seconds": 3},
                created_at=datetime.now(UTC).isoformat(),
            )
            enqueue_job(backend.data_root, request)
        return {"ok": True, "job_id": request.job_id, "profile": profile}
    except (KeyError, ValueError, TypeError, JobError) as exc:
        return {"ok": False, "error_code": "DEEP_REQUEST_INVALID", "error": str(exc)}
    except (OSError, ObjectCorruptError):
        return {
            "ok": False,
            "error_code": "DEEP_REQUEST_FAILED",
            "error": "Không đọc được dữ liệu gốc hoặc ghi được hàng đợi. Hãy kiểm tra kho và quyền ghi rồi thử lại.",
        }
