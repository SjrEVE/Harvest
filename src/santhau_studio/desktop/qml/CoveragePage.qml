import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "UiText.js" as T

Item {
    id:page
    property var state:({periods:[]})
    property bool daily:false
    property bool busy:false
    function reload() { busy=true;backend.requestRead("page5","coverage_range",JSON.stringify({date_from:from.text,date_to:to.text,resolution:daily?"DAY":"MONTH"})) }
    onStateChanged: busy=false
    Component.onCompleted: reload()
    ColumnLayout {
        anchors.fill:parent;anchors.margins:24;spacing:14
        Label { text:"Mức độ thu thập";font.pixelSize:24;font.weight:Font.DemiBold;color:"#172b3a" }
        Label { Layout.fillWidth:true;wrapMode:Text.Wrap;text:"Đối chiếu toàn bộ lịch sử theo tháng. Mở một tháng để tìm ngày còn thiếu; chỉ dữ liệu có bằng chứng mới được ghi nhận là đủ.";color:"#526477" }
        RowLayout {
            Layout.fillWidth:true;spacing:12
            DateField { id:from;objectName:"coverageFrom";label:"Từ ngày";text:"01/01/2022";Layout.fillWidth:true }
            DateField { id:to;objectName:"coverageTo";label:"Đến hết ngày";text:T.vietnamDay(-1);Layout.fillWidth:true }
            StudioButton { text:busy?"Đang đọc…":"Xem mức độ thu thập";primary:true;enabled:!busy;onClicked:page.reload();Layout.alignment:Qt.AlignBottom }
            StudioButton { text:"Toàn bộ từ 2022";Layout.alignment:Qt.AlignBottom;onClicked:{page.daily=false;from.text="01/01/2022";to.text=T.vietnamDay(-1);page.reload()} }
        }
        Label { visible:!!page.state.error;Layout.fillWidth:true;wrapMode:Text.Wrap;text:page.state.error||"";color:"#b42336" }
        Label { visible:!!page.state.synthetic;text:"Đây là độ phủ giả lập trong kho thử, không xác nhận dữ liệu E-GP thật.";color:"#b42336";Layout.fillWidth:true;wrapMode:Text.Wrap }
        Rectangle {
            Layout.fillWidth:true;implicitHeight:42;color:"#e5ecf1";radius:5
            RowLayout {
                anchors.fill:parent;anchors.margins:8;spacing:8
                Label { text:page.daily?"Ngày":"Tháng";Layout.preferredWidth:100;color:"#172b3a";font.weight:Font.DemiBold }
                Repeater { model:["TBMT","KQLCNT","HOP_DONG","KHLCNT","DU_AN","YCBG"];delegate:Label { required property string modelData;Layout.fillWidth:true;Layout.preferredWidth:1;text:T.familyShort(modelData);horizontalAlignment:Text.AlignHCenter;color:"#172b3a";font.weight:Font.DemiBold } }
            }
        }
        ListView {
            id:rows;objectName:"coveragePeriods";Layout.fillWidth:true;Layout.fillHeight:true;clip:true;spacing:5
            model:page.state.periods||[];ScrollBar.vertical:ScrollBar {}
            delegate:Rectangle {
                id:row
                required property var modelData
                width:rows.width;height:46;color:"#ffffff";radius:5
                RowLayout {
                    anchors.fill:parent;anchors.margins:5;spacing:8
                    StudioButton {
                        Layout.preferredWidth:100;text:row.modelData.label+(row.modelData.partial_period?" *":"")
                        ToolTip.visible:hovered;ToolTip.text:row.modelData.date_from+" → "+row.modelData.date_to
                        onClicked:{if(!page.daily){page.daily=true;from.text=row.modelData.date_from;to.text=row.modelData.date_to;page.reload()}}
                    }
                    Repeater {
                        model:row.modelData.families
                        delegate:StudioButton {
                            required property var modelData
                            Layout.fillWidth:true;Layout.preferredWidth:1
                            text:({COMPLETE:"Đủ",PARTIAL:"Chưa đủ",NOT_ACCESSED:"Chưa xác minh",BLOCKED:"Bị chặn",NEEDS_RECONCILE:"Cần đối chiếu"})[modelData.status]||T.label(modelData.status)
                            primary:modelData.status==="COMPLETE"
                            Accessible.name:T.family(modelData.family)+" "+row.modelData.label+": "+T.label(modelData.status)
                            ToolTip.visible:hovered;ToolTip.text:T.family(modelData.family)+" · "+T.label(modelData.status)
                            onClicked:{
                                if(!page.daily){page.daily=true;from.text=row.modelData.date_from;to.text=row.modelData.date_to;page.reload()}
                                else if(modelData.proof_ids.length) backend.requestRead("proof","coverage_proof",JSON.stringify({proof_id:modelData.proof_ids[0]}))
                            }
                        }
                    }
                }
            }
        }
        Label { text:(page.state.periods||[]).length+" "+(page.daily?"ngày":"tháng")+" · Màu xanh: đủ trong khoảng đã chọn. Dấu *: chỉ xem một phần tháng, không phải cả tháng.";color:"#526477";Layout.fillWidth:true;wrapMode:Text.Wrap }
    }
}
