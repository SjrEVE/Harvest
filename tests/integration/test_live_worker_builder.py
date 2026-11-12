"""Exercise the real worker builder with only its HTTP transport replaced."""
from contextlib import closing
from datetime import date
from types import SimpleNamespace

import httpx
from test_runner_modes import ADAPTER_SHA, DAY, ENDPOINT, make_runner, page_of
from test_runner_modes import writer as writer

from santhau_studio.collectors import http_client
from santhau_studio.collectors.live_builder import live_builder as _default_builder
from santhau_studio.collectors.source_registry import SOURCES
from santhau_studio.domain.coverage import vn_calendar_day
from santhau_studio.storage.warehouse import WarehouseWriter
from santhau_studio.workers.runner import SearchWindow


def test_default_worker_uses_absolute_allowlisted_endpoint(tmp_path, monkeypatch):
    original = http_client.FetchLane
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"page": {"content": [], "totalElements": 0}})

    def lane(**kwargs):
        return original(**kwargs, client=httpx.Client(transport=httpx.MockTransport(respond)),
                        sleeper=lambda _seconds: None, pacing_sleeper=lambda _seconds: None)

    monkeypatch.setattr(http_client, "FetchLane", lane)
    request = SimpleNamespace(sources=[{"source_key": "egp_home", "family": "TBMT"}],
                              request_limits={"max_requests": 1, "max_seconds": 30})
    with closing(WarehouseWriter.open(tmp_path / "data", create_schema=True)) as writer:
        source = _default_builder(writer, request)
        pages = list(source(vn_calendar_day(date(2026, 11, 1)), "DAY",
                            SearchWindow("2026-11-01 00:00:00", "2026-11-01 23:59:59")))
    assert len(pages) == 1 and pages[0].http_status == 200
    spec = SOURCES["egp_home"]
    assert str(requests[0].url) == f"https://{spec.host}{spec.endpoint}"


def test_live_builder_passes_remaining_http_budget_to_early_split(writer, monkeypatch):
    import json

    from santhau_studio.collectors.lane_source import bounded_source
    from santhau_studio.collectors.network_policy import HostRateLimiter

    original = http_client.FetchLane
    calls = []
    clients = []

    def respond(request):
        filters = json.loads(request.content)[0]["query"][0]["filters"]
        window = next(f for f in filters if f["searchType"] == "range")
        calls.append(window)
        total = 50 if window["from"].endswith("00:00:00") and window["to"].endswith("23:59:59") else 0
        rows = page_of(0, 10).payload["page"]["content"] if total else []
        return httpx.Response(200, json={"page": {"content": rows, "totalElements": total}})

    def lane(**kwargs):
        client = httpx.Client(transport=httpx.MockTransport(respond))
        clients.append(client)
        value = original(**kwargs, client=client, sleeper=lambda _: None, pacing_sleeper=lambda _: None)
        value.limiter = HostRateLimiter(value.host)
        return value

    monkeypatch.setattr(http_client, "FetchLane", lane)
    request = SimpleNamespace(sources=[{"source_key": "egp_home", "family": "TBMT",
                                       "adapter_sha": ADAPTER_SHA, "endpoint": ENDPOINT}],
                              request_limits={"max_requests": 2, "max_seconds": 60})
    try:
        source = bounded_source(_default_builder(writer, request), max_pages=100, max_records=1000)
        result = make_runner(writer, source).run_interval(DAY, "DAY", "run-1")
        assert result.children and any(child.status == "COMPLETE" for child in result.children)
        assert result.status == "PARTIAL" and len(calls) == 2
        assert result.requests == 2
        assert writer.conn.execute("SELECT COUNT(*) FROM coverage_proofs").fetchone()[0] == 1
    finally:
        for client in clients:
            client.close()
