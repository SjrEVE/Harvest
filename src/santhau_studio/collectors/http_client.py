"""The one HTTP lane: shared client, host-scoped pacing, bounded retry, fail-closed policy (§6).

The source repo spread these decisions across `adapters/base.py` and each adapter. Here they
are in one place so a lane cannot accidentally invent its own retry or pacing rule.

Structural differences from the source behaviour, each with a reason:

* **one limiter per host, not per adapter instance.** Six families share `muasamcong.mof.gov.vn`;
  a per-instance 3 s floor is effectively 0.5 s/request and that is what gets an operator's IP
  pinned by the WAF.
* **redirects are never followed.** The check that matters is "did we send our credentials to a
  host nobody approved" (FAULT-11), and the only way to be sure is to not make the second request.
* **cookies are discarded after every response.** This channel is token-free and session-free by
  contract; an accumulated cookie is a session even if nobody meant to open one.
* **TLS keeps the platform security floor.** A source with incompatible TLS stays blocked;
  the collector never lowers OpenSSL's security level to make a canary pass.
"""

from __future__ import annotations

import re
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from santhau_studio.collectors.deadline import RequestDeadlineExceeded, until_deadline
from santhau_studio.collectors.network_policy import (
    HostRateLimiter,
    RetryPolicy,
    Verdict,
    classify,
    enforce_allowlist,
    parse_retry_after,
    scrub_exception,
    scrub_url,
)
from santhau_studio.collectors.network_status import event as network_event
from santhau_studio.collectors.transport_policy import request_timeout, transport_category
from santhau_studio.platform_win.host_gate import HostRequestGate

TIMEOUT = request_timeout()
USER_AGENT = "SanThauDataStudio/0.0.1"

_BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "vi-VN,vi;q=0.9",
    "Content-Type": "application/json;charset=UTF-8",
}


class AccessBlocked(RuntimeError):
    """The source refused access (401/403/CAPTCHA). Never retried, never faked (§6)."""

    def __init__(self, message, *, attempt_log=()):
        super().__init__(message)
        self.attempt_log = tuple(attempt_log)


class FetchFailed(RuntimeError):
    """Bounded retries exhausted, or the response was not a usable JSON object."""

    def __init__(self, message: str, *, verdict: Verdict | None = None, attempts: int = 0, attempt_log=()) -> None:
        super().__init__(message)
        self.verdict = verdict
        self.attempts = attempts
        self.attempt_log = tuple(attempt_log)


class InsecureRequest(RuntimeError):
    """Refused before sending: wrong scheme, host off the allowlist, or credential material."""


# The one sanctioned query string in the whole collector (FAULT-11 exception).
# The portal's `expose/` endpoints return HTTP 400 unless a `token=` query
# parameter is present; the server does NOT validate its value — the M1 canary
# (2026-11-13) got HTTP 200 with identical payloads for both a dummy value and
# an empty value, and HTTP 400 with no parameter at all. The collector therefore
# sends the parameter with an EMPTY value: presence without credential material.
# Any other query string stays refused by _check_url; this pattern admits only
# `key=` / `key=value` with a plain ASCII key and an unreserved value.
QUERY_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]*=[A-Za-z0-9._~-]*")


def validate_query(query: str | None) -> str | None:
    """Return the query string if it matches the sanctioned pattern, else refuse."""
    if query is None:
        return None
    if not isinstance(query, str) or QUERY_PATTERN.fullmatch(query) is None:
        raise InsecureRequest("refusing non-sanctioned query string")
    return query


@dataclass
class Attempt:
    """One HTTP try. Retries add attempts, never a new logical request (§3.3)."""

    attempt_no: int
    started_at_utc: str
    http_status: int | None = None
    outcome: str = "OK"
    error_kind: str | None = None
    elapsed_seconds: float = 0.0
    wait_seconds: float = 0.0
    byte_len: int | None = None
    finished_at_utc: str | None = None


@dataclass
class FetchOutcome:
    payload: Any
    verdict: Verdict
    attempts: list[Attempt] = field(default_factory=list)
    http_status: int | None = None
    byte_len: int = 0

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)


def build_ssl_context(host: str) -> ssl.SSLContext:
    """Verified TLS >= 1.2 for every host, including the procurement portal."""
    del host
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # The portal can select finite-field DHE with an undersized group. Offer modern
    # ECDHE AEAD suites instead, without lowering the security level or certificate
    # checks. TLS 1.3 suites remain managed by OpenSSL.
    context.set_ciphers("ECDHE+AESGCM:ECDHE+CHACHA20")
    return context


def portal_headers(host: str) -> dict[str, str]:
    headers = dict(_BASE_HEADERS)
    if host.lower() == "muasamcong.mof.gov.vn":
        origin = f"https://{host}"
        headers["Origin"] = origin
        headers["Referer"] = f"{origin}/web/guest/contractor-selection"
    return headers


