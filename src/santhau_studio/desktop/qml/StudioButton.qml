import QtQuick
import QtQuick.Controls.Basic as C

C.Button {
    id: control
    property bool primary: false
    property bool danger: false
    implicitHeight: 38
    implicitWidth: Math.max(92, contentItem.implicitWidth + 28)
    leftPadding: 14; rightPadding: 14
    focusPolicy: Qt.StrongFocus
    font.family: "Segoe UI"; font.pixelSize: 13; font.weight: Font.DemiBold
    contentItem: Text {
        text: control.text
        font: control.font
        color: !control.enabled ? "#84909d" : (control.primary ? "#ffffff" : (control.danger ? "#b42336" : "#24394c"))
        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: 6
        color: !control.enabled ? "#e9eef2" : (control.primary ? (control.down ? "#065f58" : "#087f75") : (control.hovered || control.checked ? "#e5efee" : "#ffffff"))
        border.color: control.activeFocus ? "#087f75" : (control.primary ? color : "#cbd5df")
        border.width: control.activeFocus ? 2 : 1
    }
}
