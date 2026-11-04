import QtQuick
import QtQuick.Controls.Basic as C
C.TextField {
    id: control
    implicitHeight: 38
    leftPadding: 10; rightPadding: 10
    font.family: "Segoe UI"; font.pixelSize: 13
    color: "#172b3a"; placeholderTextColor: "#5a6d7f"
    selectionColor: "#087f75"; selectedTextColor: "#ffffff"
    selectByMouse: true
    background: Rectangle {
        radius: 6; color: control.enabled ? "#ffffff" : "#edf1f4"
        border.color: control.activeFocus ? "#087f75" : "#cbd5df"
        border.width: control.activeFocus ? 2 : 1
    }
}
