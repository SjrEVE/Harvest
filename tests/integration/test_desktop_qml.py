"""Milestone F: the QML shell must instantiate headlessly with the real backend attached.

Loading `Main.qml` eagerly creates all ten screens; every page runs its `Component.onCompleted`
loader against a real (empty) data root. A syntax error, a broken binding, or a bridge slot that
raises would leave `rootObjects()` empty or print a QML error and fail the load.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="desktop shell targets Windows")

_APP = None


def _qt_app():
    global _APP
    from PySide6.QtGui import QGuiApplication

    _APP = QGuiApplication.instance() or QGuiApplication([])
    return _APP


def test_qml_shell_instantiates_with_backend(tmp_path: Path) -> None:
    _qt_app()
    from PySide6.QtQml import QQmlApplicationEngine

    from santhau_studio.desktop.backend import StudioBackend
    from santhau_studio.desktop.bridge import BackendBridge

    engine = QQmlApplicationEngine()
    bridge = BackendBridge(StudioBackend(tmp_path / "data"))
    engine.rootContext().setContextProperty("backend", bridge)
    engine.rootContext().setContextProperty("appTitle", "Săn Thầu Data Studio (offline test)")
    qml = Path(__file__).resolve().parents[2] / "src" / "santhau_studio" / "desktop" / "qml" / "Main.qml"
    engine.addImportPath(str(qml.parent))
    engine.load(qml.as_uri())
    assert engine.rootObjects(), "QML failed to load"
    root = engine.rootObjects()[0]
    assert int(root.property("pageIndex")) == 0
    assert int(root.property("detailRecordId")) == 0


def test_bridge_slots_return_json_for_every_screen(tmp_path: Path) -> None:
    _qt_app()
    from santhau_studio.desktop.backend import StudioBackend
    from santhau_studio.desktop.bridge import BackendBridge

    bridge = BackendBridge(StudioBackend(tmp_path / "data"))
    payloads = [
        bridge.onboarding(), bridge.controlCenter(), bridge.sources(), bridge.jobs(),
        bridge.coverage(""), bridge.search('{"text": "abc"}'), bridge.recordDetail(0),
        bridge.deepQueue(), bridge.integrity(), bridge.settings(),
        bridge.jobStatus("job-absent"), bridge.workerCommand("PAUSE", ""),
    ]
    import json

    for payload in payloads:
        json.loads(payload)
