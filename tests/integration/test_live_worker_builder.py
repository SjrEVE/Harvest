"""Exercise the real worker builder with only its HTTP transport replaced."""
from contextlib import closing
from datetime import date
from types import SimpleNamespace

import httpx

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
