import QtQuick
import QtQuick.Controls.Basic as C
C.SpinBox {
    id:control
    implicitHeight:38
    font.family:"Segoe UI";font.pixelSize:13
    palette.text:"#172b3a";palette.buttonText:"#172b3a"
    palette.base:"#ffffff";palette.button:"#e5ecf1";palette.window:"#ffffff"
    palette.highlight:"#087f75";palette.highlightedText:"#ffffff"
    background:Rectangle {color:"#ffffff";radius:6;border.color:control.activeFocus?"#087f75":"#cbd5df";border.width:control.activeFocus?2:1}
}
