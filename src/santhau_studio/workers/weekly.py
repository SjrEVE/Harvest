"""A resumable six-family weekly job, using existing queue and coverage proofs."""
import json
import uuid
from dataclasses import replace
from datetime import timedelta

from santhau_studio.collectors.source_registry import FAMILY_ORDER
from santhau_studio.domain.coverage import Interval
from santhau_studio.storage import coverage
from santhau_studio.storage.serving import mark_stale

CAPABILITY = "weekly-six-v1"


def weeks(span):
    """Seven consecutive VN days from the chosen start, oldest first, clipped at end."""
    start = span.from_utc
    while start < span.to_utc:
        end = min(start + timedelta(days=7), span.to_utc)
        yield Interval(start, end)
        start = end


def dispatch(loop, request, parent_run):
    sources = {s.get("family"): s for s in request.sources}
    if request.job_kind != "BACKFILL" or set(sources) != set(FAMILY_ORDER) or len(request.sources) != 6:
        return "BLOCKED", {"reason": "Thu thập theo tuần cần đủ sáu loại hồ sơ và chế độ thu thập theo khoảng ngày."}
    writer = loop._writer
    conn = writer.conn
    periods = list(weeks(request.interval()))
    completed = 0
    try:
        for index, span in enumerate(periods, 1):
            for family in FAMILY_ORDER:
                source = sources[family]
                def resolve(source=source, family=family, span=span):
                    return coverage.resolve(conn, source["source_key"], family, span.from_utc, span.to_utc)
                for attempt in range(1, 65):
                    if loop.flag.interrupt_requested:
                        return "PARTIAL", {"weeks_completed": completed, "reason": "Đã dừng theo yêu cầu; tiến độ đã lưu."}
                    before = resolve()
                    if before.can_skip:
                        break
                    loop._weekly_progress = {"week_index": index, "week_count": len(periods),
                        "week_from": span.from_utc.isoformat(), "week_to": span.to_utc.isoformat(),
                        "family_index": FAMILY_ORDER.index(family) + 1, "attempt": attempt}
                    loop.progress = {"job_id": request.job_id, "family": family, "rows": 0, "pages": 0,
                        **loop._weekly_progress}
                    child = replace(request, sources=(source,), scope={"from": span.from_utc.isoformat(), "to": span.to_utc.isoformat()})
                    child_run = "run-" + uuid.uuid4().hex
                    # Distinct runs preserve every retry's attempts/observations and leave the
                    # parent lifecycle as the sole user control target, including crash recovery.
                    child_id = f"{request.job_id}.w{index:03d}.{family}.{attempt}"
                    writer.register_run(child_run, child_id, "BACKFILL", requested_by=request.requested_by)
                    try:
                        mark_stale(loop.data_root / "serving.sqlite", "Collection in progress; showing the previous snapshot")
                        status, detail = loop._dispatch(child, child_run)
                    except Exception as exc:
                        status, detail = "FAILED", {"error": f"{type(exc).__name__}: {exc}"[:200]}
                    if loop.flag.interrupt_requested:
                        status = "INTERRUPTED"
                    writer.finish_run(child_run, status)
                    conn.execute("INSERT INTO run_tasks(run_id,task_kind,status,detail_json) VALUES(?,?,?,?)",
                        (child_run, "BACKFILL", status, json.dumps({**detail, "parent_run": parent_run}, ensure_ascii=False)))
                    conn.commit()
                    loop._publish_snapshot()
                    after = resolve()
                    if loop.flag.interrupt_requested:
                        return "PARTIAL", {"weeks_completed": completed, "reason": "Đã dừng theo yêu cầu; tiến độ đã lưu."}
                    if after.can_skip:
                        break
                    blockers = detail.get("blockers", [])
                    exhausted = any("BUDGET_EXHAUSTED" in b for b in blockers)
                    recoverable = all("BUDGET_EXHAUSTED" in b or b == "TOTAL_UNTRUSTED" for b in blockers)
                    # A time/request cap may continue only with new proven coverage. A source
                    # block, network fault or zero progress stops here, never skips the week.
                    if status != "PARTIAL" or not exhausted or not recoverable or after.proven_fraction <= before.proven_fraction:
                        reason = ("Chạm giới hạn nhưng chưa xác nhận thêm khoảng thời gian nào; chưa chuyển tuần. Tiến độ đã lưu, cần kiểm tra hoặc tăng giới hạn trước khi chạy tiếp."
                                  if exhausted and recoverable else "Nguồn bị chặn hoặc có lỗi chưa xử lý được; chưa chuyển tuần. Tiến độ đã lưu, chạy lại để tiếp tục phần thiếu.")
                        return status if status in ("BLOCKED", "FAILED") else "PARTIAL", {
                            "weeks_completed": completed, "week_index": index, "family": family,
                            "reason": reason,
                            "last_attempt": detail}
                else:
                    return "PARTIAL", {"weeks_completed": completed, "reason": "Đã chạm giới hạn tiếp tục an toàn của tuần; tiến độ đã lưu."}
            completed += 1
        return "COMPLETED", {"weeks_completed": completed, "weeks_total": len(periods)}
    finally:
        loop._weekly_progress = {}
