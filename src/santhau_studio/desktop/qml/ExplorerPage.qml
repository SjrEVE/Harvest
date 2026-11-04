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
    property bool advanced:false
    onVisibleChanged: if (visible && !busy && !state.hits.length) run()
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
        anchors.fill: parent; anchors.margins: 24; spacing: 14
        Label {text:"Tra cứu hồ sơ";font.pixelSize:24;font.weight:Font.DemiBold;color:"#172b3a"}
        RowLayout {
            Layout.fillWidth:true
            Label {text:"Tìm trong toàn bộ kho, mở hồ sơ để xem đầy đủ trường nguồn và lịch sử.";Layout.fillWidth:true;color:"#526477"}
            StudioButton {text:page.advanced?"Ẩn bộ lọc mở rộng":"Bộ lọc mở rộng";onClicked:page.advanced=!page.advanced}
        }
        GridLayout {
            columns: width < 850 ? 3 : 4; Layout.fillWidth: true
            StudioTextField { id: terms; Layout.fillWidth: true; placeholderText: "Mã / tên hồ sơ"; Accessible.name: placeholderText; onAccepted: page.run() }
            StudioComboBox { id: family; Layout.fillWidth: true; textRole:"label"; valueRole:"value"; model: [{label:"Tất cả loại hồ sơ",value:""}].concat(T.options(["TBMT","KHLCNT","KQLCNT","DU_AN","YCBG","HOP_DONG"])); Accessible.name:"Loại hồ sơ" }
            StudioTextField { id: investor; Layout.fillWidth: true; placeholderText: "Chủ đầu tư"; Accessible.name: placeholderText }
            StudioTextField { id: tax; visible:page.advanced; Layout.fillWidth: true; placeholderText: "Mã số thuế"; Accessible.name: placeholderText }
            StudioTextField { id: province; Layout.fillWidth: true; placeholderText: "Tỉnh / thành phố"; Accessible.name: placeholderText }
            StudioTextField { id: kind; visible:page.advanced; Layout.fillWidth: true; placeholderText: "Loại / hình thức"; Accessible.name: placeholderText }
            StudioTextField { id: from; visible:page.advanced; Layout.fillWidth: true; placeholderText: "Từ ngày (năm-tháng-ngày)"; Accessible.name: placeholderText }
            StudioTextField { id: to; visible:page.advanced; Layout.fillWidth: true; placeholderText: "Đến ngày (năm-tháng-ngày)"; Accessible.name: placeholderText }
            StudioComboBox { id: priceKind; visible:page.advanced; Layout.fillWidth: true; model: ["Giá gói thầu", "Giá trúng / hợp đồng"]; Accessible.name: "Loại giá" }
            StudioComboBox { id: presence; visible:page.advanced; Layout.fillWidth: true; objectName:"pricePresence"; textRole:"label"; valueRole:"value"; model:[{label:"Mọi trạng thái giá",value:""},{label:"Đã có giá",value:"KNOWN"},{label:"Chưa xác định giá",value:"UNKNOWN"}]; Accessible.name:"Trạng thái giá" }
            StudioTextField { id: minimum; visible:page.advanced; Layout.fillWidth: true; placeholderText: "Giá từ (số)"; Accessible.name: placeholderText }
            StudioTextField { id: maximum; visible:page.advanced; Layout.fillWidth: true; placeholderText: "Giá đến (số)"; Accessible.name: placeholderText }
            StudioTextField { id: currency; visible:page.advanced; Layout.fillWidth: true; placeholderText: "Tiền tệ, VD VND"; Accessible.name: placeholderText }
            StudioComboBox { id: sort; Layout.fillWidth: true; textRole: "label"; model: [
                {label:"Mã nội bộ",value:"record_id"},{label:"Mã hồ sơ",value:"natural_key"},
                {label:"Tên",value:"title"},{label:"Ngày công bố",value:"published_date"},
                {label:"Cập nhật",value:"updated"},{label:"Giá gói",value:"price"},{label:"Giá trúng",value:"award_price"}]; Accessible.name: "Sắp xếp" }
            StudioComboBox { id: direction; Layout.fillWidth: true; model: ["Giảm dần", "Tăng dần"]; Accessible.name: "Thứ tự" }
            StudioButton { primary:true; text: busy ? "Đang tìm…" : "Tìm hồ sơ"; enabled: !busy; onClicked: page.run() }
        }
        RowLayout {
            StudioButton { text: "Trang trước"; enabled: !busy && cursors.length > 0; onClicked: { var c = cursors.slice(); cursor = c.pop(); cursors = c; load() } }
            StudioButton { text: "Trang sau"; enabled: !busy && !!page.state.next_cursor; onClicked: { cursors = cursors.concat([cursor]); cursor = page.state.next_cursor; load() } }
            Label { text: "Trang " + (cursors.length + 1) + " · " + (page.state.hits || []).length + " hồ sơ"; color: "#526477" }
            Item { Layout.fillWidth: true }
            StudioButton { text: "Xuất kho CSV"; onClicked: page.exportRequested() }
        }
        Label { Layout.fillWidth: true; wrapMode: Text.Wrap; text: T.message(page.state.error) || (page.state.note === "NO_MATCH" ? "Không có hồ sơ khớp bộ lọc." : "Giá chưa xác định không được coi là 0. Ngày hiển thị theo giờ Việt Nam."); color: page.state.error ? "#b42336" : "#526477" }
        Rectangle {
            Layout.fillWidth:true;implicitHeight:40;color:"#e5ecf1";radius:5
            RowLayout {
                anchors.fill:parent;anchors.margins:10;spacing:16
                Label {text:"Hồ sơ / chủ đầu tư";Layout.fillWidth:true;font.weight:Font.DemiBold;color:"#172b3a"}
                Label {text:"Loại";Layout.preferredWidth:100;font.weight:Font.DemiBold;color:"#172b3a"}
                Label {text:"Công bố";Layout.preferredWidth:100;font.weight:Font.DemiBold;color:"#172b3a"}
                Label {text:"Giá gói thầu";Layout.preferredWidth:150;horizontalAlignment:Text.AlignRight;font.weight:Font.DemiBold;color:"#172b3a"}
            }
        }
        ListView {
            id: results; objectName: "searchResults"; Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; spacing: 5; model: page.state.hits || []; ScrollBar.vertical: ScrollBar {}
            delegate: ItemDelegate {
                required property var modelData
                width:results.width;height:76
                Accessible.name:modelData.natural_key+" "+modelData.title_display
                background:Rectangle {color:parent.activeFocus?"#ddefe7":(parent.hovered?"#edf5f4":"#ffffff");radius:5;border.width:parent.activeFocus?2:0;border.color:"#087f75"}
                contentItem:RowLayout {
                    spacing:16
                    ColumnLayout {
                        Layout.fillWidth:true;spacing:4
                        Label {Layout.fillWidth:true;elide:Text.ElideRight;text:modelData.title_display||"Chưa có tên hồ sơ";font.weight:Font.DemiBold;color:"#172b3a"}
                        Label {Layout.fillWidth:true;elide:Text.ElideRight;text:modelData.natural_key+" · "+(modelData.investor_display||"Chưa rõ chủ đầu tư");color:"#526477"}
                    }
                    Label {text:T.familyShort(modelData.family);Layout.preferredWidth:100;color:"#526477"}
                    Label {text:modelData.published_date||"Chưa xác định";Layout.preferredWidth:100;color:"#526477"}
                    Label {text:modelData.bid_price===null?"Chưa xác định":Number(modelData.bid_price).toLocaleString(Qt.locale("vi_VN"),"f",0)+" "+(modelData.currency||"");Layout.preferredWidth:150;horizontalAlignment:Text.AlignRight;color:"#172b3a";elide:Text.ElideRight}
                }
                onClicked:page.openRecord(modelData.record_id)
            }
        }
    }
}
