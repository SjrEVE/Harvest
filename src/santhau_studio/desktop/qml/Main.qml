import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic

Window {
    id: root
    property string statusJson: ""
    width: 720
    height: 420
    minimumWidth: 480
    minimumHeight: 320
    visible: true
    title: appTitle
    color: "#1e2227"

    Column {
        anchors.centerIn: parent
        spacing: 14

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "G0-A — A8 packaging smoke"
            color: "#e6e9ec"
            font.pixelSize: 22
            font.bold: true
        }

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Qt Quick/QML renderer đang chạy trong build Nuitka standalone."
            color: "#9aa4ae"
            font.pixelSize: 13
        }

        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            width: 560
            height: 96
            radius: 6
            color: "#262b31"
            border.color: "#3a424a"

            Text {
                anchors.centerIn: parent
                width: parent.width - 24
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WrapAnywhere
                elide: Text.ElideRight
                maximumLineCount: 3
                color: "#7fd1a4"
                font.family: "Consolas"
                font.pixelSize: 12
                text: root.statusJson
            }
        }

        Button {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Đóng"
            onClicked: root.close()
        }
    }
}
