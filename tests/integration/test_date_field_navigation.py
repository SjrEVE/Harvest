"""Use the visible date controls, including typing a year and choosing a leap day."""

import os
from datetime import date
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from test_collection_date_ui import click
from test_desktop_qml import _qt_app


@pytest.fixture
def calendar(tmp_path):
    app = _qt_app()
    font = Path("C:/Windows/Fonts/segoeui.ttf")
    if font.is_file():
        QFontDatabase.addApplicationFont(str(font))
        app.setFont(QFont("Segoe UI", 10))
    engine = QQmlApplicationEngine()
    qml = Path(
        os.environ.get("SANTHAU_DATE_QML_DIR")
        or Path(__file__).parents[2] / "src/santhau_studio/desktop/qml"
    ).as_uri()
    engine.loadData(
        f'''import QtQuick
import QtQuick.Controls.Basic
import "{qml}"
ApplicationWindow {{
    width: 620; height: 560; visible: true
    DateField {{ objectName: "date"; x: 40; y: 30; width: 500; label: "Từ ngày"; text: "15/08/2026" }}
}}'''.encode(),
        QUrl.fromLocalFile(str(tmp_path / "calendar.qml")),
    )
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.requestActivate()
    app.processEvents()
    QTest.qWait(120)
    click(window, window.findChild(QObject, "date.calendar"))
    assert window.findChild(QObject, "date.popup").property("visible")
    yield window
    window.close()
    engine.deleteLater()
    app.processEvents()


def type_year(window, year, *, enter=True):
    selector = window.findChild(QObject, "date.year")
    assert selector is not None, "The calendar needs a visible year selector"
    assert selector.property("editable"), "Typing a year must avoid repeated month clicks"
    click(window, selector.property("contentItem"))
    QTest.keyClick(window, Qt.Key_A, Qt.ControlModifier)
    for digit in str(year):
        QTest.keyClick(window, Qt.Key(ord(digit)))
    if enter:
        QTest.keyClick(window, Qt.Key_Return)
    QTest.qWait(60)
    return selector


def select_month(window, month):
    selector = window.findChild(QObject, "date.month")
    assert selector is not None, "A month must be directly selectable"
    click(window, selector)
    QTest.keyClick(window, Qt.Key_Home)
    for _ in range(month - 1):
        QTest.keyClick(window, Qt.Key_Down)
    QTest.keyClick(window, Qt.Key_Return)
    QTest.qWait(80)


def click_day(window, day):
    grid = window.findChild(QObject, "date.grid")
    body = grid.property("contentItem")
    spacing = grid.property("spacing")
    width, height = (body.width() - 6 * spacing) / 7, (body.height() - 5 * spacing) / 6
    index = day.replace(day=1).weekday() + day.day - 1
    point = body.mapToScene(
        QPointF(
            (index % 7) * (width + spacing) + width / 2,
            (index // 7) * (height + spacing) + height / 2,
        )
    )
    QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(round(point.x()), round(point.y())))
    QTest.qWait(80)


def test_jump_to_year_and_month_then_choose_leap_day(calendar):
    year = type_year(calendar, 2024)
    select_month(calendar, 2)
    grid = calendar.findChild(QObject, "date.grid")
    assert (grid.property("year"), grid.property("month")) == (2024, 1)
    assert year.property("editText") == "2024"
    assert calendar.findChild(QObject, "date").property("text") == "15/08/2026"
    click_day(calendar, date(2024, 2, 29))
    assert calendar.findChild(QObject, "date").property("text") == "29/02/2024"
    assert not calendar.findChild(QObject, "date.popup").property("visible")
    click(calendar, calendar.findChild(QObject, "date.calendar"))
    assert year.property("editText") == "2024"
    assert calendar.findChild(QObject, "date.month").property("currentIndex") == 1


def test_typed_year_commits_on_month_selection_and_arrows_sync_header(calendar):
    year = type_year(calendar, 2022, enter=False)
    select_month(calendar, 12)
    grid = calendar.findChild(QObject, "date.grid")
    assert (grid.property("year"), grid.property("month")) == (2022, 11)
    click(calendar, calendar.findChild(QObject, "date.nextMonth"))
    assert (grid.property("year"), grid.property("month")) == (2023, 0)
    assert year.property("editText") == "2023"
    assert calendar.findChild(QObject, "date.month").property("currentIndex") == 0
    click(calendar, calendar.findChild(QObject, "date.previousMonth"))
    assert year.property("editText") == "2022"


def test_year_can_be_picked_from_dropdown_and_escape_preserves_date(calendar):
    selector = calendar.findChild(QObject, "date.year")
    assert selector is not None
    point = selector.mapToScene(QPointF(selector.width() - 12, selector.height() / 2))
    QTest.mouseClick(calendar, Qt.LeftButton, pos=QPoint(round(point.x()), round(point.y())))
    QTest.qWait(80)
    QTest.keyClick(calendar, Qt.Key_Down)
    QTest.keyClick(calendar, Qt.Key_Return)
    QTest.qWait(80)
    assert calendar.findChild(QObject, "date.grid").property("year") == 2025
    QTest.keyClick(calendar, Qt.Key_Escape)
    assert not calendar.findChild(QObject, "date.popup").property("visible")
    assert calendar.findChild(QObject, "date").property("text") == "15/08/2026"
    click(calendar, calendar.findChild(QObject, "date.calendar"))
    assert selector.property("editText") == "2026"


def test_invalid_year_does_not_change_calendar(calendar):
    selector = type_year(calendar, 0)
    assert not selector.property("acceptableInput")
    assert calendar.findChild(QObject, "date.grid").property("year") == 2026
    assert calendar.findChild(QObject, "date").property("text") == "15/08/2026"
