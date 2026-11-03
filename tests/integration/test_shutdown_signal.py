"""Installer→GUI shutdown signal (RC2-D): a marker file makes the GUI quit before files move."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from santhau_studio.platform_win.shutdown_signal import (
    close_shutdown_event,
    open_shutdown_event,
    shutdown_requested,
    signal_shutdown,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows installer path")


def test_marker_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    marker = open_shutdown_event()
    assert marker and Path(marker).parent.is_dir()
    assert shutdown_requested(marker) is False
    assert signal_shutdown() is True
    assert shutdown_requested(marker) is True
    close_shutdown_event(marker)
    assert shutdown_requested(marker) is False


def test_stale_marker_is_cleared_on_start(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = Path(tmp_path) / "SanThauDataStudio" / "shutdown.flag"
    path.parent.mkdir(parents=True)
    path.write_text("1", encoding="utf-8")
    marker = open_shutdown_event()
    assert shutdown_requested(marker) is False, "a stale marker must not kill the next launch"
