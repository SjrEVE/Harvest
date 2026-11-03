"""Child Qt controls have their own state property; data must bind to the page."""
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtQml import QQmlApplicationEngine
from tests.integration.test_desktop_qml import _qt_app

from santhau_studio.desktop.backend import StudioBackend
from santhau_studio.desktop.bridge import BackendBridge


def test_record_labels_and_search_pagination_use_page_data(tmp_path):
    app = _qt_app()
    engine = QQmlApplicationEngine()
    bridge = BackendBridge(StudioBackend(tmp_path))
    engine.rootContext().setContextProperty("backend", bridge)
    engine.rootContext().setContextProperty("appTitle", "Kiểm tra dữ liệu")
    qml = Path(__file__).parents[2] / "src/santhau_studio/desktop/qml/Main.qml"
    engine.load(qml.as_uri())
    window = engine.rootObjects()[0]
    bridge.pool.waitForDone()
    app.processEvents()
    record = next(o for o in window.findChildren(QObject) if o.metaObject().className().startswith("RecordPage"))
    explorer = next(o for o in window.findChildren(QObject) if o.metaObject().className().startswith("ExplorerPage"))
    record.setProperty("state", engine.toScriptValue({"record": {"family": "TBMT", "natural_key": "IB-BINDING"},
                                                     "history_counts": {"versions": 37}}))
    record.setProperty("history", engine.toScriptValue({"rows": [{}]}))
    explorer.setProperty("state", engine.toScriptValue({"hits": [{"record_id": 1, "natural_key": "IB-BINDING", "family": "TBMT"}],
                                                       "next_cursor": "cursor-next"}))
    explorer.setProperty("busy", False)
    app.processEvents()
    labels = [o.property("text") for o in record.findChildren(QObject)]
    next_page = next(o for o in explorer.findChildren(QObject) if o.property("text") == "Trang sau")
    try:
        assert any(isinstance(t, str) and "IB-BINDING" in t for t in labels)
        assert "1 dòng / 37" in labels
        assert next_page.property("enabled") is True
        assert window.findChild(QObject, "searchResults").property("count") == 1
    finally:
        window.close()
        bridge.pool.waitForDone()
