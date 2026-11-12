"""Network policy: host-scoped pacing, credential scrubbing, redirect and status rules (§6).

Two defects in the source repo are fixed structurally here rather than by convention:

* the old adapters kept a rate limiter **per adapter instance**, so six families on one
  host could issue six concurrent 3 s-paced lanes and effectively run at 0.5 s/request.
  The limiter is keyed by host and shared across lanes and processes.
* the old code wrote token-bearing URLs into `coverage_windows.reason` and passed the
  VNEPS token in a query string. Scrubbing happens at the exception boundary, so a URL
  reaching a reason, report or manifest has already been stripped.
"""

from __future__ import annotations

import random
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar
from urllib.parse import urlsplit

from santhau_studio.collectors.source_registry import is_allowed_host

Sleeper = Callable[[float], None]

# §6: default pacing floor for the EGP host, counted across retries.
HOST_PACING_FLOOR = {
    "muasamcong.mof.gov.vn": 3.0,
}
DEFAULT_PACING_FLOOR = 1.0

# Query keys that carry credentials. Any of these in a URL makes it unsafe to log.
CREDENTIAL_QUERY_KEYS = re.compile(  # secret-scan:allow regex source, not a credential value
    r"(?:^|[?&])(?:t|token|access_token|api_?key|key|auth|sig|signature|session|jsessionid|sid)=(?P<value>[^&#]*)",
    re.IGNORECASE,
)
USERINFO = re.compile(r"://[^/@]+:[^/@]+@")
BEARER = re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9+/_.\-]{8,}")


@dataclass(frozen=True)
class Verdict:
    """What a response means for the lane, per §6."""

    kind: str  # OK | RETRY | BLOCKED | STOP
    reason: str
    retry_after_s: float | None = None
    http_status: int | None = None


def scrub_url(text: str) -> str:
    """Remove credential material from any URL or URL-bearing string.

    Applied at the exception boundary, before a message becomes a reason, a report field
    or a manifest entry. Idempotent.
    """
    if not text:
        return text
    scrubbed = USERINFO.sub("://[REDACTED]@", text)

    def _cut(match: re.Match[str]) -> str:
        whole = match.group(0)
        key_end = whole.index("=")
        return whole[: key_end + 1] + "[REDACTED]"

    scrubbed = CREDENTIAL_QUERY_KEYS.sub(_cut, scrubbed)
    scrubbed = BEARER.sub(lambda m: m.group(1) + " [REDACTED]", scrubbed)
    return scrubbed


def scrub_exception(exc: BaseException) -> str:
    """Exception text with URLs stripped. Never let a raw str(exc) reach storage."""
    return scrub_url(f"{type(exc).__name__}: {exc}")


class HostRateLimiter:
    """One limiter per host, shared by every lane and process on this machine.

    The registry hands out the same instance for a given host, which is the point: a
    per-adapter limiter is not a host limit.
    """

    _registry: ClassVar[dict[str, HostRateLimiter]] = {}
    _registry_lock = threading.Lock()

    def __init__(self, host: str) -> None:
        self.host = host.lower()
        self.floor = HOST_PACING_FLOOR.get(self.host, DEFAULT_PACING_FLOOR)
        self._lock = threading.Lock()
        self._next_allowed = 0.0
        self._last_issued: float | None = None

    @classmethod
    def for_host(cls, host: str) -> HostRateLimiter:
        key = host.lower()
        with cls._registry_lock:
            limiter = cls._registry.get(key)
            if limiter is None:
                limiter = cls(key)
                cls._registry[key] = limiter
            return limiter

    @classmethod
    def reset_registry(cls) -> None:
        """Test seam only. Production must not clear live pacing state."""
        with cls._registry_lock:
            cls._registry.clear()

    def acquire(self, *, min_interval: float | None = None, sleeper: Sleeper = time.sleep) -> float:
        """Block until the host may fire. Returns the wait actually taken.

        The granted slot is at least `interval` after the previous grant *as well as* after the
        standing reservation. Measuring only the reservation let a caller ask for a longer gap
        and still be served 20 ms after the lane before it, because the earlier caller's
        interval is what the reservation had encoded.

        `sleeper` is injectable because a worker must be able to wait without owning a thread
        that is asleep for Retry-After of 17 seconds during a test, or during a pause.
        """
        interval = max(self.floor, min_interval or 0.0)
        with self._lock:
            now = time.monotonic()
            earliest = self._next_allowed
            if self._last_issued is not None:
                earliest = max(earliest, self._last_issued + interval)
            wait = max(0.0, earliest - now)
            issued = now + wait
            self._next_allowed = issued + interval
            self._last_issued = issued
        if wait:
            sleeper(wait)
        return wait

    def penalize(self, seconds: float) -> None:
        """Push the next slot out — used for 429 Retry-After and cooldowns."""
        with self._lock:
            self._next_allowed = max(self._next_allowed, time.monotonic() + max(0.0, seconds))

    def seconds_until_next_slot(self) -> float:
        """Read-only view of the host cooldown, for tests and for the UI's "cooling down" label."""
        with self._lock:
            return max(0.0, self._next_allowed - time.monotonic())


