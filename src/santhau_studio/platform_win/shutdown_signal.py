r"""Installer→GUI shutdown signal (marker file).

Restart Manager did not close this Qt app during a silent uninstall even with every deployed file
registered, and Inno's Pascal script has no OpenEvent, so the two sides share a file instead:
the installer writes `%LOCALAPPDATA%\SanThauDataStudio\shutdown.flag`, the GUI polls it and
quits before files are replaced. Works for silent and interactive setup, and leaves no event
object behind to reason about after a crash.
"""

from __future__ import annotations

import sys

if sys.platform != "win32":  # pragma: no cover - Windows-only product
    raise ImportError("santhau_studio.platform_win.shutdown_signal is Windows-only")

import os
import time
from pathlib import Path

SHUTDOWN_MARKER_ENV = "LOCALAPPDATA"


def shutdown_marker_path() -> Path:
    base = os.environ.get(SHUTDOWN_MARKER_ENV) or os.environ.get("TEMP") or "."
    return Path(base) / "SanThauDataStudio" / "shutdown.flag"


def open_shutdown_event() -> str | None:
    """Each process observes the same signal; only the installer owns its removal."""
    path = shutdown_marker_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return str(path)
    except OSError:
        return None


def shutdown_requested(marker: str | None) -> bool:
    if not marker:
        return False
    try:
        # A crashed installer must not prevent launching forever.
        return 0 <= time.time() - Path(marker).stat().st_mtime < 300
    except OSError:
        return False


def close_shutdown_event(marker: str | None) -> None:
    del marker  # another GUI or worker may not have observed the shared signal yet


def signal_shutdown() -> bool:
    """Write the marker (tests/tools); the installer does the same in Pascal."""
    path = shutdown_marker_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("1", encoding="utf-8")
        return True
    except OSError:
        return False
