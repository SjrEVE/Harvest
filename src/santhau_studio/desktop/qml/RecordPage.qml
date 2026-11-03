import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "UiText.js" as T

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
            if (key === "deepAction") page.preview = T.result(value)
            if (key === "raw") page.preview = T.rawPreview(value)
        }
    }
    Dialog {
        id:identityDialog; title:"Xác nhận thay đổi liên kết (có nhật ký và hoàn tác)"; modal:true
        width:Math.min(page.width-30,660); anchors.centerIn:parent; footer:DialogButtonBox {
            Button { text:"Xác nhận thay đổi"; DialogButtonBox.buttonRole:DialogButtonBox.AcceptRole }
            Button { text:"Hủy"; DialogButtonBox.buttonRole:DialogButtonBox.RejectRole }
            onAccepted:identityDialog.accept(); onRejected:identityDialog.reject()
        }
        ColumnLayout {
            width:parent.width
            Label { Layout.fillWidth:true; wrapMode:Text.Wrap; text:"Thao tác chỉ sửa liên kết, giữ nguyên các hồ sơ gốc. Dừng bộ thu thập trước khi ghi." }
            ComboBox { id:identityAction; objectName:"identityAction"; textRole:"label"; valueRole:"value"; model:T.options(["MERGE","CONFIRM_LINK","SPLIT","REJECT_LINK","UNDO"]); Accessible.name:"Thao tác liên kết" }
            TextField { id:identityRecords; Layout.fillWidth:true; placeholderText:"Mã các hồ sơ nội bộ, ngăn cách bằng dấu phẩy"; text:String(page.recordId); Accessible.name:placeholderText }
            TextField { id:identityEntity; Layout.fillWidth:true; placeholderText:"Mã nhóm hồ sơ (bắt buộc khi tách hoặc bỏ liên kết)"; Accessible.name:placeholderText }
            TextField { id:identityUndo; Layout.fillWidth:true; placeholderText:"Mã nhật ký của thao tác cần hoàn tác"; Accessible.name:placeholderText }
            TextField { id:identityReason; Layout.fillWidth:true; placeholderText:"Lý do bắt buộc"; Accessible.name:placeholderText }
            TextField { id:identityEvidence; Layout.fillWidth:true; placeholderText:"Bằng chứng định danh / tham chiếu"; Accessible.name:placeholderText }
        }
        onAccepted: page.identityRequested(JSON.stringify({action:identityAction.currentValue,
            record_ids:identityRecords.text.split(",").filter(x=>x.trim()).map(x=>Number(x.trim())),
            entity_id:identityEntity.text,action_id:Number(identityUndo.text),reason:identityReason.text,evidence:{reference:identityEvidence.text}}))
    }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 18; spacing: 8
        RowLayout {
            Button { text: "← Kết quả tìm kiếm"; onClicked: page.backRequested() }
            Label { Layout.fillWidth: true; elide: Text.ElideMiddle; color: "#edf2f7"; text: page.state.record ? T.family(page.state.record.family) + " · " + page.state.record.natural_key : "Chọn hồ sơ ở màn Tra cứu hồ sơ" }
            Button { text: "Tải lại"; enabled: recordId > 0 && !busy; onClicked: reload() }
        }
        Label { Layout.fillWidth: true; wrapMode: Text.Wrap; color: "#a9b4bf"; text: page.state.record ? T.label(page.state.record.source_key) + " · phiên bản: " + T.label(page.state.record.version_state) + " " + (page.state.record.version_raw || "") + " · số lần ghi nhận: " + page.state.observations + " · " + T.label(page.state.record.integrity_status) : T.message(page.state.error) }
        Label { visible: !!page.state.legacy_unverified; color: "#edbc8b"; text: "Lịch sử nhập từ kho cũ, chưa có bằng chứng thu thập mới để xác minh." }
        RowLayout {
            ComboBox {
                id: section; textRole: "label"; Layout.fillWidth: true; Accessible.name: "Loại lịch sử hồ sơ"
                model: [{label:"Các phiên bản nội dung",value:"versions"},{label:"Lịch sử dữ liệu gốc",value:"raw_revisions"},
                    {label:"Lịch sử thu thập chi tiết",value:"deep_history"},{label:"Các lô hàng",value:"lots"},
                    {label:"Hàng hóa và khối lượng",value:"goods_rows"},{label:"Tài liệu",value:"documents"},{label:"Các nhà thầu",value:"contractor_memberships"}]
                onActivated: page.loadHistory(0)
            }
            Button { text: "Mới nhất"; enabled: recordId > 0; onClicked: page.loadHistory(0) }
            Button { text: "Cũ hơn"; enabled: !!history.has_more; onClicked: page.loadHistory(history.before) }
            Button { text: "Nguồn / nhóm / liên kết"; onClicked: preview=T.pretty({record:page.state.record,notice_groups:page.state.notice_groups,aliases:page.state.aliases,entity_links:page.state.entity_links,deep_captures:page.state.deep_captures,history_counts:page.state.history_counts}) }
        }
        Label { color: "#a9b4bf"; text: T.message(history.error) || ((history.rows || []).length + " dòng / " + ((page.state.history_counts || {})[section.model[section.currentIndex].value] || 0)) }
        RowLayout {
            Button { text:"Thu thập lại chi tiết"; enabled:recordId>0; onClicked:bridge.requestRead("deepAction","enqueue_deep",JSON.stringify({record_id:recordId,lane:"DETAIL"})) }
            Button { text:"Liên kết / tách / hoàn tác…"; enabled:recordId>0; onClicked:identityDialog.open() }
            Button { text:"Nhật ký liên kết"; onClicked:preview=T.pretty(page.state.identity_audit || []) }
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
                    contentItem: Label { wrapMode: Text.WrapAnywhere; maximumLineCount: 3; elide: Text.ElideRight; color: "#edf2f7"; text: (modelData.content_version_seq || modelData.revision_no || modelData.capture_seq || modelData.id) + " · " + (modelData.name || T.label(modelData.change_kind || modelData.reason || modelData.lane)) + "\n" + (modelData.fetched_at_utc || modelData.first_observed_at_utc || modelData.captured_at_utc || "") }
                    onClicked: {
                        if (section.currentIndex === 1) bridge.requestRead("raw", "raw_object", JSON.stringify({sha:modelData.record_object_sha}))
                        else preview = modelData.normalized_json ? T.pretty(JSON.parse(modelData.normalized_json)) : T.pretty(modelData)
                    }
                }
            }
            ScrollView {
                SplitView.fillWidth: true; clip: true
                TextArea { text: preview || "Chọn một dòng để xem nội dung chuẩn hóa, dữ liệu gốc hoặc thông tin nguồn gốc."; readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere; color: "#e6e9ec"; font.family: "Consolas"; font.pixelSize: 12; Accessible.name: "Nội dung hồ sơ" }
            }
        }
    }
}