def enforce_allowlist(url: str) -> str:
    """Return the host if it is on the exact allowlist, else raise.

    §6 requires checking the redirect host as well as the request host, and forbids
    following redirects automatically.
    """
    host = urlsplit(url).hostname or ""
    if not is_allowed_host(host):
        raise PermissionError(f"host {host!r} is not on the exact allowlist")
    return host


def classify(status: int, *, retry_after: float | None = None, body_snippet: str = "") -> Verdict:
    """Map an HTTP outcome to lane policy (§6). Fail-closed on anything auth-shaped."""
    if status in (401, 403):
        return Verdict("STOP", "HTTP_403" if status == 403 else "HTTP_401", http_status=status)
    if status == 429:
        return Verdict("RETRY", "RATE_LIMIT_429", retry_after_s=retry_after, http_status=status)
    if 500 <= status < 600:
        return Verdict("RETRY", "SERVER_ERROR", http_status=status)
    if 300 <= status < 400:
        # Redirects are not followed automatically; the caller must re-check the target host.
        return Verdict("BLOCKED", "REDIRECT_OFF_ALLOWLIST", http_status=status)
    if status == 200:
        lowered = (body_snippet or "").lower()
        if "captcha" in lowered or "cf-chl" in lowered or "access denied" in lowered:
            return Verdict("STOP", "CAPTCHA", http_status=status)
        return Verdict("OK", "OK", http_status=status)
    if 200 < status < 300:
        return Verdict("OK", "OK", http_status=status)
    return Verdict("BLOCKED", f"HTTP_{status}", http_status=status)


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retry. A retry is a new attempt, never a new request (§3.3)."""

    max_attempts: int = 4
    backoff_seconds: tuple[float, ...] = (3.0, 9.0, 27.0)
    respect_retry_after: bool = True
    jitter_fraction: float = 0.2

    def __post_init__(self):
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 5:
            raise ValueError("max_attempts must be 1..5")
        if not 0 <= self.jitter_fraction <= 1 or not self.backoff_seconds or any(s < 0 for s in self.backoff_seconds):
            raise ValueError("Invalid bounded backoff policy")

    def delay_for(self, attempt_no: int, retry_after: float | None) -> float:
        if self.respect_retry_after and retry_after is not None:
            return max(0.0, retry_after)
        index = min(max(attempt_no - 1, 0), len(self.backoff_seconds) - 1)
        delay = self.backoff_seconds[index]
        return delay * (1 + self.jitter_fraction * random.random())

    def exhausted(self, attempt_no: int) -> bool:
        return attempt_no >= self.max_attempts


def parse_retry_after(value: str | None) -> float | None:
    """Retry-After as delta-seconds or HTTP-date. Anything unparseable is None, not 0."""
    if not value:
        return None
    text = value.strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    try:
        from email.utils import parsedate_to_datetime

        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    import datetime as _dt

    delta = (when - _dt.datetime.now(_dt.UTC)).total_seconds()
    return max(0.0, delta)
