import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "UiText.js" as T

Item {
    id:page
    property var state:({periods:[]})
    property bool daily:false
    property string resolution:"MONTH"
    property bool busy:false
    function reload() { busy=true;backend.requestRead("page5","coverage_range",JSON.stringify({date_from:from.text,date_to:to.text,resolution:daily?"DAY":resolution})) }
    function number(value) { return value===null || value===undefined ? "Chưa biết" : Number(value).toLocaleString(Qt.locale("vi_VN"),'f',0) }
    onStateChanged: busy=false
    Component.onCompleted: reload()
    ColumnLayout {
        anchors.fill:parent;anchors.margins:24;spacing:14
        Label { text:"Mức độ thu thập";font.pixelSize:24;font.weight:Font.DemiBold;color:"#172b3a" }
        Label { Layout.fillWidth:true;wrapMode:Text.Wrap;text:"Đã lưu / tổng nguồn / còn thiếu theo cùng ngày công bố của từng loại. Tổng nguồn chưa đo đủ hiện Chưa biết; số 10.000 bị giới hạn không được coi là tổng chính xác. Sáu loại là sáu tập hồ sơ, không cộng thành số gói thầu.";color:"#526477" }
        RowLayout {
            Layout.fillWidth:true;spacing:12
            DateField { id:from;objectName:"coverageFrom";label:"Từ ngày";text:"01/01/2022";Layout.fillWidth:true }
            DateField { id:to;objectName:"coverageTo";label:"Đến hết ngày";text:T.vietnamDay(-1);Layout.fillWidth:true }
            StudioComboBox { id:periodMode;objectName:"coverageResolution";Layout.preferredWidth:140;Layout.alignment:Qt.AlignBottom;textRole:"label";valueRole:"value";model:[{label:"Theo tháng",value:"MONTH"},{label:"Theo tuần",value:"WEEK"},{label:"Theo ngày",value:"DAY"}];onActivated:{page.resolution=currentValue;page.daily=currentValue==="DAY";page.reload()} }
            StudioButton { text:busy?"Đang đọc…":"Xem mức độ thu thập";primary:true;enabled:!busy;onClicked:page.reload();Layout.alignment:Qt.AlignBottom }
            StudioButton { text:"Toàn bộ từ 2022";Layout.alignment:Qt.AlignBottom;onClicked:{page.daily=false;page.resolution="MONTH";periodMode.currentIndex=0;from.text="01/01/2022";to.text=T.vietnamDay(-1);page.reload()} }
        }
        Label { visible:!!page.state.error;Layout.fillWidth:true;wrapMode:Text.Wrap;text:page.state.error||"";color:"#b42336" }
        Label { visible:page.state.counts_available===false;Layout.fillWidth:true;wrapMode:Text.Wrap;text:"Bảng số lượng cần được cập nhật cho bản mới. Bật bộ thu thập để tạo lại bảng từ dữ liệu đã lưu; không cần cào lại.";color:"#965a08" }
        Label { visible:!!page.state.synthetic;text:"Đây là độ phủ giả lập trong kho thử, không xác nhận dữ liệu E-GP thật.";color:"#b42336";Layout.fillWidth:true;wrapMode:Text.Wrap }
        Rectangle {
            Layout.fillWidth:true;implicitHeight:42;color:"#e5ecf1";radius:5
            RowLayout {
                anchors.fill:parent;anchors.margins:8;spacing:8
                Label { text:page.daily?"Ngày":page.resolution==="WEEK"?"Tuần từ ngày":"Tháng";Layout.preferredWidth:100;color:"#172b3a";font.weight:Font.DemiBold }
                Repeater { model:["TBMT","KQLCNT","HOP_DONG","KHLCNT","DU_AN","YCBG"];delegate:Label { required property string modelData;Layout.fillWidth:true;Layout.preferredWidth:1;text:T.familyShort(modelData);horizontalAlignment:Text.AlignHCenter;color:"#172b3a";font.weight:Font.DemiBold } }
            }
        }
        ListView {
            id:rows;objectName:"coveragePeriods";Layout.fillWidth:true;Layout.fillHeight:true;clip:true;spacing:5
            model:page.state.periods||[];ScrollBar.vertical:ScrollBar {}
            delegate:Rectangle {
                id:row
                required property var modelData
                width:rows.width;height:128;color:"#ffffff";radius:5
                RowLayout {
                    anchors.fill:parent;anchors.margins:5;spacing:8
                    StudioButton {
                        Layout.preferredWidth:100;text:row.modelData.label+(row.modelData.partial_period?" *":"")
                        ToolTip.visible:hovered;ToolTip.text:row.modelData.date_from+" → "+row.modelData.date_to
                        onClicked:{if(!page.daily){page.daily=true;from.text=row.modelData.date_from;to.text=row.modelData.date_to;page.reload()}}
                    }
                    Repeater {
                        model:row.modelData.families
                        delegate:ColumnLayout {
                            required property var modelData
                            Layout.fillWidth:true;Layout.preferredWidth:1
                            spacing:2
                            Label { text:"Đã lưu: "+page.number(modelData.stored);color:"#172b3a";font.weight:Font.DemiBold;Layout.fillWidth:true;elide:Text.ElideRight }
                            Label { text:"Nguồn: "+page.number(modelData.source_total);color:"#526477";Layout.fillWidth:true;elide:Text.ElideRight }
                            Label { text:"Thiếu: "+page.number(modelData.missing)+(modelData.percent===null || modelData.percent===undefined ? "" : " · "+modelData.percent+"%");color:"#526477";Layout.fillWidth:true;elide:Text.ElideRight }
                            StudioButton {
                                Layout.fillWidth:true;primary:modelData.status==="COMPLETE"
                                text:modelData.count_drift?"Cần đối chiếu":(modelData.proven_days||0)+"/"+(modelData.total_days||0)+" ngày đủ"
                                Accessible.name:T.family(modelData.family)+" "+row.modelData.label+": "+T.label(modelData.status)
                                ToolTip.visible:hovered;ToolTip.text:T.label(modelData.status)+". Tổng nguồn ghi nhận: "+(modelData.source_measured_from?T.timestampVN(modelData.source_measured_from)+" → "+T.timestampVN(modelData.source_measured_to):"chưa đo đủ")
                                onClicked:{
                                    if(!page.daily){page.daily=true;from.text=row.modelData.date_from;to.text=row.modelData.date_to;page.reload()}
                                    else if(modelData.proof_ids.length) backend.requestRead("proof","coverage_proof",JSON.stringify({proof_id:modelData.proof_ids[0]}))
                                }
                            }
                        }
                    }
                }
            }
        }
        Label { text:(page.state.periods||[]).length+" "+(page.daily?"ngày":page.resolution==="WEEK"?"tuần":"tháng")+" · Dữ liệu hiển thị: "+(page.state.snapshot_at?T.timestampVN(page.state.snapshot_at):"chưa có")+". Màu xanh: đủ bằng chứng; 100% số lượng chưa thay thế bằng chứng. *: một phần tháng/tuần. Bảng cập nhật sau mỗi lượt xử lý.";color:"#526477";Layout.fillWidth:true;wrapMode:Text.Wrap }
    }
}
