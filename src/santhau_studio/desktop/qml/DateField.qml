import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Control {
    id: control
    property alias text: input.text
    property string label: "Ngày"
    implicitWidth: 290
    implicitHeight: contentItem.implicitHeight
    function choose(day) {
        input.text = Qt.formatDate(day, "dd/MM/yyyy")
        calendar.close()
        input.forceActiveFocus()
    }
    contentItem: ColumnLayout {
        spacing: 4
        Label { text: control.label; color: "#526477" }
        RowLayout {
            spacing: 4
            StudioTextField {
                id: input
                objectName: control.objectName + ".text"
                Layout.fillWidth: true
                placeholderText: "ngày/tháng/năm"
                Accessible.name: control.label
                color: "#172b3a"
                background: Rectangle { color: "#ffffff"; border.color: "#cbd5df"; radius: 4 }
            }
            StudioButton {
                objectName: control.objectName + ".calendar"
                text: "Chọn lịch"
                Accessible.name: "Chọn " + control.label.toLowerCase() + " trên lịch"
                onClicked: calendar.open()
            }
        }
    }
    Popup {
        id: calendar
        objectName: control.objectName + ".popup"
        y: control.height + 4
        width: 360; height: 400
        padding: 12
        focus: true
        function syncSelectors() {
            if (!monthSelector || !yearSelector) return
            monthSelector.currentIndex = grid.month
            yearSelector.currentIndex = yearSelector.find(String(grid.year))
            yearSelector.editText = String(grid.year)
        }
        function commitYear() {
            if (yearSelector.acceptableInput) grid.year = Number(yearSelector.editText)
        }
        onOpened: {
            var day = Date.fromLocaleDateString(Qt.locale("vi_VN"), control.text.trim(), "dd/MM/yyyy")
            if (isNaN(day.getTime())) day = Date.fromLocaleDateString(Qt.locale("vi_VN"), control.text.trim(), "yyyy-MM-dd")
            if (!isNaN(day.getTime())) { grid.year=day.getFullYear(); grid.month=day.getMonth() }
            syncSelectors()
        }
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        palette.text: "#172b3a"
        background: Rectangle { color: "#ffffff"; border.color: "#cbd5df"; radius: 6 }
        ColumnLayout {
            anchors.fill: parent
            RowLayout {
                spacing: 8
                StudioButton {
                    objectName: control.objectName + ".previousMonth"
                    Layout.preferredWidth: 36; Layout.minimumWidth: 36; Layout.maximumWidth: 36
                    Layout.alignment: Qt.AlignBottom
                    text: "‹"; Accessible.name: "Tháng trước"
                    enabled: grid.year > 1 || grid.month > 0
                    onClicked: { if (grid.month === 0) { grid.year--; grid.month=11 } else grid.month-- }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    Label { text: "Tháng"; color: "#526477"; font.pixelSize: 12 }
                    StudioComboBox {
                        id: monthSelector
                        objectName: control.objectName + ".month"
                        Layout.fillWidth: true
                        Accessible.name: "Chọn tháng"
                        model: ["Tháng 1", "Tháng 2", "Tháng 3", "Tháng 4", "Tháng 5", "Tháng 6",
                                "Tháng 7", "Tháng 8", "Tháng 9", "Tháng 10", "Tháng 11", "Tháng 12"]
                        onActivated: grid.month = currentIndex
                    }
                }
                ColumnLayout {
                    Layout.preferredWidth: 100
                    spacing: 4
                    Label { text: "Năm"; color: "#526477"; font.pixelSize: 12 }
                    ComboBox {
                        id: yearSelector
                        objectName: control.objectName + ".year"
                        Layout.fillWidth: true
                        implicitHeight: 38
                        font.family: "Segoe UI"; font.pixelSize: 13
                        editable: true
                        selectTextByMouse: true
                        Accessible.name: "Chọn hoặc nhập năm"
                        validator: IntValidator { bottom: 1; top: 9999; locale: "C" }
                        model: {
                            var years = []
                            for (var year = new Date().getFullYear() + 1; year >= 1900; --year)
                                years.push(String(year))
                            return years
                        }
                        palette.text: "#172b3a"; palette.buttonText: "#172b3a"
                        palette.base: "#ffffff"; palette.window: "#ffffff"
                        palette.highlight: "#d9efeb"; palette.highlightedText: "#075e57"
                        background: Rectangle {
                            radius: 6; color: "#ffffff"
                            border.color: !yearSelector.acceptableInput ? "#b42336"
                                          : yearSelector.activeFocus ? "#087f75" : "#cbd5df"
                            border.width: yearSelector.activeFocus ? 2 : 1
                        }
                        ToolTip.visible: hovered
                        ToolTip.delay: 500
                        ToolTip.text: "Chọn năm trong danh sách hoặc gõ năm rồi nhấn Enter"
                        onActivated: grid.year = Number(currentText)
                        onAccepted: calendar.commitYear()
                    }
                }
                StudioButton {
                    objectName: control.objectName + ".nextMonth"
                    Layout.preferredWidth: 36; Layout.minimumWidth: 36; Layout.maximumWidth: 36
                    Layout.alignment: Qt.AlignBottom
                    text: "›"; Accessible.name: "Tháng sau"
                    enabled: grid.year < 9999 || grid.month < 11
                    onClicked: { if (grid.month === 11) { grid.year++; grid.month=0 } else grid.month++ }
                }
            }
            Connections {
                target: yearSelector.contentItem
                ignoreUnknownSignals: true
                function onEditingFinished() { if (calendar.opened) calendar.commitYear() }
            }
            DayOfWeekRow { Layout.fillWidth: true; locale: Qt.locale("vi_VN") }
            MonthGrid {
                id: grid
                objectName: control.objectName + ".grid"
                Layout.fillWidth: true; Layout.fillHeight: true
                month: new Date().getMonth(); year: new Date().getFullYear()
                locale: Qt.locale("vi_VN")
                enabled: yearSelector.acceptableInput
                onYearChanged: calendar.syncSelectors()
                onMonthChanged: calendar.syncSelectors()
                onClicked: function(day) { control.choose(day) }
            }
            RowLayout {
                StudioButton { text: "Hôm nay"; onClicked: control.choose(new Date()) }
                Item { Layout.fillWidth: true }
                StudioButton { text: "Đóng"; onClicked: calendar.close() }
            }
        }
    }
}
