"""Process-local operational heartbeat, without URLs, bodies or credentials."""

import threading
from datetime import UTC, datetime

_lock = threading.Lock()
_hosts = {}


def event(host, state, **fields):
    now = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    with _lock:
        value = _hosts.setdefault(host, {})
        value.update(state=state, updated_at=now, **fields)
        if state == "REQUEST":
            value["last_attempt_started_at"] = now
        elif state == "SUCCESS":
            value["last_successful_request"] = now


def snapshot():
    with _lock:
        return {host: dict(value) for host, value in _hosts.items()}
