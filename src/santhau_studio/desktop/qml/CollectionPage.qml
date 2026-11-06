import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "UiText.js" as T
import "CollectionState.js" as S

Item {
    id: page
    objectName: "collectionPage"
    property var state: ({queued:[],active:[],done:[]})
    property var overview: ({families:[]})
    property string enqueueResult: ""
    property bool submitting: false
    property bool synthetic: false
    property string selectedJobId: ""
    property string actionResult: ""
    property bool actionPending: false
    property real nowMs: Date.now()
    property real lastReplyMs: 0
    property string overviewSnapshotAt: ""
    property bool statusFresh: lastReplyMs > 0 && nowMs-lastReplyMs < 10000 && !state.error
    property var statusView: S.describe(state, statusFresh)
    property var selectedEntry: (state.active||[]).find(function(j){return j.job_id===page.selectedJob()}) || {}
    onStateChanged: {
        lastReplyMs=Date.now()
        var snapshot=state.snapshot||{}
        var totals=snapshot.row_counts||{}
        var revision=JSON.stringify([snapshot.built_at,snapshot.stale,totals.source_records,totals.observations])
        if (snapshot.built_at && revision!==overviewSnapshotAt) {
            overviewSnapshotAt=revision
            backend.requestRead("collectionOverview","collection_overview","{}")
        }
    }
    function command(name) {
        actionPending=true
        actionResult="Đang gửi yêu cầu…"
        backend.requestRead("jobAction","worker_command",JSON.stringify({command:name,job_id:page.selectedJob()}))
    }
    function selectedJob() { return target.currentValue || ((state.active||[]).length ? state.active[0].job_id : "") }
    signal selectDataRootRequested()
    function reload() {
        backend.requestRead("page4","jobs","{}")

    }
    function preset(first,last) { mode.currentIndex=0; from.text=first; to.text=last; enqueueResult="" }
    function submit() {
        var request={job_kind:mode.currentValue,family:family.currentValue,
            request_limits:{max_pages:pages.value,max_requests:requests.value,max_seconds:seconds.value,pacing_seconds:pacing.value}}
        if (mode.currentValue === "BACKFILL" && family.currentValue === "ALL") request.cadence=cadence.currentValue
        if (mode.currentValue === "INCREMENTAL") {
            request.auto_range=true
            if (refreshRecent.checked) request.refresh_recent=true
        } else {request.date_from=from.text;request.date_to=to.text}
        submitting=true; enqueueResult=""
        backend.requestRead("collectionAction","start_collection",JSON.stringify(request))
    }
    Component.onCompleted: { reload(); backend.requestRead("collectionOverview","collection_overview","{}") }
    onVisibleChanged: if (visible) reload()
    Timer { interval:2000; running:true; repeat:true; onTriggered:{page.nowMs=Date.now();page.reload()} }
    Timer { interval:30000; running:page.visible; repeat:true; onTriggered:backend.requestRead("collectionOverview","collection_overview","{}") }
    ScrollView {
        anchors.fill:parent; anchors.margins:24; contentWidth:availableWidth; clip:true
        ColumnLayout {
            width:parent.width; spacing:16
            Label { text:"Thu thập dữ liệu"; color:"#172b3a"; font.pixelSize:24; font.weight:Font.DemiBold }
            Label { Layout.fillWidth:true; wrapMode:Text.Wrap; text:"Chọn khoảng thời gian một lần. Các loại hồ sơ được xử lý tuần tự, ngày đã có đủ bằng chứng sẽ được bỏ qua."; color:"#526477" }
            CollectionStatus { Layout.fillWidth:true; view:page.statusView; snapshot:page.state.snapshot||{}; checkedAt:page.state.checked_at||""; snapshotError:((page.state.worker||{}).runtime||{}).snapshot_error||""; phaseSince:((page.state.worker||{}).runtime||{}).phase_since||""; nowMs:page.nowMs }
            RowLayout {
                Label { text:"Tác vụ và điều khiển"; font.pixelSize:18;font.weight:Font.DemiBold;color:"#172b3a";Layout.fillWidth:true }
                Label { text:{var c=page.state.counts||{};return (c.running||0)+" đang chạy · "+(c.paused||0)+" tạm dừng · "+(c.queued||0)+" chờ"+(c.unknown?" · "+c.unknown+" chưa xác nhận":"")} color:"#526477" }
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
            Label { Layout.fillWidth:true;wrapMode:Text.Wrap;visible:!!page.actionResult;text:page.actionResult;color:"#526477" }
            Flow {
                Layout.fillWidth:true; spacing:8
                StudioButton { objectName:"collectionPause";text:"Tạm dừng"; enabled:page.statusFresh && !page.actionPending && !!page.selectedEntry.can_pause; onClicked:page.command("PAUSE") }
                StudioButton { objectName:"collectionResume";text:"Tiếp tục";enabled:page.statusFresh && !page.actionPending && !!page.selectedEntry.can_resume;onClicked:page.command("RESUME") }
                StudioButton { objectName:"collectionStop";text:"Dừng tác vụ";danger:true;enabled:page.statusFresh && !page.actionPending && !!page.selectedEntry.can_stop;onClicked:page.command("STOP_GRACEFUL") }
                StudioButton { objectName:"collectorStop";text:"Tắt bộ thu thập";enabled:page.statusFresh && !page.actionPending && !!(page.state.worker||{}).pipe_ok && (((page.state.worker||{}).runtime||{}).phase!=="STOPPING_WORKER");onClicked:page.command("FORCE_TERMINATE") }
                StudioButton { objectName:"collectorStart";text:"Bật bộ thu thập";enabled:page.statusFresh && !page.actionPending && !(page.state.worker||{}).running;onClicked:{page.actionPending=true;backend.requestRead("jobAction","start_worker","{}")} }
                StudioButton { text:"Kiểm tra lại trạng thái";onClicked:page.reload() }
            }
            Label {
                objectName:"collectorStopNote"
                Layout.fillWidth:true;wrapMode:Text.Wrap;color:"#526477"
                text:"Tắt bộ thu thập chỉ dừng tiến trình. Việc đang chạy được xếp lại và hàng đợi được giữ, giống lệnh Linux stop. Muốn dừng một tác vụ, dùng Dừng tác vụ."
            }
            Label {
                Layout.fillWidth:true;wrapMode:Text.Wrap;color:"#526477";visible:!!text
                text:{var s=page.state.last_status||{};var runs=s.runs||[];var last=runs.length?runs[runs.length-1]:{};var tasks=last.tasks||[];var d=tasks.length?tasks[tasks.length-1].detail||{}:{};return s.job_id?"Kết quả tác vụ trước: "+T.label(last.status)+(d.reason?". "+T.message(d.reason):""):""}
            }
            Label { text:"Tạo tác vụ thu thập";font.pixelSize:18;font.weight:Font.DemiBold;color:"#172b3a" }
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
                    Label {
                        objectName:"collectionPolicyNote"
                        Layout.fillWidth:true; wrapMode:Text.Wrap; color:"#526477"
                        text:(page.state.collection_policy && page.state.collection_policy.notice_vi) || "Chính sách đang dùng: chỉ lấy phần còn thiếu. Bản hết hạn và đối chiếu tự động không chạy trừ khi được bật."
                    }
                    CheckBox {
                        id:refreshRecent
                        objectName:"collectionRefreshRecent"
                        visible:mode.currentValue==="INCREMENTAL"
                        text:"Đọc lại ngày đã chứng minh (gia hạn, hủy, đính chính)"
                        Accessible.name:text
                    }
                    Label { Layout.fillWidth:true; wrapMode:Text.Wrap; color:"#526477"; text:mode.currentValue==="INCREMENTAL" ? "Chỉ lấy khoảng chưa đủ bằng chứng. Ngày đã chứng minh được bỏ qua, nên sửa đổi muộn không tự vào kho trừ khi chọn ô phía trên. Lịch hằng ngày bật riêng trong Cài đặt." : mode.currentValue==="RECONCILE" ? "Đối chiếu một lượt các ngày đã chứng minh trong khoảng đã chọn. Lịch đối chiếu tự động không chạy theo chính sách thiếu-only; lượt này chỉ chạy vì bạn bấm bắt đầu." : "Bao gồm cả ngày bắt đầu và ngày kết thúc, theo giờ Việt Nam." }
                    RowLayout {
                        StudioButton { objectName:"collectionStart"; primary:true; text:page.submitting ? "Đang tạo tác vụ…" : (page.synthetic ? "Chạy thử bằng dữ liệu giả lập" : "Bắt đầu thu thập"); enabled:!page.submitting && !page.state.collection_blocked_reason; onClicked:page.submit() }
                        StudioButton { text:limits.visible ? "Ẩn giới hạn" : "Giới hạn mỗi tác vụ"; onClicked:limits.visible=!limits.visible }
                        Item { Layout.fillWidth:true }
                        Label { text:"Đóng cửa sổ không tắt bộ thu thập"; color:"#526477" }
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
                        ? "Ưu tiên tuần mới nhất rồi lùi dần, đủ 6 loại trong mỗi tuần. Nếu chọn hôm nay, lấy dữ liệu hiện có của cả 6 loại rồi tiếp tục ngày cũ; hôm nay chỉ chốt đầy đủ sau khi hết ngày. Ngày đã có bằng chứng đầy đủ được bỏ qua. Mỗi lượt tối đa "+Math.round(seconds.value/60)+" phút / "+requests.value+" yêu cầu: nếu đã hoàn thành thêm ngày, tự lấy tiếp phần thiếu. Lỗi mạng hoặc bị chặn sẽ dừng và báo rõ."
                        : "Mỗi loại tối đa "+Math.round(seconds.value/60)+" phút / "+requests.value+" yêu cầu mỗi lượt. Chạm giới hạn sẽ ghi Chưa đầy đủ; chạy lại cùng khoảng ngày để lấy tiếp phần thiếu." }
                    Label { objectName:"collectionResult"; visible:!!page.enqueueResult; Layout.fillWidth:true;wrapMode:Text.Wrap;text:page.enqueueResult;color:"#965a08" }
                    Label { visible:!!page.state.collection_blocked_reason; Layout.fillWidth:true;wrapMode:Text.Wrap;text:page.state.collection_blocked_reason||"";color:"#b42336" }
                    StudioButton { visible:!!page.state.collection_blocked_reason; text:"Chọn kho dữ liệu thật"; onClicked:page.selectDataRootRequested() }
                }
            }
            Label { text:"Hàng đợi và kết quả";font.pixelSize:18;font.weight:Font.DemiBold;color:"#172b3a" }
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
                        Label { text:S.jobLabel(modelData);color:modelData.status==="COMPLETED"?"#087f75":"#965a08" }
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
