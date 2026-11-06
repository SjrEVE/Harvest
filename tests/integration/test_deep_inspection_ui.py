import json
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtQml import QJSEngine, QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow  # noqa: F401 - register the concrete window wrapper
from PySide6.QtTest import QTest
from test_desktop_qml import _qt_app
from test_public_deep import hsmt
from test_public_deep_refresh import capture, warehouse  # noqa: F401

from santhau_studio.desktop.history import history

QML = Path(__file__).parents[2] / "src/santhau_studio/desktop/qml"


class Bridge(QObject):
    readReady = Signal(str, int, str)  # noqa: N815

    def __init__(self):
        super().__init__()
        self.calls = []

    @Slot(str, str, str, result=int)
    def requestRead(self, key, method, payload):  # noqa: N802
        self.calls.append((key, method, json.loads(payload)))
        return len(self.calls)


def component(name, bridge):
    app = _qt_app()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("testBridge", bridge)
    engine.loadData(
        (
            f'import QtQuick\nimport QtQuick.Window\nimport "{QML.as_uri()}"\n'
            f'Window {{ width:860; height:544; visible:true; {name} {{ objectName:"subject"; anchors.fill:parent; bridge:testBridge }} }}'
        ).encode(),
        QUrl(),
    )
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    app.processEvents()
    return app, engine, window, window.findChild(QObject, "subject")


def test_goods_history_keeps_parent_lot_visible(warehouse):  # noqa: F811
    capture(
        warehouse,
        hsmt(
            [
                {"id": 1, "lotNo": "PP-01", "lotName": "Lô thiết bị"},
                {"parent": 1, "name": "Máy đo", "qty": 0, "uom": "bộ"},
            ]
        ),
        "HSMT",
    )
    row = history(warehouse.conn, {"record_id": 1, "kind": "goods_rows"})["rows"][0]
    assert row["lot_no_raw"] == "PP-01" and row["lot_label"] == "Lô thiết bị"
    assert row["quantity"] == 0


def test_structured_display_preserves_zero_unknown_roles_and_reference_only():
    _qt_app()
    engine = QJSEngine()
    engine.evaluate(
        (QML / "RecordData.js").read_text(encoding="utf-8").replace(".pragma library", "")
    )
    goods = engine.evaluate(
        'describe("goods_rows", {name:"Máy đo",quantity_presence:"KNOWN",quantity:0,unit:"bộ",lot_no_raw:"PP-01"})'
    ).toString()
    assert "Máy đo" in goods and "0 bộ" in goods and "PP-01" in goods
    unknown = engine.evaluate(
        'describe("goods_rows", {name:"A",quantity_presence:"MISSING"})'
    ).toString()
    assert "Chưa có" in unknown and "undefined" not in unknown
    doc = engine.evaluate(
        'describe("documents", {name:"quyet-dinh.pdf",provenance_json:"{}"})'
    ).toString()
    assert "Chưa tải" in doc and "quyet-dinh.pdf" in doc
    party = engine.evaluate(
        'describe("contractor_memberships", {party_name:"A",role:"OFFEROR"})'
    ).toString()
    assert "Đơn vị chào giá" in party and "trúng thầu" not in party


def test_queue_filters_paging_and_open_record():
    bridge = Bridge()
    app, engine, window, page = component("DeepQueuePage", bridge)
    try:
        page.setProperty(
            "state",
            engine.toScriptValue(
                {
                    "present": True,
                    "rows": [
                        {
                            "source_record_id": 7,
                            "natural_key": "IB-7",
                            "lane": "HSMT",
                            "state": "FAILED",
                            "error": "TIMEOUT",
                        }
                    ],
                    "summary": {"FAILED": 1},
                    "total": 101,
                    "offset": 0,
                    "limit": 50,
                    "has_more": True,
                }
            ),
        )
        page.setProperty("busy", False)
        app.processEvents()
        opened = []
        page.recordRequested.connect(opened.append)
        QTest.qWait(50)
        window.findChild(QObject, "deepEntries").property("currentItem").clicked.emit()
        assert opened == [7]
        next_button = window.findChild(QObject, "deepNext")
        assert next_button.property("enabled")
        next_button.clicked.emit()
        assert bridge.calls[-1][2]["offset"] == 50
        assert not next_button.property("enabled")
        # Read completion unlocks navigation. A changed filter must restart at page 1.
        key, _, _ = bridge.calls[-1]
        bridge.readReady.emit(
            key,
            len(bridge.calls),
            json.dumps({"rows": [], "total": 101, "offset": 50, "limit": 50, "summary": {}}),
        )
        status = window.findChild(QObject, "deepStateFilter")
        status.setProperty("currentIndex", 1)
        status.activated.emit(1)
        assert bridge.calls[-1][2]["offset"] == 0
        assert bridge.calls[-1][2]["state"] == "FAILED"
    finally:
        window.close()


def test_record_change_discards_old_raw_and_gates_unsupported_collection():
    bridge = Bridge()
    app, engine, window, page = component("RecordPage", bridge)
    try:
        page.setProperty("recordId", 1)
        page.setProperty(
            "state",
            engine.toScriptValue(
                {
                    "present": True,
                    "record": {"family": "TBMT", "natural_key": "IB-1"},
                    "deep_states": [{"lane": "HSMT", "state": "UNSUPPORTED", "supported": False}],
                }
            ),
        )
        page.setProperty("busy", False)
        app.processEvents()
        assert not window.findChild(QObject, "collectHsmt").property("enabled")
        page.setProperty(
            "state",
            engine.toScriptValue(
                {
                    "present": True,
                    "deep_states": [{"lane": "HSMT", "state": "NOT_COLLECTED", "supported": True}],
                }
            ),
        )
        action = window.findChild(QObject, "collectHsmt")
        action.clicked.emit()
        assert not action.property("enabled")
        action.clicked.emit()
        assert len([c for c in bridge.calls if c[1] == "enqueue_deep"]) == 1
        page.setProperty("preview", "Old record")
        page.setProperty("recordId", 2)
        bridge.readReady.emit("raw", 1, json.dumps({"verified": True, "text": "WRONG OLD RECORD"}))
        app.processEvents()
        assert "WRONG" not in page.property("preview")
        assert not page.property("state").toVariant().get("record")
    finally:
        window.close()
