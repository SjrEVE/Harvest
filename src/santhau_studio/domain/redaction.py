"""Deterministic credential removal before raw hashing, extraction and projection."""

from __future__ import annotations

from typing import Any

from santhau_studio.collectors.network_policy import scrub_url

REDACTION_POLICY_VERSION = 2
_PRIVATE_KEYS = frozenset({
    "token", "accesstoken", "refreshtoken", "idtoken", "apikey", "authorization",
    "cookie", "setcookie", "password", "clientsecret", "secret", "sessionid", "jsessionid",
})


def redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if str(key).replace("-", "").replace("_", "").casefold() in _PRIVATE_KEYS
            else redact_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_payload(item) for item in value]
    if isinstance(value, str):
        return scrub_url(value)
    return value
