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
        width: 320; height: 350
        padding: 12
        focus: true
        onOpened: {
            var day = Date.fromLocaleDateString(Qt.locale("vi_VN"), control.text.trim(), "dd/MM/yyyy")
            if (isNaN(day.getTime())) day = Date.fromLocaleDateString(Qt.locale("vi_VN"), control.text.trim(), "yyyy-MM-dd")
            if (!isNaN(day.getTime())) { grid.year=day.getFullYear(); grid.month=day.getMonth() }
        }
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        palette.text: "#172b3a"
        background: Rectangle { color: "#ffffff"; border.color: "#cbd5df"; radius: 6 }
        ColumnLayout {
            anchors.fill: parent
            RowLayout {
                StudioButton {
                    text: "‹"; Accessible.name: "Tháng trước"
                    onClicked: { if (grid.month === 0) { grid.year--; grid.month=11 } else grid.month-- }
                }
                Label { Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; text: "Tháng " + (grid.month+1) + " / " + grid.year }
                StudioButton {
                    text: "›"; Accessible.name: "Tháng sau"
                    onClicked: { if (grid.month === 11) { grid.year++; grid.month=0 } else grid.month++ }
                }
            }
            DayOfWeekRow { Layout.fillWidth: true; locale: Qt.locale("vi_VN") }
            MonthGrid {
                id: grid
                objectName: control.objectName + ".grid"
                Layout.fillWidth: true; Layout.fillHeight: true
                month: new Date().getMonth(); year: new Date().getFullYear()
                locale: Qt.locale("vi_VN")
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
