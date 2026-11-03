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
* **the legacy TLS workaround is host-scoped.** Lowering the cipher security level globally would
  weaken every other host the app might talk to; the portal's small-DH certificate is the only
  documented reason it exists.
"""

from __future__ import annotations

import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

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

TIMEOUT = httpx.Timeout(30.0, connect=5.0)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Only this host needs the small-DH workaround; everything else keeps the default level.
LEGACY_DH_HOSTS = frozenset({"muasamcong.mof.gov.vn"})

_BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "vi-VN,vi;q=0.9",
    "Content-Type": "application/json;charset=UTF-8",
}


class AccessBlocked(RuntimeError):
    """The source refused access (401/403/CAPTCHA). Never retried, never faked (§6)."""


class FetchFailed(RuntimeError):
    """Bounded retries exhausted, or the response was not a usable JSON object."""

    def __init__(self, message: str, *, verdict: Verdict | None = None, attempts: int = 0) -> None:
        super().__init__(message)
        self.verdict = verdict
        self.attempts = attempts


class InsecureRequest(RuntimeError):
    """Refused before sending: wrong scheme, host off the allowlist, or credential material."""


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
    """Default context, except for the one host whose certificate needs SECLEVEL=1."""
    context = ssl.create_default_context()
    if host.lower() in LEGACY_DH_HOSTS:
        context.set_ciphers("DEFAULT:@SECLEVEL=1")
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
    ) -> None:
        self.url = url
        self.source_key = source_key
        self.host = self._check_url(url)
        self.retry = retry or RetryPolicy()
        self.limiter = HostRateLimiter.for_host(self.host)
        self._sleeper = sleeper
        # Pacing and backoff are different waits with different meanings, so they are injected
        # separately: a test that records backoff must not have pacing waits mixed into it.
        self._pacing_sleeper = pacing_sleeper or sleeper
        self._clock = clock
        self._own_client = client is None
        self._client = client or httpx.Client(
            headers=portal_headers(self.host),
            timeout=TIMEOUT,
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

    def post(self, body: Any, *, min_interval: float | None = None) -> FetchOutcome:
        """POST with bounded retry. Raises AccessBlocked / FetchFailed; never returns a fake body."""
        attempts: list[Attempt] = []
        last_error: str | None = None
        last_verdict: Verdict | None = None
        for attempt_no in range(1, self.retry.max_attempts + 1):
            record = Attempt(attempt_no=attempt_no, started_at_utc=_now_utc())
            started = self._clock()
            record.wait_seconds = self.limiter.acquire(min_interval=min_interval, sleeper=self._pacing_sleeper)
            try:
                response = self._client.post(self.url, json=body)
            except httpx.HTTPError as exc:
                # Transport failure: bounded retry, but the message is scrubbed before it can
                # become a reason string that outlives this call.
                last_error = scrub_exception(exc)
                record.outcome = "ERROR"
                record.error_kind = type(exc).__name__
                record.elapsed_seconds += self._clock() - started
                attempts.append(record)
                if self.retry.exhausted(attempt_no):
                    raise FetchFailed(
                        f"{self.source_key}: {self.retry.max_attempts} attempts failed, last {last_error}",
                        verdict=last_verdict,
                        attempts=attempt_no,
                    ) from exc
                self._sleeper(self.retry.delay_for(attempt_no, None))
                continue
            finally:
                # A cookie jar that fills up is an unintended session on a session-free channel.
                self._client.cookies.clear()

            record.http_status = response.status_code
            record.byte_len = len(response.content)
            record.elapsed_seconds += self._clock() - started
            verdict = classify(
                response.status_code,
                retry_after=parse_retry_after(response.headers.get("Retry-After")),
                body_snippet=response.text[:2000],
            )
            last_verdict = verdict
            if verdict.kind == "STOP":
                record.outcome = "BLOCKED"
                record.error_kind = verdict.reason
                attempts.append(record)
                raise AccessBlocked(f"{self.source_key} refused access ({verdict.reason})")
            if verdict.kind == "BLOCKED":
                record.outcome = "BLOCKED"
                record.error_kind = verdict.reason
                attempts.append(record)
                raise FetchFailed(
                    f"{self.source_key}: {verdict.reason} (HTTP {response.status_code})",
                    verdict=verdict,
                    attempts=attempt_no,
                )
            if verdict.kind == "RETRY":
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
                        attempts=attempt_no,
                    )
                self._sleeper(delay)
                continue

            try:
                payload = response.json()
            except ValueError as exc:
                record.outcome = "MALFORMED"
                record.error_kind = "NOT_JSON"
                attempts.append(record)
                raise FetchFailed(
                    f"{self.source_key}: response is not JSON ({type(exc).__name__})",
                    verdict=verdict,
                    attempts=attempt_no,
                ) from exc
            if not isinstance(payload, (dict, list)):
                record.outcome = "MALFORMED"
                record.error_kind = "SCHEMA_MISMATCH"
                attempts.append(record)
                raise FetchFailed(f"{self.source_key}: JSON payload is not an object or array")
            record.outcome = "OK"
            attempts.append(record)
            return FetchOutcome(payload=payload, verdict=verdict, attempts=attempts,
                                http_status=response.status_code, byte_len=record.byte_len or 0)

        raise FetchFailed(  # pragma: no cover - the loop always returns or raises
            f"{self.source_key}: retry loop exited without an outcome",
            verdict=last_verdict,
            attempts=len(attempts),
        )


def _now_utc() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
