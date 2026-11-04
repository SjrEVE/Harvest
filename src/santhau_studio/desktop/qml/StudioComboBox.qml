import QtQuick
import QtQuick.Controls.Basic as C
C.ComboBox {
    id: control
    implicitHeight: 38
    font.family: "Segoe UI"; font.pixelSize: 13
    leftPadding: 10; rightPadding: 30
    palette.text: "#172b3a"; palette.buttonText: "#172b3a"
    palette.base: "#ffffff"; palette.window: "#ffffff"
    palette.highlight: "#d9efeb"; palette.highlightedText: "#075e57"
    contentItem: Text {
        text: control.displayText; font: control.font; color: "#172b3a"
        verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
    }
    background: Rectangle {
        radius: 6; color: control.enabled ? "#ffffff" : "#edf1f4"
        border.color: control.activeFocus ? "#087f75" : "#cbd5df"
        border.width: control.activeFocus ? 2 : 1
    }
}
