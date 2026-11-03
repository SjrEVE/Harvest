import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: page
    required property var bridge
    property int recordId: 0
    property var state: ({present:false})
    property var history: ({rows:[]})
    property string preview: ""
    property bool busy: false
    signal backRequested()
    signal identityRequested(string requestJson)
    onVisibleChanged: if (visible) reload()
    onRecordIdChanged: { history = {rows:[]}; preview = ""; if (visible) reload() }
    function reload() {
        if (recordId <= 0) { state = {present:false}; return }
        busy = true
        bridge.requestRead("detail", "record_detail", JSON.stringify({record_id:recordId}))
        loadHistory(0)
    }
    function loadHistory(before) {
        bridge.requestRead("history", "record_history", JSON.stringify({record_id:recordId,kind:section.model[section.currentIndex].value,before:before}))
    }
    Connections {
        target: page.bridge
        function onReadReady(key, serial, text) {
            var value = JSON.parse(text)
            if (key === "detail") { page.state = value; page.busy = false }
            if (key === "history") page.history = value
            if (key === "deepAction") page.preview = JSON.stringify(value,null,2)
            if (key === "raw") page.preview = value.error || ("SHA256 verified: " + value.sha256 + (value.truncated ? "\nPREVIEW TRUNCATED\n" : "\n") + value.text)
        }
    }
    Dialog {
        id:identityDialog; title:"Xác nhận liên kết thủ công (có audit / undo)"; modal:true
        width:Math.min(page.width-30,660); anchors.centerIn:parent; standardButtons:Dialog.Ok | Dialog.Cancel
        ColumnLayout {
            width:parent.width
            Label { Layout.fillWidth:true; wrapMode:Text.Wrap; text:"Thao tác chỉ sửa liên kết, giữ nguyên các hồ sơ gốc. Dừng collector trước khi ghi." }
            ComboBox { id:identityAction; model:["MERGE","CONFIRM_LINK","SPLIT","REJECT_LINK","UNDO"]; Accessible.name:"Thao tác identity" }
            TextField { id:identityRecords; Layout.fillWidth:true; placeholderText:"Record IDs, phân cách dấu phẩy"; text:String(page.recordId); Accessible.name:placeholderText }
            TextField { id:identityEntity; Layout.fillWidth:true; placeholderText:"Entity ID (bắt buộc khi SPLIT / REJECT)"; Accessible.name:placeholderText }
            TextField { id:identityUndo; Layout.fillWidth:true; placeholderText:"Action ID cần undo"; Accessible.name:placeholderText }
            TextField { id:identityReason; Layout.fillWidth:true; placeholderText:"Lý do bắt buộc"; Accessible.name:placeholderText }
            TextField { id:identityEvidence; Layout.fillWidth:true; placeholderText:"Bằng chứng định danh / tham chiếu"; Accessible.name:placeholderText }
        }
        onAccepted: page.identityRequested(JSON.stringify({action:identityAction.currentText,
            record_ids:identityRecords.text.split(",").filter(x=>x.trim()).map(x=>Number(x.trim())),
            entity_id:identityEntity.text,action_id:Number(identityUndo.text),reason:identityReason.text,evidence:{reference:identityEvidence.text}}))
    }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 18; spacing: 8
        RowLayout {
            Button { text: "← Kết quả tìm kiếm"; onClicked: page.backRequested() }
            Label { Layout.fillWidth: true; elide: Text.ElideMiddle; color: "#edf2f7"; text: state.record ? state.record.family + " · " + state.record.natural_key : "Chọn hồ sơ ở Data Explorer" }
            Button { text: "Tải lại"; enabled: recordId > 0 && !busy; onClicked: reload() }
        }
        Label { Layout.fillWidth: true; wrapMode: Text.Wrap; color: "#a9b4bf"; text: state.record ? state.record.source_key + " · version " + state.record.version_state + " " + (state.record.version_raw || "") + " · observations " + state.observations + " · " + state.record.integrity_status : (state.error || "") }
        Label { visible: !!state.legacy_unverified; color: "#edbc8b"; text: "LEGACY_UNVERIFIED — lịch sử nhập từ kho cũ, chưa có proof thu thập mới." }
        RowLayout {
            ComboBox {
                id: section; textRole: "label"; Layout.fillWidth: true; Accessible.name: "Loại lịch sử hồ sơ"
                model: [{label:"Content versions",value:"versions"},{label:"Raw revisions",value:"raw_revisions"},
                    {label:"Deep capture history",value:"deep_history"},{label:"Lots",value:"lots"},
                    {label:"Goods / BOQ",value:"goods_rows"},{label:"Documents",value:"documents"},{label:"Contractors",value:"contractor_memberships"}]
                onActivated: page.loadHistory(0)
            }
            Button { text: "Mới nhất"; enabled: recordId > 0; onClicked: page.loadHistory(0) }
            Button { text: "Cũ hơn"; enabled: !!history.has_more; onClicked: page.loadHistory(history.before) }
            Button { text: "Nguồn / nhóm / liên kết"; onClicked: preview = JSON.stringify({record:state.record,notice_groups:state.notice_groups,aliases:state.aliases,entity_links:state.entity_links,deep_captures:state.deep_captures,history_counts:state.history_counts},null,2) }
        }
        Label { color: "#a9b4bf"; text: history.error || ((history.rows || []).length + " dòng / " + ((state.history_counts || {})[section.model[section.currentIndex].value] || 0)) }
        RowLayout {
            Button { text:"Deep capture / refresh"; enabled:recordId>0; onClicked:bridge.requestRead("deepAction","enqueue_deep",JSON.stringify({record_id:recordId,lane:"DETAIL"})) }
            Button { text:"Liên kết / tách / undo…"; enabled:recordId>0; onClicked:identityDialog.open() }
            Button { text:"Audit liên kết"; onClicked:preview=JSON.stringify(state.identity_audit || [],null,2) }
        }
        SplitView {
            Layout.fillWidth: true; Layout.fillHeight: true; orientation: Qt.Horizontal
            ListView {
                id: rows; SplitView.preferredWidth: 300; SplitView.minimumWidth: 180; clip: true
                model: history.rows || []; spacing: 5; ScrollBar.vertical: ScrollBar {}
                delegate: ItemDelegate {
                    required property var modelData
                    width: rows.width; height: 72
                    background: Rectangle { color: parent.activeFocus ? "#324657" : "#202832"; radius: 4 }
                    contentItem: Label { wrapMode: Text.WrapAnywhere; maximumLineCount: 3; elide: Text.ElideRight; color: "#edf2f7"; text: (modelData.content_version_seq || modelData.revision_no || modelData.capture_seq || modelData.id) + " · " + (modelData.change_kind || modelData.reason || modelData.name || modelData.lane || "") + "\n" + (modelData.fetched_at_utc || modelData.first_observed_at_utc || modelData.captured_at_utc || "") }
                    onClicked: {
                        if (section.currentIndex === 1) bridge.requestRead("raw", "raw_object", JSON.stringify({sha:modelData.record_object_sha}))
                        else preview = modelData.normalized_json ? JSON.stringify(JSON.parse(modelData.normalized_json),null,2) : JSON.stringify(modelData,null,2)
                    }
                }
            }
            ScrollView {
                SplitView.fillWidth: true; clip: true
                TextArea { text: preview || "Chọn một dòng để xem normalized / raw / nguồn gốc."; readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere; color: "#e6e9ec"; font.family: "Consolas"; font.pixelSize: 12; Accessible.name: "Nội dung hồ sơ" }
            }
        }
    }
}
