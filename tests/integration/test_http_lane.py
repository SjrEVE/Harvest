"""§6 network policy, exercised through the real lane with an offline transport.

No socket is opened: `httpx.MockTransport` runs the client's own redirect, cookie, status and
retry machinery, so what is asserted is the production code path, not a stand-in. The host stays
`muasamcong.mof.gov.vn` because the allowlist is exact-match — the URL is never resolved.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx
import pytest

from santhau_studio.collectors import network_policy
from santhau_studio.collectors.http_client import (
    AccessBlocked,
    FetchFailed,
    FetchLane,
    InsecureRequest,
    build_ssl_context,
    portal_headers,
)
from santhau_studio.collectors.network_policy import HostRateLimiter

URL = "https://muasamcong.mof.gov.vn/o/egp-portal-home/services/smart/search"


@pytest.fixture(autouse=True)
def fast_pacing(monkeypatch):
    """Keep the host floor real but short, so pacing is observed rather than waited out."""
    monkeypatch.setitem(network_policy.HOST_PACING_FLOOR, "muasamcong.mof.gov.vn", 0.02)
    HostRateLimiter.reset_registry()
    yield
    HostRateLimiter.reset_registry()


class Recorder:
    """Canned responses in order; the last one repeats once they run out."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    def client(self) -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(self.handler),
            headers=portal_headers("muasamcong.mof.gov.vn"),
        )


def ok(body: dict | None = None) -> httpx.Response:
    return httpx.Response(200, json=body if body is not None else {"page": {"content": [], "totalElements": 0}})


def lane_for(
    recorder: Recorder,
    *,
    sleeps: list[float],
    max_attempts: int = 3,
    pacing_sleeper: Callable[[float], None] | None = None,
) -> FetchLane:
    """Backoff sleeps are recorded; pacing waits are observed through Attempt.wait_seconds."""
    return FetchLane(
        url=URL,
        source_key="egp_home",
        client=recorder.client(),
        retry=network_policy.RetryPolicy(max_attempts=max_attempts, jitter_fraction=0),
        sleeper=sleeps.append,
        pacing_sleeper=pacing_sleeper or _consume,
    )


def _consume(seconds: float) -> None:
    del seconds


# ------------------------------------------------------------------ pacing

