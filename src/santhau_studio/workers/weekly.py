"""A resumable six-family weekly job, using existing queue and coverage proofs."""
import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from santhau_studio.collectors.source_registry import FAMILY_ORDER
from santhau_studio.domain.coverage import Interval, vn_calendar_day
from santhau_studio.storage import coverage
from santhau_studio.storage.serving import mark_stale

CAPABILITY = "weekly-six-open-day-v3"


def weeks(span):
    """Keep the chosen seven-day boundaries, visiting the newest week first."""
    periods = []
    start = span.from_utc
    while start < span.to_utc:
        end = min(start + timedelta(days=7), span.to_utc)
        periods.append(Interval(start, end))
        start = end
    yield from reversed(periods)


def work_periods(periods, now):
    """Read today's snapshot for all families before proving closed history."""
    today = vn_calendar_day(now.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date())
    for index, week in enumerate(periods, 1):
        if now < week.to_utc <= today.to_utc:
            yield index, week, Interval(max(week.from_utc, today.from_utc), week.to_utc), True, week.from_utc >= today.from_utc
            if week.from_utc < today.from_utc:
                yield index, week, Interval(week.from_utc, today.from_utc), False, True
        else:
            yield index, week, week, False, True


def dispatch(loop, request, parent_run):
    sources = {s.get("family"): s for s in request.sources}
    if request.job_kind != "BACKFILL" or set(sources) != set(FAMILY_ORDER) or len(request.sources) != 6:
        return "BLOCKED", {"reason": "Thu thập theo tuần cần đủ sáu loại hồ sơ và chế độ thu thập theo khoảng ngày."}
    writer = loop._writer
    conn = writer.conn
    periods = list(weeks(request.interval()))
    completed = 0
    pending = []
    pending_weeks = set()
    children_since_publish = 0
    initial_work = list(work_periods(periods, loop._clock()))

    def scheduled_work():
        yield from initial_work
        # The history pass may cross midnight. Re-fetch the formerly open span;
        # the resolver skips families already verified after it closed.
        for index, week, span, current_day, _ in initial_work:
            if current_day and span.to_utc <= loop._clock():
                yield index, week, span, False, False

    try:
        for index, week, span, current_day, last_part in scheduled_work():
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
                    # Publish batches on the writer thread. Deferring until actual
                    # next work avoids rebuilding just before the terminal snapshot.
                    if children_since_publish >= len(FAMILY_ORDER):
                        loop._publish_snapshot()
                        children_since_publish = 0
                        if loop.flag.interrupt_requested:
                            return "PARTIAL", {"weeks_completed": completed, "reason": "Đã dừng theo yêu cầu; tiến độ đã lưu."}
                    loop._weekly_progress = {"week_index": index, "week_count": len(periods),
                        "week_from": week.from_utc.isoformat(), "week_to": week.to_utc.isoformat(),
                        "family_index": FAMILY_ORDER.index(family) + 1, "attempt": attempt,
                        "current_day_snapshot": current_day}
                    loop.progress = {"job_id": request.job_id, "family": family, "rows": 0, "pages": 0,
                        "from": span.from_utc.isoformat(), "to": span.to_utc.isoformat(),
                        **loop._weekly_progress}
                    child = replace(request, sources=(source,), scope={"from": span.from_utc.isoformat(), "to": span.to_utc.isoformat()})
                    child_run = "run-" + uuid.uuid4().hex
                    # Distinct runs preserve every retry's attempts/observations and leave the
                    # parent lifecycle as the sole user control target, including crash recovery.
                    child_id = f"{request.job_id}.w{index:03d}.{family}.{attempt}.{span.from_utc:%Y%m%dT%H%M}"
                    if current_day:
                        child_id += ".today"
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
                    children_since_publish += 1
                    after = resolve()
                    if loop.flag.interrupt_requested:
                        return "PARTIAL", {"weeks_completed": completed, "reason": "Đã dừng theo yêu cầu; tiến độ đã lưu."}
                    if after.can_skip:
                        break
                    if current_day and status == "PARTIAL" and detail.get("open_day_snapshot"):
                        pending.append({"family":family, "from":span.from_utc.isoformat(),
                            "to":span.to_utc.isoformat(), "rows":detail.get("rows",0),
                            "blockers":detail.get("blockers",[])})
                        pending_weeks.add(index)
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
                            "reason": f"Ngày {span.from_utc.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')):%d/%m/%Y}: {reason}",
                            "last_attempt": detail}
                else:
                    return "PARTIAL", {"weeks_completed": completed, "reason": "Đã chạm giới hạn tiếp tục an toàn của tuần; tiến độ đã lưu."}
            if last_part and index not in pending_weeks:
                completed += 1
        pending = [item for item in pending if not coverage.resolve(
            conn, sources[item["family"]]["source_key"], item["family"],
            datetime.fromisoformat(item["from"]),
            datetime.fromisoformat(item["to"])).can_skip]
        completed = sum(all(coverage.resolve(conn, sources[family]["source_key"], family,
            week.from_utc, week.to_utc).can_skip for family in FAMILY_ORDER) for week in periods)
        if pending:
            dates = ", ".join(sorted({
                datetime.fromisoformat(item["from"]).astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%d/%m/%Y")
                for item in pending}))
            return "PARTIAL", {"weeks_completed":completed, "weeks_total":len(periods),
                "pending_current_day":pending,
                "reason":f"Đã lấy dữ liệu hiện có ngày {dates}. Ngày này chưa kết thúc khi lấy dữ liệu nên chưa chốt đủ ngày; cần cập nhật sau khi ngày khép để kiểm chứng phần phát sinh."}
        return "COMPLETED", {"weeks_completed": completed, "weeks_total": len(periods)}
    finally:
        loop._weekly_progress = {}
