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
    record.setProperty("sourceMode", False)
    record.findChild(QObject, "recordSection").setProperty("currentIndex", 4)
    explorer.setProperty("state", engine.toScriptValue({"hits": [{"record_id": 1, "natural_key": "IB-BINDING", "family": "TBMT"}],
                                                       "next_cursor": "cursor-next"}))
    explorer.setProperty("busy", False)
    app.processEvents()
    labels = [o.property("text") for o in record.findChildren(QObject)]
    next_page = next(o for o in explorer.findChildren(QObject) if o.property("text") == "Trang sau")
    try:
        assert any(isinstance(t, str) and "IB-BINDING" in t for t in labels)
        count = record.findChild(QObject, "recordHistoryCount").property("text")
        assert "1 dòng" in count and "37 dòng" in count
        assert next_page.property("enabled") is True
        assert window.findChild(QObject, "searchResults").property("count") == 1
    finally:
        window.close()
        bridge.pool.waitForDone()


def test_collection_labels_hide_terminal_progress_and_show_snapshot_work(tmp_path):
    app = _qt_app()
    engine = QQmlApplicationEngine()
    bridge = BackendBridge(StudioBackend(tmp_path))
    engine.rootContext().setContextProperty("backend", bridge)
    engine.rootContext().setContextProperty("appTitle", "Kiểm tra trạng thái")
    qml = Path(__file__).parents[2] / "src/santhau_studio/desktop/qml/Main.qml"
    engine.load(qml.as_uri())
    window = engine.rootObjects()[0]
    bridge.pool.waitForDone()
    app.processEvents()
    page = window.findChild(QObject, "collectionPage")
    try:
        for lifecycle in ("RUNNING", "TERMINATED", "PAUSED"):
            page.setProperty("state", engine.toScriptValue({"worker": {"running":True,"pipe_ok":True,
                "runtime":{"phase":"PUBLISHING_SNAPSHOT" if lifecycle=="RUNNING" else "IDLE"}},
                "active_status": {
                "job_id": "offline-status", "runs": [{"lifecycle": lifecycle, "status": "PARTIAL"}],
                "progress": {"family": "TBMT", "rows": 9876, "pages": 12, "state": "PUBLISHING_SNAPSHOT"}}}))
            app.processEvents()
            label = window.findChild(QObject,"collectionStateText").property("text")
            assert ("Đang cập nhật dữ liệu hiển thị" in label) == (lifecycle == "RUNNING")
            progress = window.findChild(QObject,"collectionLiveProgress")
            assert not progress.property("visible")
    finally:
        window.close()
        bridge.pool.waitForDone()


def test_workspace_status_and_queue_follow_live_collection_replies(tmp_path):
    """Returning to the workspace must not show the startup worker/queue snapshot."""
    app = _qt_app()
    engine = QQmlApplicationEngine()
    bridge = BackendBridge(StudioBackend(tmp_path))
    engine.rootContext().setContextProperty("backend", bridge)
    engine.rootContext().setContextProperty("appTitle", "Workspace status test")
    engine.load((Path(__file__).parents[2] / "src/santhau_studio/desktop/qml/Main.qml").as_uri())
    window = engine.rootObjects()[0]
    bridge.pool.waitForDone()
    app.processEvents()
    window.setProperty("pageIndex", 0)
    bridge.pool.waitForDone()
    app.processEvents()
    page = window.findChild(QObject, "collectionPage")
    try:
        for running, connected, phase, queued, expected in (
            (True, True, "COLLECTING", 2, "Đang thu thập dữ liệu"),
            (True, True, "PUBLISHING_SNAPSHOT", 1, "Đang cập nhật dữ liệu hiển thị"),
            (True, True, "IDLE", 0, "Sẵn sàng · chưa có tác vụ chạy"),
            (False, False, "", 0, "Đã tắt bộ thu thập"),
            (True, False, "", 3, "Chưa kết nối được bộ thu thập"),
        ):
            page.setProperty("state", engine.toScriptValue({
                "worker": {"running": running, "pipe_ok": connected, "runtime": {"phase": phase}},
                "active": [], "queued": [{}] * queued, "done": [],
                "counts": {"running": int(phase == "COLLECTING"), "queued": queued, "paused": 0},
            }))
            app.processEvents()
            labels = [o.property("text") for o in window.findChildren(QObject)]
            assert "Bộ thu thập: " + expected in labels
            assert any(isinstance(t, str) and t.startswith("Kho dữ liệu:")
                       and t.endswith("Tác vụ đang chờ: " + str(queued)) for t in labels)
        page.setProperty("lastReplyMs", 1)
        app.processEvents()
        labels = [o.property("text") for o in window.findChildren(QObject)]
        assert "Bộ thu thập: Chưa nhận được trạng thái mới" in labels
        assert any(isinstance(t, str) and t.startswith("Kho dữ liệu:")
                   and t.endswith("Tác vụ đang chờ: Chưa xác nhận") for t in labels)
    finally:
        window.close()
        bridge.pool.waitForDone()
