"""Click the calendar and Start button, then let a real offline worker consume the request."""
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import QMetaObject, QObject, QPoint, QPointF, Qt
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from test_desktop_qml import _qt_app

from santhau_studio.collectors.offline_acceptance import initialize
from santhau_studio.desktop.backend import StudioBackend
from santhau_studio.desktop.bridge import BackendBridge
from santhau_studio.storage.serving import build_serving_db
from santhau_studio.workers.jobs import JobRequest


def spin(app, predicate, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.01)  # Let Python worker threads run while QTest pumps Qt events.
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


@pytest.mark.parametrize(("family", "days", "expected_jobs", "weekly"), [("TBMT", 3, 1, False), ("ALL", 1, 6, False), ("ALL", 1, 1, False), ("ALL", 1, 1, True)])
def test_calendar_click_starts_real_worker_for_all_selected_days(tmp_path, monkeypatch, family, days, expected_jobs, weekly):
    app = _qt_app()
    data = tmp_path / "native-calendar"
    initialize(data)
    if family == "ALL" and expected_jobs == 1 and not weekly:
        with sqlite3.connect(data / "warehouse.sqlite") as conn:
            conn.execute("UPDATE filter_boundary_rules SET canary_verified=0 WHERE family!='TBMT'")
        build_serving_db(data / "warehouse.sqlite", data / "serving.sqlite")
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
        selector = window.findChild(QObject, "collectionFamily")
        window.findChild(QObject, "collectionCadence").setProperty("currentIndex", 0 if weekly else 1)
        if family == "TBMT":
            selector.setProperty("currentIndex", 1)
        assert selector.property("currentValue") == family
        click(window, window.findChild(QObject, "collectionSince2022"))
        assert window.findChild(QObject, "collectionDateFrom").property("text") == "01/01/2022"
        click(window, window.findChild(QObject, "collectionYesterday"))
        assert window.findChild(QObject, "collectionDateFrom").property("text") == window.findChild(QObject, "collectionDateTo").property("text")
        if family == "ALL" and expected_jobs == 1 and not weekly:
            selector.setProperty("currentIndex", 2)  # KHLCNT, deliberately unverified.
            click(window, window.findChild(QObject, "collectionStart"))
            page = window.findChild(QObject, "collectionPage")
            spin(app, lambda:not page.property("submitting"))
            assert "Kế hoạch lựa chọn nhà thầu: nguồn chưa được xác minh" in page.property("enqueueResult")
            assert "lọc thời gian" not in page.property("enqueueResult")
            assert not list((data / "jobs/queued").glob("*.json"))
            selector.setProperty("currentIndex", 0)
        # Reopening a typed historical day must show its month, not today's month.
        window.findChild(QObject, "collectionDateFrom").setProperty("text", "15/08/2022")
        click(window, window.findChild(QObject, "collectionDateFrom.calendar"))
        grid = window.findChild(QObject, "collectionDateFrom.grid")
        assert (grid.property("year"), grid.property("month")) == (2022, 7)
        QTest.keyClick(window, Qt.Key_Escape)
        today = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
        first, last = today-timedelta(days=days), today-timedelta(days=1)
        choose(window,"collectionDateFrom",first)
        choose(window,"collectionDateTo",last)
        evidence = os.environ.get("SANTHAU_DATE_UI_EVIDENCE")
        if evidence:
            Path(evidence).mkdir(parents=True,exist_ok=True)
            assert window.grabWindow().save(str(Path(evidence)/f"selected-days-{family}-{expected_jobs}.png"))
        # Bad manual input is rejected in Vietnamese before any worker starts.
        window.findChild(QObject, "collectionDateTo").setProperty("text", "31/02/2026")
        click(window, window.findChild(QObject, "collectionStart"))
        page = window.findChild(QObject, "collectionPage")
        spin(app, lambda:not page.property("submitting"))
        assert "Ngày kết thúc không hợp lệ" in page.property("enqueueResult")
        assert not list((data / "jobs/queued").glob("*.json"))
        window.findChild(QObject, "collectionDateTo").setProperty("text", last.strftime("%d/%m/%Y"))
        click(window,window.findChild(QObject,"collectionStart"))
        page = window.findChild(QObject,"collectionPage")
        spin(app,lambda:not page.property("submitting"))
        assert "Đã xếp hàng" in page.property("enqueueResult"),page.property("enqueueResult")
        if family == "ALL" and expected_jobs == 1 and not weekly:
            assert "1/6 loại" in page.property("enqueueResult")
            assert "nguồn chưa được xác minh" in page.property("enqueueResult")
        spin(app,lambda:len(list((data/"jobs/done").glob("*.json")))==expected_jobs,seconds=60)
        job = JobRequest.from_json(next((data/"jobs/done").glob("*.json")).read_text())
        if weekly:
            assert job.scope["cadence"] == "WEEK" and len(job.sources) == 6
        assert job.interval().duration == timedelta(days=days)
        assert job.interval().from_utc.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date()==first
        assert job.interval().to_utc.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date()==last+timedelta(days=1)
        with sqlite3.connect(data/"warehouse.sqlite") as conn:
            status = conn.execute("SELECT lifecycle,status FROM runs WHERE job_id=?",(job.job_id,)).fetchone()
            records = conn.execute("SELECT count(*) FROM source_records").fetchone()[0]
        assert status == ("FINISHED","COMPLETED"),status
        expected_families = 6 if weekly else expected_jobs
        assert records==36*days*expected_families,records
        with sqlite3.connect(data/"warehouse.sqlite") as conn:
            assert conn.execute("SELECT count(*) FROM runs WHERE lifecycle='FINISHED' AND status='COMPLETED'").fetchone()[0] == (7 if weekly else expected_jobs)
            assert conn.execute("SELECT count(DISTINCT family) FROM source_records").fetchone()[0] == expected_families
        QMetaObject.invokeMethod(page, "reload")
        def ui_finished():
            state=page.property("state")
            state=state.toVariant() if hasattr(state,"toVariant") else state
            return len(state.get("done",[]))==expected_jobs and not state.get("active")
        spin(app,ui_finished)
        def coverage_finished():
            overview = page.property("overview")
            overview = overview.toVariant() if hasattr(overview, "toVariant") else overview
            return sum(r["yesterday_status"] == "COMPLETE" for r in overview.get("families", [])) == expected_families
        spin(app, coverage_finished)
        # Switching to automatic must hide both dates rather than silently ignoring visible input.
        window.findChild(QObject,"collectionMode").setProperty("currentIndex",1)
        QTest.qWait(100)
        assert not window.findChild(QObject,"collectionDateFrom").property("visible")
        assert not window.findChild(QObject,"collectionDateTo").property("visible")
        if evidence:
            assert window.grabWindow().save(str(Path(evidence)/f"automatic-mode-{family}-{expected_jobs}.png"))
            (Path(evidence)/f"native-result-{family}-{expected_jobs}.json").write_text(json.dumps({"status":"PASS", "scope":job.scope,
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
