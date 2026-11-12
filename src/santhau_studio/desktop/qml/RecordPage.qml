import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "UiText.js" as T
import "RecordData.js" as R

Item {
    id: page
    required property var bridge
    property int recordId: 0
    property var state: ({present:false})
    property var history: ({rows:[]})
    property var selectedRow: null
    property string preview: ""
    property bool sourceMode: true
    property bool busy: false
    property bool historyBusy: false
    property bool actionPending: false
    property bool modeChosen: false
    property string actionMessage: ""
    property int detailSerial: -1
    property int historySerial: -1
    property int rawSerial: -1
    property int actionSerial: -1
    readonly property string historyKind: section.model[section.currentIndex].value
    readonly property bool supported: (state.deep_states || []).some(function(s){return !!s.supported})
    readonly property bool hasFailedRefresh: (state.deep_states || []).some(function(s){return s.state==="PARSE_ERROR" || s.state==="FAILED" || s.state==="BLOCKED"})
    signal backRequested()
    signal identityRequested(string requestJson)
    onVisibleChanged: if (visible) reload()
    onRecordIdChanged: {
        state={present:false}; history={rows:[]}; selectedRow=null; preview=""; actionMessage=""
        detailSerial=-1; historySerial=-1; rawSerial=-1; actionSerial=-1
        actionPending=false; modeChosen=false
        if (visible) reload()
    }
    function reload() {
        if (recordId <= 0) { state={present:false}; busy=false; return }
        busy=true
        detailSerial=bridge.requestRead("detail", "record_detail", JSON.stringify({record_id:recordId}))
        if (!sourceMode) loadHistory(0)
    }
    function loadHistory(before) {
        if (recordId<=0) return
        selectedRow=null; preview=""; rawSerial=-1; historyBusy=true
        historySerial=bridge.requestRead("history", "record_history", JSON.stringify({record_id:recordId,kind:historyKind,before:before}))
    }
    function choose(row) {
        selectedRow=row; rawSerial=-1
        if (sourceMode) preview=row.label+"\n\n"+(row.presence==="UNKNOWN"?"Nguồn chưa cung cấp":(typeof row.value==="object"?JSON.stringify(row.value,null,2):String(row.value)))
        else preview=R.describe(historyKind,row)
    }
    function collect(lane, force) {
        if (actionPending || busy || !supported) return
        actionPending=true; actionMessage="Đang lưu yêu cầu thu thập…"
        actionSerial=bridge.requestRead("deepAction", "enqueue_deep", JSON.stringify({record_id:recordId,lane:lane,force_refresh:!!force}))
    }
    Connections {
        target:page.bridge
        function onReadReady(key, serial, text) {
            var value=JSON.parse(text)
            if (key==="detail" && serial===page.detailSerial) {
                page.state=value; page.busy=false
                if (!page.modeChosen) {
                    page.modeChosen=true
                    if (((value.history_counts || {}).goods_rows || 0)>0) { page.sourceMode=false; section.currentIndex=0; page.loadHistory(0) }
                }
                if (page.sourceMode && !page.selectedRow && ((value.source_fields || {}).fields || []).length) page.choose(value.source_fields.fields[0])
            }
            if (key==="history" && serial===page.historySerial) {
                page.history=value; page.historyBusy=false; rows.positionViewAtBeginning()
                if (!page.sourceMode && (value.rows || []).length) page.choose(value.rows[0])
            }
            if (key==="deepAction" && serial===page.actionSerial) {
                page.actionPending=false
                page.actionMessage=value.ok?"Đã xếp hàng. Theo dõi kết quả ở Thu thập chi tiết; dữ liệu hiển thị sẽ đổi sau khi cập nhật bản tra cứu.":T.message(value.error || value.error_code)
            }
            if (key==="raw" && serial===page.rawSerial) page.preview=T.rawPreview(value)
        }
    }
    Dialog {
        id:identityDialog; title:"Xác nhận thay đổi liên kết (có nhật ký và hoàn tác)"; modal:true
        width:Math.min(page.width-30,660); anchors.centerIn:parent; footer:DialogButtonBox {
            StudioButton { text:"Xác nhận thay đổi"; DialogButtonBox.buttonRole:DialogButtonBox.AcceptRole }
            StudioButton { text:"Hủy"; DialogButtonBox.buttonRole:DialogButtonBox.RejectRole }
            onAccepted:identityDialog.accept(); onRejected:identityDialog.reject()
        }
        ColumnLayout {
            width:parent.width
            Label { Layout.fillWidth:true; wrapMode:Text.Wrap; text:"Thao tác chỉ sửa liên kết, giữ nguyên các hồ sơ gốc. Dừng bộ thu thập trước khi ghi." }
            StudioComboBox { id:identityAction; objectName:"identityAction"; textRole:"label"; valueRole:"value"; model:T.options(["MERGE","CONFIRM_LINK","SPLIT","REJECT_LINK","UNDO"]); Accessible.name:"Thao tác liên kết" }
            StudioTextField { id:identityRecords; Layout.fillWidth:true; placeholderText:"Mã các hồ sơ nội bộ, ngăn cách bằng dấu phẩy"; text:String(page.recordId); Accessible.name:placeholderText }
            StudioTextField { id:identityEntity; Layout.fillWidth:true; placeholderText:"Mã nhóm hồ sơ (bắt buộc khi tách hoặc bỏ liên kết)"; Accessible.name:placeholderText }
            StudioTextField { id:identityUndo; Layout.fillWidth:true; placeholderText:"Mã nhật ký của thao tác cần hoàn tác"; Accessible.name:placeholderText }
            StudioTextField { id:identityReason; Layout.fillWidth:true; placeholderText:"Lý do bắt buộc"; Accessible.name:placeholderText }
            StudioTextField { id:identityEvidence; Layout.fillWidth:true; placeholderText:"Bằng chứng định danh / tham chiếu"; Accessible.name:placeholderText }
        }
        onAccepted: page.identityRequested(JSON.stringify({action:identityAction.currentValue,
            record_ids:identityRecords.text.split(",").filter(x=>x.trim()).map(x=>Number(x.trim())),
            entity_id:identityEntity.text,action_id:Number(identityUndo.text),reason:identityReason.text,evidence:{reference:identityEvidence.text}}))
    }
    Menu {
        id:moreMenu
        MenuItem { text:"Thu thập lại chi tiết hồ sơ"; enabled:page.supported && !page.actionPending; onTriggered:page.collect("DETAIL",true) }
        MenuItem { text:"Liên kết / tách / hoàn tác…"; enabled:page.recordId>0; onTriggered:identityDialog.open() }
        MenuItem { text:"Thông tin nguồn và liên kết"; enabled:!!page.state.present; onTriggered:page.preview=T.pretty({record:page.state.record,notice_groups:page.state.notice_groups,aliases:page.state.aliases,entity_links:page.state.entity_links,relation_edges:page.state.relation_edges,deep_captures:page.state.deep_captures}) }
        MenuItem { text:"Nhật ký liên kết"; enabled:!!page.state.present; onTriggered:page.preview=T.pretty(page.state.identity_audit || []) }
    }
    ColumnLayout {
        anchors.fill:parent; anchors.margins:20; spacing:10
        RowLayout {
            StudioButton { text:"Kết quả tìm kiếm"; onClicked:page.backRequested() }
            Label { Layout.fillWidth:true; elide:Text.ElideMiddle; textFormat:Text.PlainText; font.pixelSize:16; font.weight:Font.DemiBold; color:"#172b3a"; text:page.state.record?T.family(page.state.record.family)+" · "+page.state.record.natural_key:"Chọn hồ sơ để xem chi tiết" }
            StudioButton { text:"Tải lại"; enabled:page.recordId>0 && !page.busy; onClicked:page.reload() }
            StudioButton { text:"Thao tác khác"; onClicked:moreMenu.popup() }
        }
        Label {
            Layout.fillWidth:true; wrapMode:Text.Wrap; color:page.state.error?"#b42336":"#526477"; font.pixelSize:13
            text:page.busy?"Đang đọc hồ sơ…":page.state.error?T.message(page.state.error):page.state.record?
                T.label(page.state.record.source_key)+" · phiên bản nguồn "+(page.state.record.version_raw || "chưa rõ")+
                " · ghi nhận gần nhất "+T.timestampVN(page.state.record.last_seen_at_utc)+
                (page.state.legacy_unverified?" · dữ liệu nhập cũ, chưa xác minh":""):"Mở một hồ sơ từ màn Tra cứu hồ sơ hoặc Thu thập chi tiết."
        }
        Flow {
            Layout.fillWidth:true; Layout.preferredHeight:childrenRect.height; spacing:16
            Repeater {
                model:page.state.deep_states || []
                delegate:Label {
                    required property var modelData
                    font.pixelSize:13; color:modelData.state==="FETCHED"?"#087f75":modelData.state==="FAILED" || modelData.state==="PARSE_ERROR"?"#b42336":"#526477"
                    text:T.label(modelData.lane)+": "+T.label(modelData.state)
                    ToolTip.visible:hover.hovered && !!modelData.error
                    ToolTip.text:T.message(modelData.error || "")
                    HoverHandler { id:hover }
                }
            }
        }
        RowLayout {
            visible:!!page.state.present; spacing:10
            StudioButton { objectName:"collectHsmt"; text:"Lấy hàng hóa HSMT"; enabled:page.supported && !page.busy && !page.actionPending; onClicked:page.collect("HSMT",false) }
            StudioButton { objectName:"collectReoffer"; text:"Lấy thông tin chào giá"; enabled:page.supported && !page.busy && !page.actionPending; onClicked:page.collect("REOFFER",false) }
            Label { Layout.fillWidth:true; wrapMode:Text.Wrap; text:page.hasFailedRefresh?"Có lần lấy gặp lỗi. Nội dung đã lưu trước đó vẫn được giữ để đối chiếu.":page.supported?"Nguồn công khai đã xác minh cho loại hồ sơ này.":"Chưa hỗ trợ thu thập chi tiết cho loại hồ sơ này."; color:page.hasFailedRefresh?"#965a08":"#526477"; font.pixelSize:13 }
        }
        Label { visible:!!page.actionMessage; Layout.fillWidth:true; wrapMode:Text.Wrap; text:page.actionMessage; color:"#526477"; font.pixelSize:13 }
        RowLayout {
            StudioButton { text:"Trường nguồn"; primary:page.sourceMode; onClicked:{page.modeChosen=true;page.sourceMode=true;page.selectedRow=null;page.preview="";page.rawSerial=-1;if (((page.state.source_fields || {}).fields || []).length) page.choose(page.state.source_fields.fields[0])} }
            StudioButton { text:"Hàng hóa và tài liệu"; primary:!page.sourceMode; onClicked:{page.modeChosen=true;page.sourceMode=false;page.loadHistory(0)} }
            StudioComboBox {
                id:section; objectName:"recordSection"; visible:!page.sourceMode; Layout.fillWidth:true
                textRole:"label"; Accessible.name:"Loại nội dung hồ sơ"
                model:[{label:"Hàng hóa và khối lượng",value:"goods_rows"},{label:"Các lô hàng",value:"lots"},
                    {label:"Tài liệu",value:"documents"},{label:"Đơn vị tham gia",value:"contractor_memberships"},
                    {label:"Phiên bản nội dung",value:"versions"},{label:"Lịch sử dữ liệu gốc",value:"raw_revisions"},
                    {label:"Lịch sử thu thập chi tiết",value:"deep_history"}]
                onActivated:page.loadHistory(0)
            }
            Label { visible:page.sourceMode; Layout.fillWidth:true; text:((page.state.source_fields || {}).fields || []).length+" trường nguồn"; color:"#526477"; font.pixelSize:13 }
        }
        Label {
            objectName:"recordHistoryCount"
            Layout.fillWidth:true; wrapMode:Text.Wrap; font.pixelSize:13
            color:page.history.error || (page.state.source_fields || {}).error?"#b42336":"#526477"
            text:page.sourceMode?((page.state.source_fields || {}).error || "Chọn một trường để đọc giá trị nguồn."):
                page.historyBusy?"Đang đọc nội dung…":page.history.error ||
                (page.history.rows || []).length+" dòng đang hiển thị / "+((page.state.history_counts || {})[page.historyKind] || 0)+" dòng đã lưu"
        }
        SplitView {
            Layout.fillWidth:true; Layout.fillHeight:true; orientation:Qt.Horizontal
            ListView {
                id:rows; objectName:"recordRows"; SplitView.preferredWidth:300; SplitView.minimumWidth:220
                clip:true; spacing:1; reuseItems:true; enabled:!page.busy && (page.sourceMode || !page.historyBusy)
                model:page.sourceMode?((page.state.source_fields || {}).fields || []):(page.history.rows || [])
                ScrollBar.vertical:ScrollBar {}
                delegate:ItemDelegate {
                    id:entry; required property var modelData
                    width:rows.width; height:86
                    background:Rectangle { color:entry.hovered || entry.activeFocus || page.selectedRow===entry.modelData?"#e0efec":"#ffffff"; border.color:entry.activeFocus?"#087f75":"transparent" }
                    contentItem:ColumnLayout {
                        spacing:4
                        Label { Layout.fillWidth:true; textFormat:Text.PlainText; wrapMode:Text.Wrap; maximumLineCount:2; elide:Text.ElideRight; font.pixelSize:13; font.weight:Font.DemiBold; color:"#172b3a"; text:page.sourceMode?entry.modelData.label:R.title(page.historyKind,entry.modelData) }
                        Label { Layout.fillWidth:true; textFormat:Text.PlainText; elide:Text.ElideRight; font.pixelSize:13; color:"#526477"; text:page.sourceMode?(entry.modelData.presence==="UNKNOWN"?"Nguồn chưa cung cấp":typeof entry.modelData.value==="object"?"Nội dung có cấu trúc":String(entry.modelData.value)):R.subtitle(page.historyKind,entry.modelData) }
                    }
                    onClicked:page.choose(modelData)
                }
                Label { anchors.centerIn:parent; width:parent.width-24; wrapMode:Text.Wrap; horizontalAlignment:Text.AlignHCenter; visible:!rows.count && !page.busy && !page.historyBusy; text:"Chưa có nội dung ở mục này. Trạng thái phía trên cho biết đã thu thập hay chưa."; color:"#526477"; font.pixelSize:13 }
            }
            ScrollView {
                SplitView.fillWidth:true; SplitView.minimumWidth:220; clip:true
                TextArea { objectName:"recordPreview"; text:page.preview || "Chọn một dòng để đọc nội dung."; readOnly:true; selectByMouse:true; wrapMode:TextEdit.WrapAnywhere; textFormat:TextEdit.PlainText; color:"#172b3a"; font.family:"Segoe UI"; font.pixelSize:14; padding:16; Accessible.name:"Nội dung hồ sơ"; background:Rectangle {color:"#ffffff"} }
            }
        }
        RowLayout {
            Layout.fillWidth:true
            StudioButton { text:"Thông tin kỹ thuật"; enabled:!!page.selectedRow; onClicked:page.preview=T.pretty(page.selectedRow) }
            StudioButton { text:"Đọc dữ liệu gốc"; visible:!page.sourceMode && page.historyKind==="raw_revisions"; enabled:!!page.selectedRow; onClicked:{page.preview="Đang kiểm tra và đọc dữ liệu gốc…";page.rawSerial=page.bridge.requestRead("raw","raw_object",JSON.stringify({sha:page.selectedRow.record_object_sha}))} }
            Item { Layout.fillWidth:true }
            StudioButton { text:"Trang đầu"; visible:!page.sourceMode; enabled:!page.historyBusy && page.recordId>0; onClicked:page.loadHistory(0) }
            StudioButton { text:"Trang sau"; visible:!page.sourceMode; enabled:!page.historyBusy && !!page.history.has_more; onClicked:page.loadHistory(page.history.before) }
        }
    }
}
