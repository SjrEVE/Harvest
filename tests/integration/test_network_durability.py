import ssl
import time
from datetime import datetime

import httpx
import pytest

from santhau_studio.collectors.budget import RequestBudget
from santhau_studio.collectors.http_client import FetchFailed, FetchLane
from santhau_studio.collectors.network_policy import HostRateLimiter, RetryPolicy
from santhau_studio.storage.warehouse import WarehouseWriter

URL = "https://muasamcong.mof.gov.vn/o/egp-portal-home/services/smart/search"


@pytest.fixture(autouse=True)
def isolated_pacing():
    HostRateLimiter.reset_registry()
    yield
    HostRateLimiter.reset_registry()


def lane(client, **kwargs):
    return FetchLane(
        url=URL,
        source_key="egp_home",
        client=client,
        sleeper=lambda _s: None,
        pacing_sleeper=lambda _s: None,
        **kwargs,
    )


def test_default_connect_timeout_accepts_a_ten_second_handshake():
    def slow_handshake(request):
        if request.extensions["timeout"]["connect"] < 10:
            raise httpx.ConnectTimeout("synthetic handshake takes 10 seconds")
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(slow_handshake)) as client:
        assert lane(client).post({}).payload == {"ok": True}


def test_temporary_tls_eof_recovers_on_fourth_attempt():
    calls = []

    def transport(_request):
        calls.append(1)
        if len(calls) < 4:
            raise httpx.ConnectError("temporary TLS EOF") from ssl.SSLEOFError(
                8, "EOF occurred in violation of protocol"
            )
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        result = lane(client).post({})
    assert len(calls) == 4 and result.payload["ok"]
    assert all(a.error_kind == "TLS_EOF_TRANSIENT" for a in result.attempts[:3])
    assert all(
        a.finished_at_utc
        and datetime.fromisoformat(a.finished_at_utc) >= datetime.fromisoformat(a.started_at_utc)
        for a in result.attempts
    )


@pytest.mark.parametrize(
    "cause",
    [
        ssl.SSLCertVerificationError(1, "certificate verify failed"),
        ssl.SSLError(1, "unsupported protocol"),
    ],
)
def test_certificate_and_unclassified_tls_failures_are_not_retried(cause):
    calls = []

    def transport(_request):
        calls.append(1)
        raise httpx.ConnectError("TLS negotiation failed") from cause

    with (
        httpx.Client(transport=httpx.MockTransport(transport)) as client,
        pytest.raises(FetchFailed) as caught,
    ):
        lane(client).post({})
    assert len(calls) == 1
    assert caught.value.verdict.kind == "BLOCKED"


def test_absolute_request_deadline_bounds_a_slow_transport():
    calls = []

    def transport(_request):
        calls.append(1)
        time.sleep(1.6)
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        start = time.monotonic()
        with pytest.raises(FetchFailed, match="DEADLINE"):
            lane(client, budget=RequestBudget(10, 1)).post({})
        assert time.monotonic() - start < 1.4
    assert len(calls) == 1


def test_attempt_store_keeps_transport_times_and_elapsed(tmp_path):
    writer = WarehouseWriter.open(tmp_path, create_schema=True)
    try:
        writer.register_run("timing", "timing", "BACKFILL")
        start, end = "2026-11-05T00:00:01.123456Z", "2026-11-05T00:00:11.456789Z"
        aid = writer.ensure_attempt(
            run_id="timing",
            family="TBMT",
            endpoint="synthetic",
            interval_from=start,
            interval_to=end,
            page_no=0,
            attempt_no=1,
            outcome="TIMEOUT",
            response=None,
            started_at_utc=start,
            finished_at_utc=end,
            elapsed_seconds=10.333333,
            wait_seconds=3.0,
        )
        row = writer.conn.execute(
            "SELECT started_at_utc,finished_at_utc FROM fetch_attempts WHERE id=?", (aid,)
        ).fetchone()
        assert tuple(row) == (start, end)
        metrics = writer.conn.execute(
            "SELECT elapsed_seconds,wait_seconds FROM fetch_attempt_telemetry WHERE attempt_id=?",
            (aid,),
        ).fetchone()
        assert tuple(metrics) == (10.333333, 3.0)
    finally:
        writer.close()


def test_retry_jitter_is_bounded_and_attempt_count_is_validated(monkeypatch):
    monkeypatch.setattr("random.random", lambda: 1.0)
    policy = RetryPolicy(max_attempts=5, jitter_fraction=0.2)
    assert policy.delay_for(1, None) == pytest.approx(3.6)
    assert policy.delay_for(3, 50) == 50
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=6)


def test_worker_status_has_request_heartbeat_and_last_success(tmp_path):
    from santhau_studio.workers.jobs import WorkerLoop
    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={}))) as client:
        lane(client).post({})
    status = WorkerLoop(tmp_path, publish_serving=False).runtime_status()
    network = status["network"]["muasamcong.mof.gov.vn"]
    assert network["state"] == "SUCCESS" and network["last_successful_request"]
    assert network["last_attempt_started_at"] <= network["last_successful_request"]


def test_host_lease_wait_respects_deadline_without_sending(tmp_path, monkeypatch):
    from santhau_studio.platform_win.host_gate import HostRequestGate
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    calls = []
    def transport(_request):
        calls.append(1)
        return httpx.Response(200, json={})
    client = httpx.Client(transport=httpx.MockTransport(transport))
    monkeypatch.setattr(httpx, "Client", lambda **_kw: client)
    request_lane = FetchLane(url=URL, source_key="egp_home", budget=RequestBudget(10, 1))
    with HostRequestGate("muasamcong.mof.gov.vn", 0), pytest.raises(FetchFailed):
        request_lane.post({})
    time.sleep(0.1)  # The cancelled lease waiter must not send after the holder exits.
    assert not calls
