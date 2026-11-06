import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "UiText.js" as T

Item {
    id: page
    required property var bridge
    property var state: ({rows:[], summary:{}})
    property bool busy: false
    property int requestSerial: -1
    property int pageOffset: 0
    readonly property var summary: state.summary || ({})
    signal recordRequested(int recordId)
    onVisibleChanged: if (visible) reload()

    function requestPage(offset) {
        pageOffset = offset
        busy = true
        requestSerial = bridge.requestRead("deepQueue", "deep_queue", JSON.stringify({
            offset:offset, limit:50, state:statusFilter.currentValue || "",
            lane:laneFilter.currentValue || "", text:query.text
        }))
    }
    function reload() { requestPage(0) }
    function tone(value) {
        if (value === "FAILED" || value === "PARSE_ERROR") return "#b42336"
        if (value === "STALE" || value === "BLOCKED" || value === "PARTIAL") return "#965a08"
        return value === "FETCHED" || value === "SOURCE_EMPTY" ? "#087f75" : "#526477"
    }
    Connections {
        target: page.bridge
        function onReadReady(key, serial, text) {
            if (key !== "deepQueue" || serial !== page.requestSerial) return
            page.state = JSON.parse(text)
            page.busy = false
            entries.positionViewAtBeginning()
        }
    }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 20; spacing: 12
        Label {
            Layout.fillWidth: true; wrapMode: Text.Wrap; color: "#526477"; font.pixelSize: 13
            text: (page.state.scope || "Theo dõi nội dung đã lấy và những hồ sơ cần kiểm tra lại.")
                + ((page.state.refresh_policy && page.state.refresh_policy.notice_vi) ? " " + page.state.refresh_policy.notice_vi : "")
        }
        Flow {
            Layout.fillWidth: true; Layout.preferredHeight: childrenRect.height; spacing: 20
            Repeater {
                model: [
                    {label:"Đã lấy", count:(page.state.scope ? page.summary.FETCHED || 0 : page.summary.FULL || 0), tone:"#087f75"},
                    {label:"Chưa lấy", count:page.summary.NOT_COLLECTED || 0, tone:"#526477"},
                    {label:"Cần cập nhật", count:page.state.scope ? page.summary.STALE || 0 : page.summary.stale || 0, tone:"#965a08"},
                    {label:"Đã giữ một phần", count:page.summary.partial_saved || 0, tone:"#965a08"},
                    {label:"Cần kiểm tra", count:(page.summary.FAILED || 0)+(page.summary.PARSE_ERROR || 0)+(page.summary.BLOCKED || 0), tone:"#b42336"},
                    {label:"Chưa có mục bổ sung", count:page.summary.SOURCE_EMPTY || 0, tone:"#526477"}
                ]
                delegate: Label {
                    required property var modelData
                    text: modelData.label + "  " + modelData.count.toLocaleString(Qt.locale("vi_VN"), "f", 0)
                    color: modelData.tone; font.pixelSize: 14; font.weight: Font.DemiBold
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 10
            StudioTextField {
                id: query; objectName: "deepQuery"; Layout.fillWidth: true
                placeholderText: "Tìm mã hồ sơ…"; Accessible.name: "Tìm mã hồ sơ thu thập chi tiết"
                onAccepted: page.reload()
            }
            StudioComboBox {
                id: statusFilter; objectName: "deepStateFilter"; Layout.preferredWidth: 178
                textRole:"label"; valueRole:"value"; Accessible.name:"Lọc trạng thái thu thập"
                model: [{label:"Mọi trạng thái",value:""},{label:"Lỗi lấy dữ liệu",value:"FAILED"},
                    {label:"Lỗi phân tích",value:"PARSE_ERROR"},{label:"Bị chặn",value:"BLOCKED"},
                    {label:"Cần cập nhật",value:"STALE"},{label:"Đã giữ một phần",value:"PARTIAL"},{label:"Chưa lấy",value:"NOT_COLLECTED"},
                    {label:"Đã lấy",value:"FETCHED"},{label:"Chưa có mục bổ sung",value:"SOURCE_EMPTY"}]
                onActivated: page.reload()
            }
            StudioComboBox {
                id: laneFilter; objectName:"deepLaneFilter"; Layout.preferredWidth:164
                textRole:"label"; valueRole:"value"; Accessible.name:"Lọc loại nội dung"
                model:[{label:"Mọi nội dung",value:""},{label:"Hàng hóa HSMT",value:"HSMT"},
                    {label:"Chi tiết hồ sơ",value:"DETAIL"},{label:"Thông tin chào giá",value:"REOFFER"}]
                onActivated: page.reload()
            }
            StudioButton { text:"Tải lại"; enabled:!page.busy; onClicked:page.requestPage(page.pageOffset) }
        }
        Label {
            Layout.fillWidth:true; wrapMode:Text.Wrap; font.pixelSize:13
            color:page.state.error?"#b42336":"#526477"
            text: page.busy ? "Đang đọc trạng thái thu thập…" : page.state.error ||
                (((page.state.snapshot || {}).stale ? "Dữ liệu hiển thị đang chờ cập nhật. " : "") +
                 "Chọn một dòng để mở hồ sơ. Thời gian hiển thị theo giờ Việt Nam." +
                 (page.state.legacy_count ? " Có " + page.state.legacy_count + " mục lịch sử ngoài phạm vi thống kê trên." : ""))
        }
        Rectangle {
            Layout.fillWidth:true; height:32; color:"#e6edf1"
            RowLayout {
                anchors.fill:parent; anchors.leftMargin:12; anchors.rightMargin:12; spacing:12
                Label { text:"Mã hồ sơ"; Layout.preferredWidth:145; color:"#526477"; font.pixelSize:13 }
                Label { text:"Nội dung"; Layout.preferredWidth:165; color:"#526477"; font.pixelSize:13 }
                Label { text:"Trạng thái"; Layout.fillWidth:true; color:"#526477"; font.pixelSize:13 }
                Label { text:"Lần xử lý gần nhất"; Layout.preferredWidth:145; color:"#526477"; font.pixelSize:13 }
            }
        }
        ListView {
            id:entries; objectName:"deepEntries"; Layout.fillWidth:true; Layout.fillHeight:true
            clip:true; spacing:1; reuseItems:true; enabled:!page.busy
            model:page.state.rows || []; ScrollBar.vertical:ScrollBar {}
            delegate: ItemDelegate {
                id:entry; required property var modelData
                objectName:"deepEntry-"+modelData.source_record_id+"-"+modelData.lane
                width:entries.width; height:68
                Accessible.name: modelData.natural_key + ", " + T.label(modelData.lane) + ", " + T.label(modelData.state || modelData.completeness) + ". Mở hồ sơ."
                background:Rectangle { color:entry.hovered || entry.activeFocus?"#e0efec":"#ffffff"; border.color:entry.activeFocus?"#087f75":"transparent" }
                contentItem:ColumnLayout {
                    spacing:5
                    RowLayout {
                        Layout.fillWidth:true; spacing:12
                        Label { text:entry.modelData.natural_key; textFormat:Text.PlainText; Layout.preferredWidth:145; elide:Text.ElideRight; font.pixelSize:13; font.weight:Font.DemiBold; color:"#172b3a" }
                        Label { text:T.label(entry.modelData.lane); Layout.preferredWidth:165; elide:Text.ElideRight; font.pixelSize:13; color:"#526477" }
                        Label { text:T.label(entry.modelData.state || entry.modelData.completeness); Layout.fillWidth:true; elide:Text.ElideRight; font.pixelSize:13; color:page.tone(entry.modelData.state) }
                        Label { text:T.timestampVN(entry.modelData.updated_at_utc || entry.modelData.captured_at_utc); Layout.preferredWidth:145; font.pixelSize:13; color:"#526477" }
                    }
                    Label {
                        Layout.fillWidth:true; elide:Text.ElideRight; textFormat:Text.PlainText; font.pixelSize:13; color:"#526477"
                        text: entry.modelData.error ? T.message(entry.modelData.error) : entry.modelData.legacy ? "Bằng chứng thu thập trước đây" :
                            entry.modelData.state === "NOT_COLLECTED" ? "Chưa có lần lấy nội dung này" :
                            entry.modelData.state === "STALE" ? (entry.modelData.stale ? "Nguồn đã đổi hoặc bản lưu bị đánh dấu cần lấy lại" : "Hạn làm mới đã qua và chính sách đang cho phép lấy lại") :
                            entry.modelData.state === "PARTIAL" ? "Đã giữ dòng đọc được. Dòng lỗi được ghi lại, không xóa dữ liệu tốt." :
                            entry.modelData.refresh_due_elapsed && entry.modelData.state === "FETCHED" ? "Đã lưu. Hạn làm mới đã qua; bộ thu thập không tự lấy lại." :
                            entry.modelData.state === "FETCHED" ? "Đã lưu phản hồi nguồn và dữ liệu chi tiết" : "Mở hồ sơ để xem bằng chứng và tình trạng từng nội dung"
                    }
                }
                onClicked:page.recordRequested(modelData.source_record_id)
            }
            Label {
                anchors.centerIn:parent; width:Math.min(parent.width-40,500); wrapMode:Text.Wrap; horizontalAlignment:Text.AlignHCenter
                visible:!page.busy && !entries.count; color:"#526477"; font.pixelSize:14
                text:page.state.error?"Chưa đọc được danh sách. Dùng Tải lại để thử lại.":page.state.present===false?"Chưa có bản dữ liệu hiển thị. Mở Thu thập dữ liệu để kiểm tra bộ thu thập.":"Không có nội dung khớp bộ lọc. Thử trạng thái khác hoặc xóa mã hồ sơ."
            }
        }
        RowLayout {
            Layout.fillWidth:true
            Label { Layout.fillWidth:true; color:"#526477"; font.pixelSize:13; text:entries.count?((page.state.offset || 0)+1)+"–"+((page.state.offset || 0)+entries.count)+" / "+(page.state.total || entries.count)+" nội dung":"0 nội dung" }
            StudioButton { objectName:"deepPrevious"; text:"Trang trước"; enabled:!page.busy && page.pageOffset>0; onClicked:page.requestPage(Math.max(0,page.pageOffset-50)) }
            StudioButton { objectName:"deepNext"; text:"Trang sau"; enabled:!page.busy && !!page.state.has_more; onClicked:page.requestPage(page.pageOffset+50) }
        }
    }
}
