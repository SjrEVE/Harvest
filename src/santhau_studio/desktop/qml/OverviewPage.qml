import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "UiText.js" as T

Item {
    id:page
    property var state:({families:[]})
    function reload(){backend.requestRead("page2","control_center","{}")}
    Component.onCompleted:reload()
    onVisibleChanged:if(visible)reload()
    Timer {interval:5000;repeat:true;running:page.visible;onTriggered:page.reload()}
    ColumnLayout {
        anchors.fill:parent;anchors.margins:24;spacing:16
        Label {text:"Tổng quan kho dữ liệu";font.pixelSize:24;font.weight:Font.DemiBold;color:"#172b3a"}
        Label {Layout.fillWidth:true;wrapMode:Text.Wrap;color:"#526477";text:"Số hồ sơ đã lưu và lịch sử ghi nhận. Số lượng trong kho không thay thế bằng chứng thu thập đủ từng khoảng ngày."}
        RowLayout {
            Layout.fillWidth:true
            Label {Layout.fillWidth:true;color:"#526477";text:"Cập nhật hiển thị: "+T.timestampVN((page.state.snapshot||{}).built_at)}
            Label {text:(page.state.snapshot||{}).stale?"Cần cập nhật hiển thị":"Đã cập nhật";color:(page.state.snapshot||{}).stale?"#965a08":"#087f75"}
            StudioButton {text:"Tải lại";onClicked:page.reload()}
        }
        Rectangle {
            Layout.fillWidth:true;implicitHeight:42;color:"#e5ecf1";radius:5
            RowLayout {
                anchors.fill:parent;anchors.margins:12;spacing:12
                Repeater {
                    model:["Loại hồ sơ","Hồ sơ","Phiên bản","Dữ liệu gốc","Lần ghi nhận","Bằng chứng gần nhất"]
                    delegate:Label {required property string modelData;required property int index;text:modelData;Layout.fillWidth:true;Layout.preferredWidth:index===5?210:125;font.weight:Font.DemiBold;color:"#172b3a"}
                }
            }
        }
        Repeater {
            model:page.state.families||[]
            delegate:Rectangle {
                required property var modelData
                Layout.fillWidth:true;implicitHeight:52;color:"#ffffff";radius:5
                RowLayout {
                    anchors.fill:parent;anchors.margins:12;spacing:12
                    Label {text:T.familyShort(modelData.family);Layout.fillWidth:true;Layout.preferredWidth:125;font.weight:Font.DemiBold;color:"#172b3a"}
                    Repeater {
                        model:[modelData.records,modelData.versions,modelData.raw_revisions,modelData.observations]
                        delegate:Label {required property var modelData;text:Number(modelData).toLocaleString(Qt.locale("vi_VN"),"f",0);Layout.fillWidth:true;Layout.preferredWidth:125;color:"#172b3a"}
                    }
                    Label {text:T.timestampVN(modelData.last_verified_at_utc);Layout.fillWidth:true;Layout.preferredWidth:210;color:"#526477";elide:Text.ElideRight}
                }
            }
        }
        Label {visible:!(page.state.families||[]).length;text:"Kho chưa có hồ sơ. Vào Thu thập dữ liệu hoặc nhập kho cũ trong Cài đặt.";color:"#526477";Layout.fillWidth:true;wrapMode:Text.Wrap}
        Label {visible:!!page.state.error;text:T.message(page.state.error);color:"#b42336";Layout.fillWidth:true;wrapMode:Text.Wrap}
        Label {text:"Bộ thu thập: "+((page.state.worker||{}).running?"đang hoạt động":"đã dừng")+" · Dung lượng dữ liệu: "+Math.round(((page.state.disk||{}).bytes||0)/1048576)+" MB";color:"#526477";Layout.fillWidth:true;wrapMode:Text.Wrap}
        Item {Layout.fillHeight:true}
    }
}
