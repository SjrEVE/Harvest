import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "UiText.js" as T

Item {
    id: page
    objectName: "collectionPage"
    property var state: ({queued:[],active:[],done:[]})
    property var overview: ({families:[]})
    property string enqueueResult: ""
    property bool submitting: false
    property bool synthetic: false
    property string selectedJobId: ""
    function selectedJob() { return target.currentValue || ((state.active||[]).length ? state.active[0].job_id : "") }
    signal selectDataRootRequested()
    function reload() {
        backend.requestRead("page4","jobs","{}")
        backend.requestRead("collectionOverview","collection_overview","{}")
    }
    function preset(first,last) { mode.currentIndex=0; from.text=first; to.text=last; enqueueResult="" }
    function submit() {
        var request={job_kind:mode.currentValue,family:family.currentValue,
            request_limits:{max_pages:pages.value,max_requests:requests.value,max_seconds:seconds.value,pacing_seconds:pacing.value}}
        if (mode.currentValue === "BACKFILL" && family.currentValue === "ALL") request.cadence=cadence.currentValue
        if (mode.currentValue === "INCREMENTAL") request.auto_range=true
        else {request.date_from=from.text;request.date_to=to.text}
        submitting=true; enqueueResult=""
        backend.requestRead("collectionAction","start_collection",JSON.stringify(request))
    }
    Component.onCompleted: reload()
    onVisibleChanged: if (visible) reload()
    Timer { interval:2000; running:page.visible; repeat:true; onTriggered:page.reload() }
    ScrollView {
        anchors.fill:parent; anchors.margins:24; contentWidth:availableWidth; clip:true
        ColumnLayout {
            width:parent.width; spacing:16
            Label { text:"Thu thập dữ liệu"; color:"#172b3a"; font.pixelSize:24; font.weight:Font.DemiBold }
            Label { Layout.fillWidth:true; wrapMode:Text.Wrap; text:"Chọn khoảng thời gian một lần. Các loại hồ sơ được xử lý tuần tự, ngày đã có đủ bằng chứng sẽ được bỏ qua."; color:"#526477" }
            Rectangle {
                Layout.fillWidth:true; implicitHeight:form.implicitHeight+32; radius:8; color:"#ffffff"
                ColumnLayout {
                    id:form; anchors.fill:parent; anchors.margins:16; spacing:12
                    GridLayout {
                        Layout.fillWidth:true; columns:width<850 ? 2 : 4; columnSpacing:12; rowSpacing:12
                        ColumnLayout {
                            Layout.fillWidth:true
                            Label { text:"Cách thu thập"; color:"#526477" }
                            StudioComboBox { id:mode; objectName:"collectionMode"; Layout.fillWidth:true; textRole:"label"; valueRole:"value"; model:T.options(["BACKFILL","INCREMENTAL","RECONCILE"]) }
                        }
                        ColumnLayout {
                            Layout.fillWidth:true
                            Label { text:"Loại hồ sơ"; color:"#526477" }
                            StudioComboBox { id:family; objectName:"collectionFamily"; Layout.fillWidth:true; textRole:"label"; valueRole:"value"; model:T.options(["ALL","TBMT","KHLCNT","KQLCNT","DU_AN","YCBG","HOP_DONG"]); Accessible.name:"Loại hồ sơ cần thu thập" }
                        }
                        DateField { id:from; objectName:"collectionDateFrom"; Layout.fillWidth:true; label:"Từ ngày"; text:"01/01/2022"; visible:mode.currentValue!=="INCREMENTAL" }
                        DateField { id:to; objectName:"collectionDateTo"; Layout.fillWidth:true; label:"Đến hết ngày"; text:T.vietnamDay(-1); visible:mode.currentValue!=="INCREMENTAL" }
                    }
                    RowLayout {
                        visible:mode.currentValue==="BACKFILL" && family.currentValue==="ALL"
                        Layout.fillWidth:true
                        Label { text:"Chia đợt";color:"#526477" }
                        StudioComboBox {
                            id:cadence;objectName:"collectionCadence";Layout.fillWidth:true
                            textRole:"label";valueRole:"value"
                            model:[{label:"Theo tuần — đủ 6 loại rồi chuyển tuần tiếp",value:"WEEK"},{label:"Theo từng loại — toàn bộ khoảng ngày",value:"RANGE"}]
                            Accessible.name:"Chọn thu thập theo tuần hoặc theo từng loại"
                        }
                    }
                    Flow {
                        Layout.fillWidth:true; spacing:8; visible:mode.currentValue!=="INCREMENTAL"
                        StudioButton { objectName:"collectionSince2022"; text:"Từ năm 2022"; onClicked:page.preset("01/01/2022",T.vietnamDay(-1)) }
                        StudioButton { objectName:"collectionYesterday"; text:"Hôm qua"; onClicked:page.preset(T.vietnamDay(-1),T.vietnamDay(-1)) }
                        StudioButton { text:"Tháng trước"; onClicked:{var n=new Date();var a=new Date(n.getFullYear(),n.getMonth()-1,1);var b=new Date(n.getFullYear(),n.getMonth(),0);page.preset(Qt.formatDate(a,"dd/MM/yyyy"),Qt.formatDate(b,"dd/MM/yyyy"))} }
                    }
                    Label { Layout.fillWidth:true; wrapMode:Text.Wrap; color:"#526477"; text:mode.currentValue==="INCREMENTAL" ? "Cập nhật theo mốc đã thu thập, kiểm tra lại các ngày gần nhất. Lịch chạy hằng ngày được bật riêng trong Cài đặt." : "Bao gồm cả ngày bắt đầu và ngày kết thúc, theo giờ Việt Nam." }
                    RowLayout {
                        StudioButton { objectName:"collectionStart"; primary:true; text:page.submitting ? "Đang tạo tác vụ…" : (page.synthetic ? "Chạy thử bằng dữ liệu giả lập" : "Bắt đầu thu thập"); enabled:!page.submitting && !page.state.collection_blocked_reason; onClicked:page.submit() }
                        StudioButton { text:limits.visible ? "Ẩn giới hạn" : "Giới hạn mỗi tác vụ"; onClicked:limits.visible=!limits.visible }
                        Item { Layout.fillWidth:true }
                        Label { text:page.state.worker && page.state.worker.running ? "Bộ thu thập đang hoạt động" : "Tự bật bộ thu thập khi bắt đầu"; color:"#526477" }
                    }
                    GridLayout {
                        id:limits; visible:false; Layout.fillWidth:true; columns:4; columnSpacing:12
                        Label { text:"Số trang"; color:"#526477" }
                        StudioSpinBox { id:pages; from:1;to:10000;value:1000;editable:true;Accessible.name:"Số trang tối đa" }
                        Label { text:"Số yêu cầu"; color:"#526477" }
                        StudioSpinBox { id:requests;from:1;to:30000;value:3000;editable:true;Accessible.name:"Số yêu cầu tối đa" }
                        Label { text:"Thời gian (giây)"; color:"#526477" }
                        StudioSpinBox { id:seconds;from:1;to:86400;value:3600;editable:true;Accessible.name:"Thời gian tối đa tính bằng giây" }
                        Label { text:"Giãn cách (giây)"; color:"#526477" }
                        StudioSpinBox { id:pacing;from:3;to:300;value:3;editable:true;Accessible.name:"Khoảng cách giữa hai yêu cầu, tính bằng giây" }
                    }
                    Label { Layout.fillWidth:true;wrapMode:Text.Wrap;color:"#526477";text:mode.currentValue==="BACKFILL" && family.currentValue==="ALL" && cadence.currentValue==="WEEK"
                        ? "Mỗi tuần gồm 7 ngày từ ngày bắt đầu, xử lý từ cũ đến mới. Đủ cả 6 loại mới chuyển tuần; ngày đã có bằng chứng đầy đủ được bỏ qua. Mỗi lượt tối đa "+Math.round(seconds.value/60)+" phút / "+requests.value+" yêu cầu: nếu đã hoàn thành thêm ngày, tự lấy tiếp phần thiếu trong cùng tuần. Bị chặn hoặc không tiến triển thì dừng và báo rõ."
                        : "Mỗi loại tối đa "+Math.round(seconds.value/60)+" phút / "+requests.value+" yêu cầu mỗi lượt. Chạm giới hạn sẽ ghi Chưa đầy đủ; chạy lại cùng khoảng ngày để lấy tiếp phần thiếu." }
                    Label { objectName:"collectionResult"; visible:!!page.enqueueResult; Layout.fillWidth:true;wrapMode:Text.Wrap;text:page.enqueueResult;color:"#965a08" }
                    Label { visible:!!page.state.collection_blocked_reason; Layout.fillWidth:true;wrapMode:Text.Wrap;text:page.state.collection_blocked_reason||"";color:"#b42336" }
                    StudioButton { visible:!!page.state.collection_blocked_reason; text:"Chọn kho dữ liệu thật"; onClicked:page.selectDataRootRequested() }
                }
            }
            RowLayout {
                Label { text:"Tác vụ và tiến độ"; font.pixelSize:18;font.weight:Font.DemiBold;color:"#172b3a";Layout.fillWidth:true }
                Label { text:(page.state.queued||[]).length+" chờ · "+(page.state.active||[]).length+" đang chạy · "+(page.state.done||[]).length+" đã kết thúc"; color:"#526477" }
            }
            Label {
                Layout.fillWidth:true;wrapMode:Text.Wrap;color:"#087f75"
                text:{var s=page.state.active_status||{};var runs=s.runs||[];var last=runs.length?runs[runs.length-1]:{};var p=s.progress||{};return s.job_id ? T.label(last.lifecycle)+" · "+T.label(last.status)+(p.week_index ? " · Tuần "+p.week_index+"/"+p.week_count+" · "+T.collectionSpan(p.week_from,p.week_to,"BACKFILL")+" · Loại "+p.family_index+"/6" : "")+(p.family ? " · "+T.family(p.family)+" · "+p.rows+" dòng · "+p.pages+" trang" : "") : "Chưa có tác vụ đang chạy."}
            }
            StudioComboBox {
                id:target;objectName:"collectionTarget";Layout.fillWidth:true
                visible:(page.state.active||[]).length>0
                textRole:"label";valueRole:"value"
                model:(page.state.active||[]).map(function(j){return {label:(j.families||[]).map(T.familyShort).join(", ")+" · "+j.job_id,value:j.job_id}})
                Accessible.name:"Chọn tác vụ để tạm dừng, tiếp tục hoặc dừng"
                onActivated:page.selectedJobId=currentValue
                onModelChanged:{var at=model.findIndex(function(j){return j.value===page.selectedJobId});if(at>=0)currentIndex=at}
            }
            Label {
                Layout.fillWidth:true;wrapMode:Text.Wrap;color:"#965a08";visible:!!text
                text:{var runs=(page.state.active_status||{}).runs||[];var tasks=runs.length?runs[runs.length-1].tasks||[]:[];var detail=tasks.length?tasks[tasks.length-1].detail||{}:{};return detail.reason?T.message(detail.reason):""}
            }
            Flow {
                Layout.fillWidth:true; spacing:8
                StudioButton { objectName:"collectionPause";text:"Tạm dừng"; enabled:(page.state.active||[]).length>0; onClicked:backend.requestRead("jobAction","worker_command",JSON.stringify({command:"PAUSE",job_id:page.selectedJob()})) }
                StudioButton { objectName:"collectionResume";text:"Tiếp tục";enabled:(page.state.active||[]).length>0;onClicked:backend.requestRead("jobAction","worker_command",JSON.stringify({command:"RESUME",job_id:page.selectedJob()})) }
                StudioButton { text:"Dừng tác vụ";danger:true;enabled:(page.state.active||[]).length>0;onClicked:backend.requestRead("jobAction","worker_command",JSON.stringify({command:"STOP_GRACEFUL",job_id:page.selectedJob()})) }
                StudioButton { text:"Tắt bộ thu thập";onClicked:backend.requestRead("jobAction","worker_command",JSON.stringify({command:"FORCE_TERMINATE"})) }
                StudioButton { text:"Bật bộ thu thập";onClicked:backend.requestRead("jobAction","start_worker","{}") }
                StudioButton { text:"Tải lại";onClicked:page.reload() }
            }
            Repeater {
                model:(page.state.active||[]).concat(page.state.queued||[]).concat((page.state.done||[]).slice(-12).reverse())
                delegate:Rectangle {
                    required property var modelData
                    Layout.fillWidth:true;implicitHeight:60;color:"#ffffff";radius:5
                    RowLayout {
                        anchors.fill:parent;anchors.margins:12;spacing:16
                        Label { text:(modelData.scope||{}).cadence==="WEEK"?"Theo tuần · 6 loại":(modelData.families||[]).map(T.familyShort).join(", ");Layout.preferredWidth:120;font.weight:Font.DemiBold;color:"#172b3a" }
                        ColumnLayout {
                            Layout.fillWidth:true;spacing:3
                            Label { Layout.fillWidth:true;elide:Text.ElideRight;text:T.collectionSpan((modelData.scope||{}).from,(modelData.scope||{}).to,modelData.job_kind);color:"#172b3a" }
                            Label { text:modelData.job_id||"";font.pixelSize:12;color:"#526477" }
                        }
                        Label { text:modelData.queue_state==="queued"?"Đang chờ":T.label(modelData.status||modelData.lifecycle);color:modelData.status==="COMPLETED"?"#087f75":"#965a08" }
                    }
                }
            }
            Label { text:"Dữ liệu hôm qua · "+(page.overview.yesterday||"");font.pixelSize:18;font.weight:Font.DemiBold;color:"#172b3a" }
            Repeater {
                model:page.overview.families||[]
                delegate:RowLayout {
                    required property var modelData
                    Layout.fillWidth:true
                    Label { text:T.family(modelData.family);Layout.fillWidth:true;color:"#172b3a" }
                    Label { text:T.label(modelData.yesterday_status)+(modelData.readiness==="BLOCKED"?" · Chưa xác minh nguồn":"");color:modelData.yesterday_status==="COMPLETE"?"#087f75":"#965a08" }
                }
            }
        }
    }
}
