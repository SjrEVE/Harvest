"""Vietnamese presentation must never change protocol values or original raw content."""
from pathlib import Path

from PySide6.QtQml import QJSEngine
from tests.integration.test_desktop_qml import _qt_app


def test_vietnamese_labels_keep_protocol_values_and_raw_bytes():
    _qt_app()
    js = QJSEngine()
    source = Path(__file__).parents[2] / "src/santhau_studio/desktop/qml/UiText.js"
    loaded = js.evaluate(source.read_text(encoding="utf-8").replace(".pragma library", ""))
    assert not loaded.isError(), loaded.toString()
    assert js.evaluate('options(["INCREMENTAL"])[0].value').toString() == "INCREMENTAL"
    assert js.evaluate('options(["INCREMENTAL"])[0].label').toString() == "Cập nhật tự động đến hiện tại"
    assert js.evaluate('label("UNKNOWN")').toString() == "Chưa xác định"
    assert js.evaluate('label(0)').toString() == "0"
    assert js.evaluate('collectionSpan("2026-10-31T17:00:00Z", "2026-11-03T17:00:00Z", "BACKFILL")').toString() == "01/11/2026 → hết 03/11/2026 (giờ Việt Nam)"
    assert js.evaluate('collectionSpan("", "", "INCREMENTAL")').toString() == "Tự cập nhật đến hiện tại"
    original = '{"title":"Original English from source","price":null}'
    import json
    raw = js.evaluate('rawPreview(' + json.dumps({"sha256":"abc", "verified":True, "text":original}) + ')').toString()
    assert raw.endswith(original)
    assert "Dữ liệu gốc" in raw


def test_localized_selectors_send_values_not_translated_labels():
    _qt_app()
    import tempfile

    from PySide6.QtCore import QObject
    from PySide6.QtQml import QQmlApplicationEngine

    from santhau_studio.desktop.backend import StudioBackend
    from santhau_studio.desktop.bridge import BackendBridge
    with tempfile.TemporaryDirectory() as root:
        engine = QQmlApplicationEngine()
        bridge = BackendBridge(StudioBackend(Path(root)))
        engine.rootContext().setContextProperty("backend", bridge)
        engine.rootContext().setContextProperty("appTitle", "Kiểm tra tiếng Việt")
        qml = Path(__file__).parents[2] / "src/santhau_studio/desktop/qml/Main.qml"
        engine.load(qml.as_uri())
        window = engine.rootObjects()[0]
        for name, index, expected in [("collectionMode",1,"INCREMENTAL"),("pricePresence",2,"UNKNOWN"),("identityAction",4,"UNDO")]:
            selector = window.findChild(QObject, name)
            assert selector is not None
            selector.setProperty("currentIndex", index)
            assert selector.property("currentValue") == expected
            assert selector.property("currentText") != expected
        window.close()
        # QML starts asynchronous reads while loading. They may create jobs/log folders;
        # finish them before TemporaryDirectory removes the isolated Data Root.
        bridge.invalidateReads()
        bridge.pool.waitForDone()