def test_two_families_on_one_host_share_one_limiter(monkeypatch) -> None:
    """The source repo's per-adapter limiter let six lanes fire at one host 6x faster."""
    clock = [100.0]
    issued: list[float] = []
    original_handler = Recorder.handler

    def advance(seconds: float) -> None:
        clock[0] += seconds

    def handler(recorder: Recorder, request: httpx.Request) -> httpx.Response:
        issued.append(clock[0])
        response = original_handler(recorder, request)
        advance(0.006)
        return response

    monkeypatch.setattr(network_policy.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(Recorder, "handler", handler)
    first, second = Recorder(ok()), Recorder(ok())
    lane_a = lane_for(first, sleeps=[], pacing_sleeper=advance)
    lane_b = lane_for(second, sleeps=[], pacing_sleeper=advance)
    assert lane_a.limiter is lane_b.limiter, "the limiter is keyed by host, not by adapter instance"

    a, b = lane_a.post([{}]), lane_b.post([{}])
    assert a.attempts[0].wait_seconds == 0.0, "an idle host fires immediately"
    assert b.attempts[0].wait_seconds == pytest.approx(0.014)
    assert issued[1] >= issued[0] + 0.02, "families share the full host pacing interval"
    assert len(first.requests) == 1 and len(second.requests) == 1


def test_pacing_floor_is_the_max_of_host_and_request(monkeypatch) -> None:
    monkeypatch.setitem(network_policy.HOST_PACING_FLOOR, "pacing.example", 0.02)
    limiter = HostRateLimiter.for_host("pacing.example")
    assert limiter.floor == 0.02
    slept: list[float] = []
    assert limiter.acquire(sleeper=slept.append) == 0.0, "the first slot on an idle host is immediate"
    assert limiter.acquire(sleeper=slept.append) == pytest.approx(0.02, abs=0.01), "the host floor is waited out"
    # A wider requested interval is measured from the previous grant, which was itself scheduled
    # ahead, so the wait is at least the interval rather than exactly it.
    assert limiter.acquire(min_interval=0.2, sleeper=slept.append) >= 0.2, "raise the gap, never lower it"
    assert HostRateLimiter("unknown.example").floor == network_policy.DEFAULT_PACING_FLOOR


# ------------------------------------------------------------------ fail-closed statuses

@pytest.mark.parametrize("status", [401, 403])
def test_auth_refusals_stop_without_retrying(status: int) -> None:
    recorder = Recorder(httpx.Response(status, text="no"))
    lane = lane_for(recorder, sleeps=[])
    with pytest.raises(AccessBlocked, match="refused access"):
        lane.post([{}])
    assert len(recorder.requests) == 1, "401/403 is never retried (§6)"


def test_captcha_page_is_a_stop_not_a_retry() -> None:
    recorder = Recorder(httpx.Response(200, text="<html>please enter CAPTCHA to continue</html>"))
    lane = lane_for(recorder, sleeps=[])
    with pytest.raises(AccessBlocked, match="CAPTCHA"):
        lane.post([{}])
    assert len(recorder.requests) == 1


def test_5xx_retries_within_the_bound_then_fails() -> None:
    sleeps: list[float] = []
    recorder = Recorder(httpx.Response(503), httpx.Response(503), httpx.Response(503))
    lane = lane_for(recorder, sleeps=sleeps)
    with pytest.raises(FetchFailed, match="SERVER_ERROR"):
        lane.post([{}])
    assert len(recorder.requests) == 3
    assert sleeps == [3.0, 9.0], "bounded exponential backoff, no infinite retry"


def test_timeout_is_transient_and_retried() -> None:
    sleeps: list[float] = []
    recorder = Recorder(httpx.ReadTimeout("read timed out"), ok())
    lane = lane_for(recorder, sleeps=sleeps)
    outcome = lane.post([{}])
    assert outcome.payload["page"]["content"] == []
    assert outcome.attempt_count == 2
    assert len(recorder.requests) == 2


def test_429_honours_retry_after_and_penalizes_the_host() -> None:
    sleeps: list[float] = []
    recorder = Recorder(httpx.Response(429, headers={"Retry-After": "17"}), ok())
    lane = lane_for(recorder, sleeps=sleeps)
    outcome = lane.post([{}])
    assert outcome.attempt_count == 2
    assert sleeps == [17.0], "Retry-After beats the local backoff schedule"
    assert outcome.attempts[1].wait_seconds >= 16.0, "the wait is applied to the host, not just this lane"
    assert HostRateLimiter.for_host("muasamcong.mof.gov.vn") is lane.limiter


def test_retry_after_http_date_is_parsed_not_zeroed() -> None:
    later = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(time.time() + 30))
    verdict = network_policy.classify(429, retry_after=network_policy.parse_retry_after(later))
    assert verdict.retry_after_s is not None and 20 <= verdict.retry_after_s <= 31
    assert network_policy.parse_retry_after("garbage") is None, "unparseable is None, never 0"


def test_malformed_json_fails_closed_instead_of_yielding_an_empty_page() -> None:
    """An empty-looking page that is really a parse failure must not become coverage."""
    recorder = Recorder(httpx.Response(200, text="<html>wrong</html>"))
    lane = lane_for(recorder, sleeps=[])
    with pytest.raises(FetchFailed, match="not JSON"):
        lane.post([{}])


# ------------------------------------------------------------------ redirects and TLS

def test_redirect_is_never_followed_so_credentials_cannot_move_hosts() -> None:
    """FAULT-11: the guarantee is that no second request is made at all."""
    recorder = Recorder(
        httpx.Response(302, headers={"Location": "https://evil.example/steal"}),
        httpx.Response(200, json={"leaked": True}),
    )
    lane = lane_for(recorder, sleeps=[])
    with pytest.raises(FetchFailed, match="REDIRECT"):
        lane.post([{}])
    assert len(recorder.requests) == 1, "the target host was never contacted"


