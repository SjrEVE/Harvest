"""The real endpoint rejects space-separated dates; use ISO T without shifting VN days."""
import json
from types import SimpleNamespace

import httpx

from santhau_studio.collectors.egp_home import EgpHomeLane, SearchWindow
from santhau_studio.collectors.http_client import FetchLane
from santhau_studio.collectors.network_policy import RetryPolicy


def test_verified_calendar_window_uses_the_accepted_wire_format():
    seen = []
    def handle(request):
        body = json.loads(request.content)
        bounds = next(f for f in body[0]["query"][0]["filters"] if f["searchType"] == "range")
        seen.append(bounds)
        if " " in bounds["from"] or " " in bounds["to"]:
            return httpx.Response(400, text="error")
        return httpx.Response(200, json={"page":{"content":[],"totalElements":0}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = FetchLane(url="https://muasamcong.mof.gov.vn/o/egp-portal-home/services/smart/search",
                         source_key="egp_home",client=client,retry=RetryPolicy(max_attempts=1),
                         pacing_sleeper=lambda _:None)
        source = EgpHomeLane(family="TBMT",lane=lane,
            window=SearchWindow(True,"2026-11-01 00:00:00.000","2026-11-03 23:59:59.999"))
        assert source.fetch_page(0).http_status == 200
    assert seen == [{"fieldName":"publicDate","searchType":"range",
                     "from":"2026-11-01T00:00:00.000","to":"2026-11-03T23:59:59.999"}]


def test_approved_canary_driver_uses_absolute_https_url(tmp_path,monkeypatch):
    import tools.canary as cli
    from santhau_studio.collectors import http_client
    seen=[]
    def handle(request):
        seen.append(str(request.url))
        return httpx.Response(200,json={"page":{"content":[],"totalElements":0}})
    client=httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(http_client,"FetchLane",lambda **kw:FetchLane(client=client,pacing_sleeper=lambda _:None,**kw))
    monkeypatch.setenv(cli.APPROVAL_ENV,"offline-test-only")
    monkeypatch.setenv(cli.ADAPTER_SHA_ENV,"a"*64)
    try:
        assert cli.run_probe(SimpleNamespace(execute=True,family="TBMT",data_root=tmp_path,
                                            max_requests=1,max_seconds=30))==0
    finally:
        client.close()
    assert seen==["https://muasamcong.mof.gov.vn/o/egp-portal-home/services/smart/search"]
