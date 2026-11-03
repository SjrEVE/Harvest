"""Click the calendar and Start button, then let a real offline worker consume the request."""
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from PySide6.QtCore import QMetaObject, QObject, QPoint, QPointF, Qt
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from test_desktop_qml import _qt_app

from santhau_studio.collectors.offline_acceptance import initialize
from santhau_studio.desktop.backend import StudioBackend
from santhau_studio.desktop.bridge import BackendBridge
from santhau_studio.workers.jobs import JobRequest


def spin(app, predicate, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(30)
    raise AssertionError("UI or worker did not reach the expected state")


def click(window, item):
    assert isinstance(item, QQuickItem)
    point = item.mapToScene(QPointF(item.width()/2, item.height()/2))
    QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(round(point.x()), round(point.y())))
    QTest.qWait(80)


def choose(window, name, day):
    field = window.findChild(QObject, name)
    click(window, window.findChild(QObject, name + ".calendar"))
    popup = window.findChild(QObject, name + ".popup")
    assert popup.property("visible"), "Calendar button must open the calendar"
    grid = window.findChild(QObject, name + ".grid")
    grid.setProperty("year", day.year)
    grid.setProperty("month", day.month-1)
    QTest.qWait(80)
    body = grid.property("contentItem")
    spacing = grid.property("spacing")
    cell_width = (body.width() - 6*spacing)/7
    cell_height = (body.height() - 5*spacing)/6
    index = day.replace(day=1).weekday() + day.day - 1
    point = body.mapToScene(QPointF((index%7)*(cell_width+spacing)+cell_width/2,
                                   (index//7)*(cell_height+spacing)+cell_height/2))
    QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(round(point.x()),round(point.y())))
    QTest.qWait(80)
    assert field.property("text") == day.strftime("%d/%m/%Y")
    assert not popup.property("visible")


def test_calendar_click_starts_real_worker_for_all_selected_days(tmp_path, monkeypatch):
    app = _qt_app()
    data = tmp_path / "native-calendar"
    initialize(data)
    monkeypatch.setenv("SANTHAU_OFFLINE_ACCEPTANCE_ROOT", str(data.resolve()))
    backend = StudioBackend(data)
    bridge = BackendBridge(backend)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", bridge)
    engine.rootContext().setContextProperty("appTitle", "Săn Thầu — kiểm tra ngày, dữ liệu thử ngoại tuyến")
    qml = Path(__file__).resolve().parents[2] / "src/santhau_studio/desktop/qml/Main.qml"
    engine.load(qml.as_uri())
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    assert isinstance(window, QQuickWindow)
    try:
        window.setProperty("pageIndex", 3)
        window.requestActivate()
        QTest.qWait(350)
        today = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
        first, last = today-timedelta(days=3), today-timedelta(days=1)
        choose(window,"collectionDateFrom",first)
        choose(window,"collectionDateTo",last)
        evidence = os.environ.get("SANTHAU_DATE_UI_EVIDENCE")
        if evidence:
            Path(evidence).mkdir(parents=True,exist_ok=True)
            assert window.grabWindow().save(str(Path(evidence)/"selected-days.png"))
        click(window,window.findChild(QObject,"collectionStart"))
        page = window.findChild(QObject,"collectionPage")
        spin(app,lambda:not page.property("submitting"))
        assert "đang kết nối bộ thu thập" in page.property("enqueueResult"),page.property("enqueueResult")
        spin(app,lambda:len(list((data/"jobs/done").glob("*.json")))==1,seconds=30)
        job = JobRequest.from_json(next((data/"jobs/done").glob("*.json")).read_text())
        assert job.interval().duration == timedelta(days=3)
        assert job.interval().from_utc.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date()==first
        assert job.interval().to_utc.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date()==last+timedelta(days=1)
        with sqlite3.connect(data/"warehouse.sqlite") as conn:
            status = conn.execute("SELECT lifecycle,status FROM runs WHERE job_id=?",(job.job_id,)).fetchone()
            records = conn.execute("SELECT count(*) FROM source_records").fetchone()[0]
        assert status == ("FINISHED","COMPLETED"),status
        assert records==108,records
        QMetaObject.invokeMethod(page, "reload")
        def ui_finished():
            state=page.property("state")
            state=state.toVariant() if hasattr(state,"toVariant") else state
            return len(state.get("done",[]))==1 and not state.get("active")
        spin(app,ui_finished)
        # Switching to automatic must hide both dates rather than silently ignoring visible input.
        window.findChild(QObject,"collectionMode").setProperty("currentIndex",1)
        QTest.qWait(100)
        assert not window.findChild(QObject,"collectionDateFrom").property("visible")
        assert not window.findChild(QObject,"collectionDateTo").property("visible")
        if evidence:
            assert window.grabWindow().save(str(Path(evidence)/"automatic-mode.png"))
            (Path(evidence)/"native-result.json").write_text(json.dumps({"status":"PASS", "scope":job.scope,
                "records":records,"worker_result":status,"network":"synthetic offline only"},indent=2))
    finally:
        backend.worker_command("FORCE_TERMINATE")
        deadline=time.monotonic()+10
        while backend.worker_status()["running"] and time.monotonic()<deadline:
            QTest.qWait(50)
        assert not backend.worker_status()["running"],"Acceptance worker must exit"
        window.close()
        bridge.invalidateReads()
        bridge.pool.waitForDone()