class FetchLane:
    """Posts JSON to one allowlisted endpoint and applies §6 policy around it."""

    def __init__(
        self,
        *,
        url: str,
        source_key: str,
        client: httpx.Client | None = None,
        retry: RetryPolicy | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        pacing_sleeper: Callable[[float], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        budget=None,
        connect_timeout_seconds: float = 15.0,
        query: str | None = None,
    ) -> None:
        self.url = url
        self.query = validate_query(query)
        self.source_key = source_key
        self.host = self._check_url(url)
        self.retry = retry or RetryPolicy()
        self.limiter = HostRateLimiter.for_host(self.host)
        self._sleeper = sleeper
        # Pacing and backoff are different waits with different meanings, so they are injected
        # separately: a test that records backoff must not have pacing waits mixed into it.
        self._pacing_sleeper = pacing_sleeper or sleeper
        self._clock = clock
        self.budget = budget
        self.connect_timeout_seconds = connect_timeout_seconds
        configured_timeout = request_timeout(connect_timeout_seconds)
        self._own_client = client is None
        self._client = client or httpx.Client(
            headers=portal_headers(self.host),
            timeout=configured_timeout,
            follow_redirects=False,  # see module docstring: no second request, no leaked credential
            verify=build_ssl_context(self.host),
        )
        self._lock = threading.Lock()

    @staticmethod
    def _check_url(url: str) -> str:
        parts = urlsplit(url)
        if parts.scheme != "https":
            raise InsecureRequest(f"refusing non-HTTPS endpoint: {scrub_url(url)}")
        if parts.query or parts.fragment:
            raise InsecureRequest(f"endpoint must carry no query string: {scrub_url(url)}")
        try:
            return enforce_allowlist(url)
        except PermissionError as exc:
            raise InsecureRequest(scrub_exception(exc)) from exc

    @property
    def client(self) -> httpx.Client:
        return self._client

    def close(self) -> None:
        if self._own_client:
            self._client.close()

    def __enter__(self) -> FetchLane:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _post_bounded(self, body):
        self._request_started = False
        remaining = self.budget.remaining_seconds if self.budget else None
        timeout = request_timeout(self.connect_timeout_seconds, remaining)

        def send(cancelled):
            def post():
                if cancelled.is_set():
                    raise RequestDeadlineExceeded("REQUEST_DEADLINE_EXCEEDED")
                if self.budget:
                    self.budget.consume()
                network_event(self.host, "REQUEST", source_key=self.source_key)
                self._request_started = True
                request_url = self.url + ("?" + self.query if self.query else "")
                return self._client.post(request_url, json=body, timeout=timeout)
            if self._own_client:
                network_event(self.host, "HOST_WAIT")
                gate_options = {"budget": self.budget} if self.budget is not None else {}
                with HostRequestGate(self.host, max(self.limiter.floor, self._min_interval), **gate_options) as gate:
                    try:
                        response = post()
                    except httpx.HTTPError as exc:
                        if transport_category(exc)[1] and self.retry.exhausted(self._attempt_no):
                            gate.defer(60)
                        raise
                    if response.status_code == 429:
                        gate.defer(parse_retry_after(response.headers.get("Retry-After")))
                    elif response.status_code >= 500 and self.retry.exhausted(self._attempt_no):
                        gate.defer(60)
                    return response
            return post()

        response = until_deadline(send, remaining, self.close if self._own_client else None)
        if self.budget:
            self.budget.check_time()
        return response

    def post(self, body: Any, *, min_interval: float | None = None) -> FetchOutcome:
        """POST with bounded retry. Raises AccessBlocked / FetchFailed; never returns a fake body."""
        attempts: list[Attempt] = []
        last_error: str | None = None
        last_verdict: Verdict | None = None
        for attempt_no in range(1, self.retry.max_attempts + 1):
            self._attempt_no = attempt_no
            if self.budget:
                self.budget.check()
            record = Attempt(attempt_no=attempt_no, started_at_utc=_now_utc())
            network_event(self.host, "PACING", attempt=attempt_no)
            started = self._clock()
            wait = self._pacing_sleeper if not self.budget else lambda seconds: self.budget.sleep(seconds, self._pacing_sleeper)
            record.wait_seconds = self.limiter.acquire(min_interval=min_interval, sleeper=wait)
            try:
                self._min_interval = min_interval or 0.0
                response = self._post_bounded(body)
            except (RequestDeadlineExceeded, FetchFailed) as exc:
                record.outcome, record.error_kind = "ERROR", ("STOP_REQUESTED" if "STOP_REQUESTED" in str(exc) else ("REQUEST_DEADLINE_EXCEEDED" if "DEADLINE" in str(exc) else "REQUEST_BUDGET_EXHAUSTED"))
                record.elapsed_seconds = self._clock() - started
                record.finished_at_utc = _now_utc()
                network_event(self.host, "DEADLINE", error_category=record.error_kind)
                if self._request_started:
                    attempts.append(record)
                raise FetchFailed(str(exc), attempt_log=attempts) from exc
            except httpx.HTTPError as exc:
                # Transport failure: bounded retry, but the message is scrubbed before it can
                # become a reason string that outlives this call.
                last_error = scrub_exception(exc)
                record.outcome = "ERROR"
                record.error_kind, retryable = transport_category(exc)
                network_event(self.host, "TRANSPORT_ERROR", error_category=record.error_kind)
                record.elapsed_seconds += self._clock() - started
                record.finished_at_utc = _now_utc()
                attempts.append(record)
                if not retryable:
                    raise FetchFailed(f"{self.source_key}: {record.error_kind}",
                        verdict=Verdict("BLOCKED", record.error_kind), attempt_log=attempts) from exc
                if self.retry.exhausted(attempt_no):
                    if self._own_client:
                        self.limiter.penalize(60)
                    raise FetchFailed(
                        f"{self.source_key}: {self.retry.max_attempts} attempts failed, last {last_error}",
                        verdict=last_verdict,
                        attempts=attempt_no, attempt_log=attempts,
                    ) from exc
                self._backoff(self.retry.delay_for(attempt_no, None), attempts)
                continue
            finally:
                # A cookie jar that fills up is an unintended session on a session-free channel.
                self._client.cookies.clear()

            record.http_status = response.status_code
            record.finished_at_utc = _now_utc()
            record.byte_len = len(response.content)
            record.elapsed_seconds += self._clock() - started
            verdict = classify(
                response.status_code,
                retry_after=parse_retry_after(response.headers.get("Retry-After")),
                body_snippet=response.text[:2000],
            )
            last_verdict = verdict
            if verdict.kind == "STOP":
                network_event(self.host, "BLOCKED", error_category=verdict.reason)
                record.outcome = "BLOCKED"
                record.error_kind = verdict.reason
                attempts.append(record)
                raise AccessBlocked(f"{self.source_key} refused access ({verdict.reason})", attempt_log=attempts)
            if verdict.kind == "BLOCKED":
                network_event(self.host, "BLOCKED", error_category=verdict.reason)
                record.outcome = "BLOCKED"
                record.error_kind = verdict.reason
                attempts.append(record)
                raise FetchFailed(
                    f"{self.source_key}: {verdict.reason} (HTTP {response.status_code})",
                    verdict=verdict,
                    attempts=attempt_no, attempt_log=attempts,
                )
            if verdict.kind == "RETRY":
                network_event(self.host, "HTTP_RETRYABLE", error_category=verdict.reason)
                record.outcome = "RETRY"
                record.error_kind = verdict.reason
                attempts.append(record)
                delay = self.retry.delay_for(attempt_no, verdict.retry_after_s)
                if verdict.retry_after_s:
                    # Back off the *host*: every lane sharing it must respect the same signal.
                    self.limiter.penalize(verdict.retry_after_s)
                if self.retry.exhausted(attempt_no):
                    raise FetchFailed(
                        f"{self.source_key}: {verdict.reason} persisted over {attempt_no} attempts",
                        verdict=verdict,
                        attempts=attempt_no, attempt_log=attempts,
                    )
                self._backoff(delay, attempts)
                continue

            try:
                payload = response.json()
            except ValueError as exc:
                network_event(self.host, "MALFORMED", error_category="NOT_JSON")
                record.outcome = "MALFORMED"
                record.error_kind = "NOT_JSON"
                attempts.append(record)
                raise FetchFailed(
                    f"{self.source_key}: response is not JSON ({type(exc).__name__})",
                    verdict=verdict,
                    attempts=attempt_no, attempt_log=attempts,
                ) from exc
            if not isinstance(payload, (dict, list)):
                network_event(self.host, "MALFORMED", error_category="SCHEMA_MISMATCH")
                record.outcome = "MALFORMED"
                record.error_kind = "SCHEMA_MISMATCH"
                attempts.append(record)
                raise FetchFailed(f"{self.source_key}: JSON payload is not an object or array", attempt_log=attempts)
            record.outcome = "OK"
            network_event(self.host, "SUCCESS", error_category=None)
            attempts.append(record)
            return FetchOutcome(payload=payload, verdict=verdict, attempts=attempts,
                                http_status=response.status_code, byte_len=record.byte_len or 0)

        raise FetchFailed(  # pragma: no cover - the loop always returns or raises
            f"{self.source_key}: retry loop exited without an outcome",
            verdict=last_verdict,
            attempts=len(attempts),
        )

    def _backoff(self, delay, attempts):
        network_event(self.host, "BACKOFF", retry_delay_seconds=delay)
        try:
            if self.budget:
                self.budget.sleep(delay, self._sleeper)
            else:
                self._sleeper(delay)
        except FetchFailed as exc:
            network_event(self.host, "BUDGET_EXHAUSTED")
            exc.attempt_log = tuple(attempts)
            raise


def _now_utc() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
