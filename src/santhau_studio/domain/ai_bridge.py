"""Cross-platform, fail-safe bridge to the Windows-only AI control lane.

Importing workers.ai_control pulls in workers.ipc, which is intentionally
Windows-only. Callers on Linux must keep working with observation disabled.
"""
from __future__ import annotations

import contextlib


def emit(phase: str, **fields) -> None:
    try:
        from santhau_studio.workers import ai_control
    except (ImportError, OSError):
        return
    with contextlib.suppress(Exception):  # observation must never break a worker
        ai_control.emit(phase, **fields)