def test_http_endpoint_is_refused_before_any_request() -> None:
    with pytest.raises(InsecureRequest, match="non-HTTPS"):
        FetchLane(url="http://muasamcong.mof.gov.vn/o/egp-portal-home/services/smart/search", source_key="egp_home")


def test_unlisted_host_is_refused_before_any_request() -> None:
    with pytest.raises(InsecureRequest, match="allowlist"):
        FetchLane(url="https://muasamcong.mof.gov.vn.evil.example/search", source_key="egp_home")


def test_query_string_on_the_endpoint_is_refused() -> None:
    """The old code put the VNEPS token in a query string; an endpoint must not carry one."""
    with pytest.raises(InsecureRequest, match="no query string"):
        FetchLane(url=URL + "?token=notarealsecret", source_key="egp_home")


def test_portal_tls_keeps_the_same_security_policy_as_other_hosts() -> None:
    """Spec section 6 forbids lowering TLS security even for the portal."""
    portal = build_ssl_context("muasamcong.mof.gov.vn")
    other = build_ssl_context("example.org")
    portal_ciphers = {c["name"] for c in portal.get_ciphers()}
    other_ciphers = {c["name"] for c in other.get_ciphers()}
    assert other_ciphers == portal_ciphers
    assert build_ssl_context("MUASAMCONG.MOF.GOV.VN").get_ciphers()[0]["name"] == portal.get_ciphers()[0]["name"]


def test_tls_does_not_offer_finite_field_dh_to_portal() -> None:
    """The portal selects undersized finite-field DH when offered it (live RC3 probe)."""
    context = build_ssl_context("muasamcong.mof.gov.vn")
    ciphers = context.get_ciphers()
    assert not any(c["kea"] == "kx-dhe" for c in ciphers)
    assert any(c["kea"] == "kx-ecdhe" for c in ciphers)
    assert all(c["aead"] for c in ciphers)


# ------------------------------------------------------------------ credential hygiene

def test_cookies_are_discarded_after_every_response() -> None:
    """A session cookie on a session-free channel is a session nobody authorised."""
    recorder = Recorder(httpx.Response(200, json={"page": {"content": [], "totalElements": 0}},
                                       headers={"Set-Cookie": "JSESSIONID=abcdef; Path=/"}))
    lane = lane_for(recorder, sleeps=[])
    lane.post([{}])
    lane.post([{}])
    assert dict(lane.client.cookies) == {}


def test_exception_text_never_carries_a_token_into_storage() -> None:
    secret = "https://muasamcong.mof.gov.vn/search?token=supersecretvalue&t=2"
    message = network_policy.scrub_exception(httpx.ConnectError(secret))
    assert "supersecretvalue" not in message
    assert "[REDACTED]" in message


def test_scrubbing_is_idempotent_and_leaves_clean_urls_alone() -> None:
    clean = "https://muasamcong.mof.gov.vn/o/egp-portal-home/services/smart/search"
    once = network_policy.scrub_url(clean)
    assert once == clean == network_policy.scrub_url(once)
    dirty = network_policy.scrub_url("https://h/x?token=abc&sid=99")
    assert network_policy.scrub_url(dirty) == dirty


def test_headers_carry_no_authorization_and_match_the_portal_client() -> None:
    headers = portal_headers("muasamcong.mof.gov.vn")
    assert "Authorization" not in headers and "Cookie" not in headers
    assert headers["Origin"] == "https://muasamcong.mof.gov.vn"
    assert headers["Content-Type"].startswith("application/json")


def test_attempts_are_recorded_for_every_try_not_only_the_last() -> None:
    """§3.3: a retry is a new attempt on the same request, and the evidence must show all of them."""
    recorder = Recorder(httpx.Response(500), ok())
    lane = lane_for(recorder, sleeps=[])
    outcome = lane.post([{}])
    assert [a.attempt_no for a in outcome.attempts] == [1, 2]
    assert [a.http_status for a in outcome.attempts] == [500, 200]
    assert outcome.attempts[0].outcome == "RETRY" and outcome.attempts[1].outcome == "OK"
