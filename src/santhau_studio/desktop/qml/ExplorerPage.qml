import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "UiText.js" as T

Item {
    id: page
    required property var bridge
    property var state: ({hits: []})
    property bool busy: false
    property var query: ({})
    property var cursors: []
    property string cursor: ""
    signal openRecord(int recordId)
    signal exportRequested()
    function run() {
        query = {browse:true,text:terms.text,family:family.currentValue,province:province.text,
            tax_id:tax.text,investor:investor.text,date_from:from.text,date_to:to.text,type:kind.text,
            price_kind:priceKind.currentIndex === 1 ? "award" : "bid",price_state:presence.currentValue,
            price_min:minimum.text,price_max:maximum.text,currency:currency.text,
            sort:sort.model[sort.currentIndex].value,direction:direction.currentIndex === 0 ? "desc" : "asc",limit:50}
        cursors = []; cursor = ""; load()
    }
    function load() {
        busy = true
        var request = Object.assign({}, query)
        if (cursor) request.cursor = cursor
        bridge.requestRead("explorer", "search", JSON.stringify(request))
    }
    Connections {
        target: page.bridge
        function onReadReady(key, serial, text) {
            if (key !== "explorer") return
            page.state = JSON.parse(text); page.busy = false
        }
    }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 18; spacing: 8
        GridLayout {
            columns: width < 850 ? 3 : 4; Layout.fillWidth: true
            TextField { id: terms; Layout.fillWidth: true; placeholderText: "Mã / tên hồ sơ"; Accessible.name: placeholderText; onAccepted: page.run() }
            ComboBox { id: family; Layout.fillWidth: true; textRole:"label"; valueRole:"value"; model: [{label:"Tất cả loại hồ sơ",value:""}].concat(T.options(["TBMT","KHLCNT","KQLCNT","DU_AN","YCBG","HOP_DONG"])); Accessible.name:"Loại hồ sơ" }
            TextField { id: investor; Layout.fillWidth: true; placeholderText: "Chủ đầu tư"; Accessible.name: placeholderText }
            TextField { id: tax; Layout.fillWidth: true; placeholderText: "Mã số thuế"; Accessible.name: placeholderText }
            TextField { id: province; Layout.fillWidth: true; placeholderText: "Tỉnh / thành phố"; Accessible.name: placeholderText }
            TextField { id: kind; Layout.fillWidth: true; placeholderText: "Loại / hình thức"; Accessible.name: placeholderText }
            TextField { id: from; Layout.fillWidth: true; placeholderText: "Từ ngày (năm-tháng-ngày)"; Accessible.name: placeholderText }
            TextField { id: to; Layout.fillWidth: true; placeholderText: "Đến ngày (năm-tháng-ngày)"; Accessible.name: placeholderText }
            ComboBox { id: priceKind; Layout.fillWidth: true; model: ["Giá gói thầu", "Giá trúng / hợp đồng"]; Accessible.name: "Loại giá" }
            ComboBox { id: presence; Layout.fillWidth: true; objectName:"pricePresence"; textRole:"label"; valueRole:"value"; model:[{label:"Mọi trạng thái giá",value:""},{label:"Đã có giá",value:"KNOWN"},{label:"Chưa xác định giá",value:"UNKNOWN"}]; Accessible.name:"Trạng thái giá" }
            TextField { id: minimum; Layout.fillWidth: true; placeholderText: "Giá từ (số)"; Accessible.name: placeholderText }
            TextField { id: maximum; Layout.fillWidth: true; placeholderText: "Giá đến (số)"; Accessible.name: placeholderText }
            TextField { id: currency; Layout.fillWidth: true; placeholderText: "Tiền tệ, VD VND"; Accessible.name: placeholderText }
            ComboBox { id: sort; Layout.fillWidth: true; textRole: "label"; model: [
                {label:"Mã nội bộ",value:"record_id"},{label:"Mã hồ sơ",value:"natural_key"},
                {label:"Tên",value:"title"},{label:"Ngày công bố",value:"published_date"},
                {label:"Cập nhật",value:"updated"},{label:"Giá gói",value:"price"},{label:"Giá trúng",value:"award_price"}]; Accessible.name: "Sắp xếp" }
            ComboBox { id: direction; Layout.fillWidth: true; model: ["Giảm dần", "Tăng dần"]; Accessible.name: "Thứ tự" }
            Button { text: busy ? "Đang tìm…" : "Tìm / áp dụng"; enabled: !busy; onClicked: page.run() }
        }
        RowLayout {
            Button { text: "Trang trước"; enabled: !busy && cursors.length > 0; onClicked: { var c = cursors.slice(); cursor = c.pop(); cursors = c; load() } }
            Button { text: "Trang sau"; enabled: !busy && !!page.state.next_cursor; onClicked: { cursors = cursors.concat([cursor]); cursor = page.state.next_cursor; load() } }
            Label { text: "Trang " + (cursors.length + 1) + " · " + (page.state.hits || []).length + " hồ sơ"; color: "#a9b4bf" }
            Item { Layout.fillWidth: true }
            Button { text: "Xuất kho CSV"; onClicked: page.exportRequested() }
        }
        Label { Layout.fillWidth: true; wrapMode: Text.Wrap; text: T.message(page.state.error) || (page.state.note === "NO_MATCH" ? "Không có hồ sơ khớp bộ lọc." : "Giá chưa xác định không được coi là 0. Ngày hiển thị theo giờ Việt Nam."); color: page.state.error ? "#f09c91" : "#a9b4bf" }
        ListView {
            id: results; objectName: "searchResults"; Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; spacing: 5; model: page.state.hits || []; ScrollBar.vertical: ScrollBar {}
            delegate: ItemDelegate {
                required property var modelData
                width: results.width; height: 64
                Accessible.name: modelData.natural_key + " " + modelData.title_display
                background: Rectangle { color: parent.activeFocus ? "#324657" : "#202832"; radius: 4 }
                contentItem: Column {
                    Label { width: parent.width; elide: Text.ElideRight; text: modelData.natural_key + " · " + (modelData.title_display || "(không tiêu đề)"); color: "#edf2f7" }
                    Label { width: parent.width; elide: Text.ElideRight; text: T.familyShort(modelData.family) + " · " + (modelData.investor_display || "Chưa rõ chủ đầu tư") + " · " + (modelData.published_date || "Chưa rõ ngày"); color: "#a9b4bf" }
                    Label { text: T.label(modelData.source_key) + " · Giá: " + (modelData.bid_price === null ? "chưa xác định" : modelData.bid_price) + " " + (modelData.currency || ""); color: "#9dbdae" }
                }
                onClicked: page.openRecord(modelData.record_id)
            }
        }
    }
}
