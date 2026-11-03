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
from pathlib import Path

SHUTDOWN_MARKER_ENV = "LOCALAPPDATA"


def shutdown_marker_path() -> Path:
    base = os.environ.get(SHUTDOWN_MARKER_ENV) or os.environ.get("TEMP") or "."
    return Path(base) / "SanThauDataStudio" / "shutdown.flag"


def open_shutdown_event() -> str | None:
    """Compatibility name: clears any stale marker and returns the path the GUI should poll."""
    path = shutdown_marker_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        return str(path)
    except OSError:
        return None


def shutdown_requested(marker: str | None) -> bool:
    return bool(marker) and Path(marker).exists()


def close_shutdown_event(marker: str | None) -> None:
    if marker:
        Path(marker).unlink(missing_ok=True)


def signal_shutdown() -> bool:
    """Write the marker (tests/tools); the installer does the same in Pascal."""
    path = shutdown_marker_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("1", encoding="utf-8")
        return True
    except OSError:
        return False
