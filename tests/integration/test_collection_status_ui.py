"""The real QML shows current work separately from job history and gates actions."""
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtQml import QQmlApplicationEngine
from test_desktop_qml import _qt_app

from santhau_studio.desktop.backend import StudioBackend
from santhau_studio.desktop.bridge import BackendBridge


def test_status_states_and_controls_are_unambiguous(tmp_path):
    app = _qt_app()
    engine = QQmlApplicationEngine()
    bridge = BackendBridge(StudioBackend(tmp_path))
    engine.rootContext().setContextProperty("backend", bridge)
    engine.rootContext().setContextProperty("appTitle", "Status test")
    engine.load((Path(__file__).parents[2] / "src/santhau_studio/desktop/qml/Main.qml").as_uri())
    window = engine.rootObjects()[0]
    bridge.pool.waitForDone()
    app.processEvents()
    page = window.findChild(QObject, "collectionPage")
    try:
        cases = [
            (False, False, "", "Đã tắt bộ thu thập"),
            (True, False, "", "Chưa kết nối được bộ thu thập"),
            (True, True, "RECOVERING", "Đang kiểm tra kho dữ liệu"),
            (True, True, "PUBLISHING_SNAPSHOT", "Đang cập nhật dữ liệu hiển thị"),
            (True, True, "COLLECTING", "Đang thu thập dữ liệu"),
            (True, True, "PAUSING", "Đang tạm dừng"),
            (True, True, "STOPPING_JOB", "Đang dừng tác vụ"),
            (True, True, "STOPPING_WORKER", "Đang tắt bộ thu thập"),
            (True, True, "IDLE", "Sẵn sàng · chưa có tác vụ chạy"),
        ]
        for running, connected, phase, title in cases:
            page.setProperty("state", engine.toScriptValue({"worker": {"running": running, "pipe_ok": connected,
                "runtime": {"phase": phase}}, "active": [], "queued": [], "done": []}))
            app.processEvents()
            label = window.findChild(QObject, "collectionStateText")
            assert label is not None and label.property("text") == title
        for lifecycle, pause, resume in (("RUNNING", True, False), ("PAUSED", False, True)):
            page.setProperty("state", engine.toScriptValue({"worker": {"running": True, "pipe_ok": True,
                "runtime": {"phase": "COLLECTING" if pause else "IDLE"}},
                "counts": {"running": int(pause), "paused": int(resume)},
                "active": [{"job_id": "test", "lifecycle": lifecycle, "can_pause": pause,
                            "can_resume": resume, "can_stop": True}]}))
            app.processEvents()
            assert window.findChild(QObject, "collectionPause").property("enabled") == pause
            assert window.findChild(QObject, "collectionResume").property("enabled") == resume
        page.setProperty("lastReplyMs", 1)
        app.processEvents()
        assert window.findChild(QObject, "collectionStateText").property("text") == "Chưa nhận được trạng thái mới"
        assert not window.findChild(QObject, "collectionResume").property("enabled")
    finally:
        window.close()
        bridge.pool.waitForDone()
