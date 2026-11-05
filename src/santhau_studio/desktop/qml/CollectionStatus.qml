import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "UiText.js" as T

Rectangle {
    id: panel
    property var view: ({})
    property var snapshot: ({})
    property string checkedAt: ""
    property string snapshotError: ""
    property string phaseSince: ""
    property real nowMs: Date.now()
    property color ink: view.tone === "error" ? "#b42336" : view.tone === "warning" ? "#965a08" : "#172b3a"
    color: "#ffffff"
    radius: 8
    border.color: "#cbd5df"
    implicitHeight: content.implicitHeight + 28
    ColumnLayout {
        id: content
        anchors.fill: parent; anchors.margins: 14; spacing: 7
        Label { objectName:"collectionStateText"; Layout.fillWidth:true; wrapMode:Text.Wrap; text:panel.view.title||"Đang đọc trạng thái"; font.pixelSize:20; font.weight:Font.DemiBold; color:panel.ink; Accessible.name:text }
        Label { objectName:"collectionStateDetail"; Layout.fillWidth:true; wrapMode:Text.Wrap; text:panel.view.detail||""; color:"#526477" }
        Label {
            objectName:"collectionLiveProgress"; Layout.fillWidth:true; wrapMode:Text.Wrap
            property var progress: panel.view.progress || {}
            visible: !!progress.family
            text: T.family(progress.family)+" · "+(progress.rows||0)+" dòng · "+(progress.pages||0)+" trang"
                +(progress.week_index?" · Tuần "+progress.week_index+"/"+progress.week_count+" · Loại "+progress.family_index+"/6":"")
                +(progress.from?"\n"+T.collectionSpan(progress.from,progress.to,"BACKFILL"):"")
            color:"#172b3a"; font.weight:Font.DemiBold
        }
        Label {
            Layout.fillWidth:true; wrapMode:Text.Wrap; color:"#526477"
            property var progress: panel.view.progress || {}
            visible: !!progress.updated_at
            text:"Nhận trang gần nhất: "+T.timestampVN(progress.updated_at)
                +(panel.nowMs-Date.parse(progress.updated_at)>60000?" · Chưa có trang mới hơn 1 phút; có thể đang chờ nguồn hoặc thử lại.":"")
        }
        Label { objectName:"collectionHeartbeat"; Layout.fillWidth:true; wrapMode:Text.Wrap; text:panel.checkedAt?"Kiểm tra trạng thái lúc "+T.timestampVN(panel.checkedAt):"Đang chờ phản hồi đầu tiên"; color:"#526477"; font.pixelSize:13 }
        Label { Layout.fillWidth:true;wrapMode:Text.Wrap;visible:!!panel.phaseSince && panel.view.busy && panel.view.phase!=="COLLECTING"; text:"Bước hiện tại bắt đầu lúc "+T.timestampVN(panel.phaseSince);color:"#526477";font.pixelSize:13 }
        Label { Layout.fillWidth:true; wrapMode:Text.Wrap; visible:!!panel.snapshot.stale; text:"Dữ liệu tra cứu chưa cập nhật đến tiến độ mới nhất. Bản hiển thị: "+(panel.snapshot.built_at?T.timestampVN(panel.snapshot.built_at):"chưa có"); color:"#965a08" }
        Label { Layout.fillWidth:true; wrapMode:Text.Wrap; visible:!!panel.snapshotError; text:T.message(panel.snapshotError); color:"#b42336" }
    }
}
