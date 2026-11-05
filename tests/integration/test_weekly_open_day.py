"""An unfinished calendar day must not stall six-family historical collection."""
import json
from contextlib import closing
from datetime import UTC, date, datetime

import pytest

from santhau_studio.collectors.egp_home import Page
from santhau_studio.collectors.http_client import AccessBlocked, FetchFailed
from santhau_studio.collectors.offline_acceptance import initialize
from santhau_studio.collectors.source_registry import FAMILY_ORDER
from santhau_studio.domain.coverage import vn_calendar_day
from santhau_studio.storage.warehouse import connect_read_only
from santhau_studio.workers.jobs import JobRequest, WorkerLoop, enqueue_job

TODAY = vn_calendar_day(date(2026, 11, 4))
NOW = datetime(2026, 11, 4, 12, tzinfo=UTC)


def collect(root, calls, *, first=date(2026, 10, 27), now=NOW, fault=None, drift=False, clock=None):
    if not (root / "warehouse.sqlite").exists():
        initialize(root)
    with closing(connect_read_only(root / "warehouse.sqlite")) as conn:
        sources = tuple(dict(conn.execute(
            "SELECT source_key,family,endpoint,adapter_sha FROM filter_boundary_rules WHERE family=?",
            (family,)).fetchone()) for family in FAMILY_ORDER)
    request = JobRequest(job_id=f"test-{len(list((root / 'jobs/done').glob('*.json')))}",
        job_kind="BACKFILL", requested_by="test", sources=sources,
        scope={"cadence":"WEEK", "from":vn_calendar_day(first).from_utc.isoformat(),
               "to":TODAY.to_utc.isoformat()}, created_at=now.isoformat())
    enqueue_job(root, request)

    def builder(_writer, child):
        family = child.sources[0]["family"]
        key, version = {"TBMT":("notifyNo","notifyVersion"), "KQLCNT":("notifyNo","notifyVersion"),
            "KHLCNT":("planNo","planVersion"), "HOP_DONG":("contractCode","contractVersion"),
            "DU_AN":("pno","pversion"), "YCBG":("notifyNo","notifyVersion")}[family]

        def pages(interval, _granularity, _window):
            calls.append((family, interval))
            if fault:
                fault(interval)
            page_count = 2 if drift and interval == TODAY else 1
            for page in range(page_count):
                row = {key:f"SYNTH-{family}-{interval.from_utc:%Y%m%d}-{page}", version:"00",
                    "publicDate":interval.from_utc.isoformat(), "publicDateKqlcnt":interval.from_utc.isoformat(),
                    "bidName":"Thử ngoại tuyến", "pname":"Thử ngoại tuyến", "ctName":"Thử ngoại tuyến"}
                yield Page(payload={"page":{"content":[row],"totalElements":page+1}},
                    page_no=page, rows=1, has_more=page+1<page_count, total_hint=page+1,
                    total_hint_trusted=True, http_status=200, attempt_log=())
        return pages

    loop = WorkerLoop(root, page_source_builder=builder, clock=clock or (lambda: now),
        idle_exit_seconds=.01, poll_seconds=.001)
    loop.run()
    assert loop.exit_code == 0, loop.last_error
    with closing(connect_read_only(root / "warehouse.sqlite")) as conn:
        row = conn.execute("SELECT r.status,t.detail_json FROM runs r JOIN run_tasks t USING(run_id) WHERE r.job_id=?",
            (request.job_id,)).fetchone()
        counts = tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("source_records","source_record_versions","observations","coverage_proofs"))
        open_rows = conn.execute("SELECT status,proof_id FROM coverage_intervals WHERE interval_to_utc>?",
            (NOW.isoformat().replace("+00:00", "Z"),)).fetchall()
    return row["status"], json.loads(row["detail_json"]), counts, open_rows


def test_current_day_collects_all_families_and_history_without_false_complete(tmp_path):
    calls = []
    status, detail, counts, open_rows = collect(tmp_path, calls)
    assert counts == (54,54,54,48)
    assert status == "PARTIAL" and len(detail["pending_current_day"]) == 6
    assert detail["weeks_completed"] == 1
    assert "chưa kết thúc" in detail["reason"] and "bị chặn" not in detail["reason"]
    assert [family for family,_ in calls[:6]] == list(FAMILY_ORDER)
    assert all(span == TODAY for _,span in calls[:6])
    assert len(open_rows) == 6 and all(row[:] == ("PARTIAL",None) for row in open_rows)
    calls.clear()
    status, _, again, _ = collect(tmp_path, calls, now=datetime(2026,11,5,12,tzinfo=UTC))
    # Six new observations describe the later verification; records/versions stay unique.
    assert status == "COMPLETED" and again == (54,54,60,54)
    assert len(calls) == 6 and all(span == TODAY for _,span in calls)
    calls.clear()
    status, _, third, _ = collect(tmp_path, calls, now=datetime(2026,11,5,13,tzinfo=UTC))
    assert status == "COMPLETED" and third == again and calls == []


