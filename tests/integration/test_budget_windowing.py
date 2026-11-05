"""Budget planning must save proven time, never treat old pages as proof."""
from datetime import timedelta

import pytest
from test_runner_modes import DAY, make_runner, page_of
from test_runner_modes import writer as writer

from santhau_studio.collectors.egp_home import Page
from santhau_studio.collectors.lane_source import bounded_source
from santhau_studio.storage import coverage


def hourly_source(calls, *, saturated=False):
    def source(interval, granularity, _window):
        first = int((interval.from_utc - DAY.from_utc).total_seconds() // 3600)
        hours = int(interval.duration.total_seconds() // 3600)
        rows = [{"notifyNo": f"SYNTH-H{hour}-{i}", "notifyVersion": "00", "bidName": "Offline"}
                for hour in range(first, first + hours) for i in range(10)]
        total = 10_000 if saturated and granularity == "DAY" else len(rows)
        for page_no in range(len(rows) // 10 + 1):
            calls.append((interval, page_no))
            content = rows[page_no * 10:(page_no + 1) * 10]
            yield Page(payload={"page": {"content": content, "totalElements": total}},
                page_no=page_no, rows=len(content), has_more=len(content) == 10,
                total_hint=total, total_hint_trusted=total < 10_000, http_status=200)
    return source


def test_saturated_total_splits_after_one_page_and_reports_parent_work(writer):
    calls = []
    outcome = make_runner(writer, hourly_source(calls, saturated=True)).run_interval(DAY, "DAY", "run-1")
    assert len([c for c in calls if c[0] == DAY]) == 1
    assert outcome.status == "COMPLETE"
    assert outcome.rows == 250 and outcome.pages == 49 and outcome.requests == 49
    assert outcome.records_new == 240 and outcome.records_unchanged == 10
    assert writer.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 250
    assert coverage.resolve(writer.conn, "egp_home", "TBMT", DAY.from_utc, DAY.to_utc).can_skip


def test_remaining_budget_splits_early_then_reruns_only_unproven_time(writer):
    calls = []
    attempt = 0
    def collect():
        nonlocal attempt
        attempt += 1
        run_id = f"budget-run-{attempt}"
        writer.register_run(run_id, f"budget-job-{attempt}", "BACKFILL")
        source = bounded_source(hourly_source(calls), max_pages=8, max_records=10000)
        return make_runner(writer, source).backfill(run_id, DAY)
    outcomes = collect()
    resolved = coverage.resolve(writer.conn, "egp_home", "TBMT", DAY.from_utc, DAY.to_utc)
    assert resolved.proven_fraction == 3 / 24
    assert len(calls) == 8 and len([c for c in calls if c[0] == DAY]) == 1
    # The final budget refusal made no HTTP request. The parent probe still counts.
    assert sum(o.requests for o in outcomes) == 8
    assert sum(o.rows for o in outcomes) == 50
    assert len(outcomes[0].children) == 4
    for _ in range(10):
        previous_proven = resolved.proven
        calls.clear()
        collect()
        assert all(not call[0].intersects(proven) for call in calls for proven in previous_proven)
        resolved = coverage.resolve(writer.conn, "egp_home", "TBMT", DAY.from_utc, DAY.to_utc)
        if resolved.can_skip:
            break
    assert resolved.can_skip
    assert writer.conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0] == 240
    assert writer.conn.execute("SELECT COUNT(*) FROM source_record_versions").fetchone()[0] == 240
    calls.clear()
    assert collect() == [] and calls == []


@pytest.mark.parametrize("limit", ["request", "time"])
def test_http_budget_capacity_reaches_runner_through_bounded_source(writer, limit):
    import httpx

    from santhau_studio.collectors.budget import RequestBudget
    from santhau_studio.collectors.http_client import FetchLane
    from santhau_studio.collectors.lane_source import egp_page_source
    from santhau_studio.collectors.network_policy import HostRateLimiter

    calls = []
    def portal(request):
        import json
        body = json.loads(request.content)[0]
        date_filter = next(f for f in body["query"][0]["filters"] if f["searchType"] == "range")
        calls.append(date_filter)
        count = 240 if date_filter["to"].endswith("23:59:59") and date_filter["from"].endswith("00:00:00") else 0
        content = page_of(0, 10).payload["page"]["content"] if count else []
        return httpx.Response(200, json={"page": {"content": content, "totalElements": count}})
    budget = RequestBudget(8 if limit == "request" else 100, 3600 if limit == "request" else 24,
                           clock=lambda: 0)
    with httpx.Client(transport=httpx.MockTransport(portal)) as client:
        def lane_factory():
            lane = FetchLane(url="https://muasamcong.mof.gov.vn/o/egp-portal-home/services/smart/search",
                source_key="egp_home", client=client, budget=budget, sleeper=lambda _s: None)
            lane.limiter = HostRateLimiter(lane.host)
            return lane
        source = egp_page_source(family="TBMT", pacing_seconds=3, budget=budget,
            lane_factory=lane_factory)
        runner = make_runner(writer, bounded_source(source, max_pages=1000, max_records=10000))
        outcome = runner.run_interval(DAY, "DAY", "run-1")
    assert len([c for c in calls if c["from"].endswith("00:00:00") and c["to"].endswith("23:59:59")]) == 1
    assert outcome.children and any(c.status == "COMPLETE" for c in outcome.children)


def test_exact_multiple_reserves_an_empty_terminal_page(writer):
    calls = []
    span = type(DAY)(DAY.from_utc, DAY.from_utc + timedelta(hours=3))
    source = bounded_source(hourly_source(calls), max_pages=3, max_records=10000)
    outcome = make_runner(writer, source).run_interval(span, "HOUR", "run-1")
    assert any(child.status == "COMPLETE" for child in outcome.children)
    assert not coverage.resolve(writer.conn, "egp_home", "TBMT", span.from_utc, span.to_utc).can_skip


def test_stop_after_a_proven_child_cannot_complete_the_parent(writer):
    calls = []
    runner = make_runner(writer, hourly_source(calls, saturated=True),
        stop=lambda: writer.conn.execute("SELECT COUNT(*) FROM coverage_proofs").fetchone()[0] > 0)
    outcome = runner.run_interval(DAY, "DAY", "run-1")
    from santhau_studio.workers.jobs import _judge
    assert outcome.status == "PARTIAL"
    assert _judge([outcome]) == "PARTIAL"
    assert not coverage.resolve(writer.conn, "egp_home", "TBMT", DAY.from_utc, DAY.to_utc).can_skip


def test_day_only_rule_defers_after_probe_instead_of_spending_remaining_budget(writer):
    from test_runner_modes import verified_rule
    calls = []
    def source(interval, _granularity, _window):
        for page in (page_of(0, 10, total=25), page_of(1, 10, total=25), page_of(2, 5, total=25)):
            calls.append((interval, page.page_no))
            yield page
    runner = make_runner(writer, bounded_source(source, max_pages=5, max_records=10000),
                         rule=verified_rule(writer, "DAY"))
    span = type(DAY)(DAY.from_utc, DAY.to_utc + timedelta(days=1))
    outcomes = runner.backfill("run-1", span)
    assert len(calls) == 4  # complete newest day (3), probe next (1), defer
    assert outcomes[0].status == "COMPLETE" and outcomes[1].status == "PARTIAL"
    assert not outcomes[1].children
    assert any("BUDGET_EXHAUSTED" in b for b in outcomes[1].blockers)
    assert coverage.resolve(writer.conn, "egp_home", "TBMT", span.from_utc, span.to_utc).proven_fraction == .5


def test_worker_summary_reconciles_parent_probe_with_observations(tmp_path):
    import json
    from contextlib import closing

    from test_worker_ipc import _insert_rule, job_request

    from santhau_studio.storage.warehouse import connect_read_only
    from santhau_studio.workers.jobs import WorkerLoop, enqueue_job
    calls = []
    def builder(writer, request):
        _insert_rule(writer, request, precision="HOUR")
        return hourly_source(calls)
    request = job_request(request_limits={"max_pages": 8})
    enqueue_job(tmp_path, request)
    loop = WorkerLoop(tmp_path, page_source_builder=builder, idle_exit_seconds=.01)
    loop.run()
    assert loop.exit_code == 0, loop.last_error
    with closing(connect_read_only(tmp_path / "warehouse.sqlite")) as conn:
        detail = json.loads(conn.execute("SELECT detail_json FROM run_tasks").fetchone()[0])
        assert detail["rows"] == detail["rows_received"] == 50
        assert detail["requests"] == detail["attempts"] == len(calls) == 8
        assert detail["records_new"] == 50 and detail["records_unchanged"] == 0
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == detail["rows"]
        assert conn.execute("SELECT COUNT(*) FROM fetch_requests").fetchone()[0] == detail["requests"]


def test_early_split_closes_each_factory_owned_http_client(writer, monkeypatch):
    from contextlib import nullcontext

    import httpx

    from santhau_studio.collectors.http_client import FetchLane
    from santhau_studio.collectors.lane_source import egp_page_source
    from santhau_studio.collectors.network_policy import HostRateLimiter
    clients = []
    def reply(_self, _url, **kwargs):
        body = kwargs["json"][0]
        window = next(f for f in body["query"][0]["filters"] if f["searchType"] == "range")
        full_day = window["from"].endswith("00:00:00") and window["to"].endswith("23:59:59")
        content = page_of(0, 10).payload["page"]["content"] if full_day else []
        return httpx.Response(200, json={"page": {"content": content, "totalElements": 10000 if full_day else 0}})
    monkeypatch.setattr(httpx.Client, "post", reply)
    monkeypatch.setattr("santhau_studio.collectors.http_client.HostRequestGate", lambda *_args: nullcontext())
    def factory():
        lane = FetchLane(url="https://muasamcong.mof.gov.vn/o/egp-portal-home/services/smart/search",
            source_key="egp_home", sleeper=lambda _s: None)
        lane.limiter = HostRateLimiter(lane.host)
        clients.append(lane.client)
        return lane
    source = egp_page_source(family="TBMT", lane_factory=factory)
    outcome = make_runner(writer, source).run_interval(DAY, "DAY", "run-1")
    try:
        assert outcome.status == "COMPLETE"
        assert clients and all(client.is_closed for client in clients)
    finally:
        for client in clients:
            client.close()
