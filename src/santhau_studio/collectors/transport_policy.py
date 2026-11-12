"""Finite network limits and conservative transport-error classification."""

import math
import ssl

import httpx


def request_timeout(connect_seconds=15.0, remaining=None):
    if (
        type(connect_seconds) not in (int, float)
        or not math.isfinite(connect_seconds)
        or not 15 <= connect_seconds <= 20
    ):
        raise ValueError("connect_timeout_seconds must be 15..20")
    limits = {"connect": float(connect_seconds), "read": 30.0, "write": 30.0, "pool": 5.0}
    if remaining is not None:
        if remaining <= 0:
            raise TimeoutError("REQUEST_DEADLINE_EXCEEDED")
        limits = {key: min(value, remaining) for key, value in limits.items()}
    return httpx.Timeout(**limits)


def transport_category(exc):
    chain, seen = [], set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    text = " ".join(str(e).lower() for e in chain)
    if any(isinstance(e, ssl.SSLCertVerificationError) for e in chain) or any(
        t in text
        for t in (
            "certificate_verify_failed",
            "certificate verify failed",
            "hostname mismatch",
            "hostname doesn't match",
        )
    ):
        return "TLS_CERTIFICATE_INVALID", False
    if any(isinstance(e, ssl.SSLEOFError) for e in chain) or any(
        t in text for t in ("unexpected_eof_while_reading", "eof occurred in violation of protocol")
    ):
        return "TLS_EOF_TRANSIENT", True
    if any(isinstance(e, ssl.SSLError) for e in chain) or "[ssl:" in text:
        return "TLS_UNCLASSIFIED", False
    if isinstance(exc, httpx.TimeoutException):
        return type(exc).__name__, True
    if isinstance(
        exc, (httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError)
    ):
        return type(exc).__name__, True
    return type(exc).__name__, False