def test_today_only_is_a_snapshot_for_all_six_families(tmp_path):
    calls = []
    status, detail, counts, _ = collect(tmp_path, calls, first=date(2026,11,4))
    assert counts == (6,6,6,0) and status == "PARTIAL"
    assert detail["weeks_completed"] == 0 and len(detail["pending_current_day"]) == 6


def test_changing_total_today_does_not_block_closed_days(tmp_path):
    calls = []
    status, detail, counts, _ = collect(tmp_path, calls, drift=True)
    assert counts == (60,60,60,48) and status == "PARTIAL"
    assert len(detail["pending_current_day"]) == 6


@pytest.mark.parametrize("kind", ["network", "captcha", "budget"])
def test_real_fault_today_still_stops_collection(tmp_path, kind):
    calls = []
    def fault(_interval):
        if kind == "captcha":
            raise AccessBlocked("CAPTCHA")
        raise FetchFailed("NETWORK_UNAVAILABLE" if kind == "network" else "REQUEST_BUDGET_EXHAUSTED")
    status, detail, counts, _ = collect(tmp_path, calls, fault=fault)
    assert status in ("PARTIAL","BLOCKED") and not detail.get("pending_current_day")
    assert counts == (0,0,0,0)
    assert len(calls) == 1


def test_pending_today_cannot_hide_a_fault_on_a_closed_day(tmp_path):
    calls = []
    def fault(interval):
        if interval != TODAY:
            raise FetchFailed("NETWORK_UNAVAILABLE")
    status, detail, counts, _ = collect(tmp_path, calls, fault=fault)
    assert status == "PARTIAL" and counts == (6,6,6,0)
    assert detail["weeks_completed"] == 0
    assert "NETWORK_UNAVAILABLE" in str(detail["last_attempt"])
    assert len(calls) == 7


def test_crossing_midnight_rechecks_only_pending_day(tmp_path):
    calls = []
    current = [NOW]
    def rollover(interval):
        if interval != TODAY:
            current[0] = datetime(2026, 11, 5, 0, tzinfo=UTC)
    status, detail, counts, _ = collect(tmp_path, calls, fault=rollover, clock=lambda: current[0])
    assert status == "COMPLETED"
    assert detail["weeks_completed"] == 2
    assert counts == (54, 54, 60, 54)
    assert len(calls) == 60 and all(span == TODAY for _, span in calls[-6:])
    calls.clear()
    assert collect(tmp_path, calls, now=current[0])[2] == counts
    assert calls == []


def test_day_closing_during_fetch_requires_a_fresh_fetch(tmp_path):
    calls = []
    current = [NOW]
    def rollover(_interval):
        current[0] = datetime(2026, 11, 5, 0, tzinfo=UTC)
    status, _, counts, _ = collect(tmp_path, calls, first=date(2026, 11, 4),
        fault=rollover, clock=lambda: current[0])
    assert status == "COMPLETED"
    # Only the first family began before midnight; it must be observed again.
    assert counts == (6, 6, 7, 6)
    assert len(calls) == 7 and calls[-1][0] == FAMILY_ORDER[0]


def test_closing_day_failure_remains_partial_without_false_proof(tmp_path):
    calls = []
    current = [NOW]
    def fault(interval):
        if interval != TODAY:
            current[0] = datetime(2026, 11, 5, 0, tzinfo=UTC)
        elif current[0] != NOW:
            raise FetchFailed("NETWORK_UNAVAILABLE")
    status, detail, counts, _ = collect(tmp_path, calls, fault=fault, clock=lambda: current[0])
    assert status == "PARTIAL" and counts == (54, 54, 54, 48)
    assert "NETWORK_UNAVAILABLE" in str(detail.get("last_attempt"))
    assert "04/11/2026" in detail["reason"]
    assert "Hôm nay chưa kết thúc" not in detail["reason"]


def test_partial_snapshot_reports_access_without_granting_skip(tmp_path):
    from santhau_studio.storage.coverage import resolve
    collect(tmp_path, [], first=date(2026, 11, 4))
    with closing(connect_read_only(tmp_path / "warehouse.sqlite")) as conn:
        result = resolve(conn, "egp_home", "TBMT", TODAY.from_utc, TODAY.to_utc)
    assert result.status == "PARTIAL"
    assert result.proven_fraction == 0 and not result.can_skip
    assert result.proof_ids == () and result.gaps == (TODAY,)
