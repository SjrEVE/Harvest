import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "UiText.js" as T

Window {
    id: root
    width: 1380
    height: 900
    minimumWidth: 1080
    minimumHeight: 620
    visible: true
    title: appTitle
    color: "#f3f6f8"

    property int pageIndex: 3
    property var workspace: ({synthetic:false,data_root:""})
    Component.onCompleted: backend.requestRead("workspace", "workspace_identity", "{}")
    property int detailRecordId: 0
    property var operation: ({ running: false })
    property string operationMessage: ""
    function startAction(action, source) {
        var result = root.parse(backend.startMaintenance(action, source || ""))
        operationMessage = T.result(result)
        operation = root.parse(backend.maintenanceStatus())
    }
    onClosing: function(close) {
        if (operation.running) {
            close.accepted = false
            operationMessage = "Chờ tác vụ hoàn tất trước khi đóng ứng dụng."
        }
    }
    Timer {
        interval: 750
        repeat: true
        running: root.operation.running
        onTriggered: {
            root.operation = root.parse(backend.maintenanceStatus())
            if (root.operation.progress) root.operationMessage = T.result(root.operation)
            if (!root.operation.running) {
                root.operationMessage = T.result(root.operation)
                page10.reload()
                if (root.pageIndex === 6) page7.reload()
            }
        }
    }
    property var navItems: [
        "Kho dữ liệu", "Tổng quan", "Nguồn dữ liệu", "Thu thập dữ liệu",
        "Mức độ thu thập", "Tra cứu hồ sơ", "Chi tiết hồ sơ",
        "Thu thập chi tiết", "Kiểm tra dữ liệu", "Cài đặt và sao lưu"
    ]

    function parse(text) {
        try { return JSON.parse(text) }
        catch (error) { return { error: "Không đọc được phản hồi: " + error } }
    }

    Connections {
        target: backend
        function onReadReady(key, serial, text) {
            if (key === "workspace") root.workspace = root.parse(text)
            var pages = {page1:page1,page2:page2,page3:page3,page4:page4,page5:page5,page8:page8,page9:page9,page10:page10}
            if (pages[key]) pages[key].state = root.parse(text)
            if (key === "integrityRaw") { evidenceText.text=T.rawPreview(root.parse(text)); evidenceDialog.open() }
            if (key === "proof") { evidenceText.text = T.pretty(root.parse(text)); evidenceDialog.open() }
            if (key === "jobAction") { page4.actionPending=false; page4.actionResult=T.result(root.parse(text)); page4.reload() }
            if (key === "collectionOverview") page4.overview = root.parse(text)
            if (key === "collectionAction") {
                page4.submitting = false
                var result = root.parse(text)
                page4.enqueueResult = T.collectionResult(result)
                page4.reload()
            }
        }
        function onDataRootChanged() {
            backend.requestRead("workspace","workspace_identity","{}")
            root.detailRecordId = 0
            page4.submitting = false
            page4.enqueueResult = ""
            rootPath.text = backend.dataRootPath()
            page1.reload()
            page2.reload()
            page3.reload()
            page4.reload()
            page5.reload()
            page6.state = { hits: [] }
            page7.state = ({present:false})
            page8.reload()
            page9.reload()
            page10.reload()
        }
    }

    Dialog {
        id:evidenceDialog; title:"Bằng chứng / cấu hình"; width:Math.min(root.width-60,850); height:Math.min(root.height-80,620)
        anchors.centerIn:parent; modal:true
        footer: DialogButtonBox {
            StudioButton { text:"Đóng"; DialogButtonBox.buttonRole:DialogButtonBox.RejectRole }
            onRejected:evidenceDialog.close()
        }
        ScrollView { anchors.fill:parent; TextArea { id:evidenceText; readOnly:true; selectByMouse:true; wrapMode:TextEdit.WrapAnywhere } }
    }
    RowLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.preferredWidth:220;Layout.fillHeight:true;color:"#172b3a"
            ColumnLayout {
                anchors.fill:parent;anchors.margins:16;spacing:8
                focus:true
                Keys.onDownPressed:root.pageIndex=Math.min(root.pageIndex+1,root.navItems.length-1)
                Keys.onUpPressed:root.pageIndex=Math.max(root.pageIndex-1,0)
                Label { text:"SĂN THẦU";font.pixelSize:21;font.weight:Font.DemiBold;color:"#ffffff";topPadding:14 }
                Label { text:"Không gian dữ liệu";color:"#b8cbd7";bottomPadding:22 }
                Repeater {
                    model:root.navItems
                    delegate:StudioButton {
                        required property int index
                        required property string modelData
                        Layout.fillWidth:true;implicitHeight:42
                        text:modelData;checked:root.pageIndex===index
                        contentItem:Text { text:parent.text;font.pixelSize:14;font.family:"Segoe UI";verticalAlignment:Text.AlignVCenter;color:root.pageIndex===index?"#ffffff":"#c5d6e0" }
                        background:Rectangle {radius:6;color:root.pageIndex===index?"#087f75":(parent.hovered?"#284354":"transparent");border.width:parent.activeFocus?2:0;border.color:"#89d4ca"}
                        onClicked:root.pageIndex=index
                        Accessible.name:modelData
                    }
                }
                Item { Layout.fillHeight:true }
                Label { text:"RC3 · Dữ liệu có nguồn gốc";color:"#b8cbd7";font.pixelSize: 13;bottomPadding:8 }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            Rectangle {
                Layout.fillWidth:true;Layout.preferredHeight:76
                color:root.workspace.synthetic?"#fff0d9":"#ffffff"
                RowLayout {
                    anchors.fill:parent;anchors.margins:20;spacing:16
                    ColumnLayout {
                        Layout.fillWidth:true;spacing:5
                        Label {text:root.workspace.synthetic?"KHÔNG PHẢI DỮ LIỆU THẬT — KHO THỬ NGOẠI TUYẾN":root.navItems[root.pageIndex];font.pixelSize:18;font.weight:Font.DemiBold;color:root.workspace.synthetic?"#943b0c":"#172b3a"}
                        Label {Layout.fillWidth:true;elide:Text.ElideMiddle;text:root.workspace.data_root||backend.dataRootPath();color:"#526477";font.pixelSize: 13}
                    }
                    Label { Layout.maximumWidth:360;wrapMode:Text.Wrap;text:page4.statusView.title||"Đang đọc trạng thái";color:page4.statusView.tone==="warning"?"#965a08":"#172b3a";font.weight:Font.DemiBold;visible:root.pageIndex!==3 }
                    StudioButton {text:"Chọn kho dữ liệu";onClicked:root.pageIndex=0}
                }
            }

            StackLayout {
                currentIndex: root.pageIndex
                Layout.fillWidth: true
                Layout.fillHeight: true

                // ------------------------------------------------ UI-01 Onboarding
                Item {
                    id: page1
                    property var state: ({})
                    Component.onCompleted: reload()
                    onVisibleChanged: if (visible) reload()
                    function reload() { backend.requestRead("page1", "onboarding", "{}") }

                    ScrollView {
                        anchors.fill: parent; anchors.margins: 18; clip: true
                        contentWidth: availableWidth
                        ColumnLayout {
                        width: parent.width
                        spacing: 10
                        RowLayout {
                            StudioButton { text: "Tạo kho / bật bộ thu thập"; onClicked: { root.pageIndex = 3; backend.requestRead("jobAction", "start_worker", "{}") } }
                            StudioButton { text: "Nhập kho dữ liệu cũ"; onClicked: root.pageIndex = 9 }
                        }


                        Text { text: "Thư mục dữ liệu"; color: "#526477"; font.pixelSize: 13 }
                        RowLayout {
                            StudioTextField {
                                id: rootPath
                                Layout.fillWidth: true
                                text: backend.dataRootPath()
                                color: "#172b3a"
                                background: Rectangle { color: "#ffffff"; border.color: "#333c45"; radius: 4 }
                            }
                            StudioButton {
                                text: "Kiểm tra"
                                onClicked: {
                                    var result = root.parse(backend.validateDataRoot(rootPath.text))
                                    validation.text = result.ok
                                        ? "Thư mục dùng được · còn trống " + Math.round(result.free_bytes / 1048576) + " MB"
                                        : "Không dùng được thư mục · " + T.message(result.error)
                                }
                            }
                            StudioButton {
                                text: "Dùng thư mục này"
                                onClicked: {
                                    var result = root.parse(backend.selectDataRoot(rootPath.text))
                                    validation.text = result.ok ? "Đã chọn · " + result.path : T.message(result.error)
                                }
                            }
                        }
                        Text { id: validation; color: "#087f75"; font.pixelSize: 13 }

                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: "#e5eeef" }

                        Text {
                            text: "Kho dữ liệu: " + (page1.state.warehouse_present ? "có" : "chưa")
                                  + "   ·   Dữ liệu hiển thị: " + (page1.state.serving_present ? "có" : "chưa")
                                  + "   ·   Tác vụ đang chờ: " + (page4.statusFresh && !page4.state.error
                                      ? (page4.state.queued || []).length : "Chưa xác nhận")
                            color: "#334d60"
                            font.pixelSize: 13
                        }
                        Text {
                            Layout.fillWidth: true
                            wrapMode: Text.Wrap
                            text: "Bộ thu thập: " + (page4.statusView.title || "Đang đọc trạng thái")
                            color: page4.statusView.tone === "error" ? "#b42336"
                                   : page4.statusView.tone === "warning" ? "#965a08" : "#172b3a"
                            font.pixelSize: 13
                        }
                        Text { text: T.message(page1.state.policy_note); color: "#526477"; font.pixelSize: 13 }
                        StudioButton { text: "Tải lại"; onClicked: page1.reload() }
                        Item { Layout.preferredHeight: 8 }
                        }
                    }
                }

                OverviewPage { id:page2 }

                // ------------------------------------------------ UI-03 Sources
                Item {
                    id: page3
                    property var state: ({ registry: [], boundary_rules: [] })
                    Component.onCompleted: reload()
                    onVisibleChanged: if (visible) reload()
                    function reload() { backend.requestRead("page3", "sources", "{}") }

                    ScrollView {
                        anchors.fill: parent; anchors.margins: 18; clip: true
                        contentWidth: availableWidth
                        ColumnLayout {
                        width: parent.width
                        spacing: 8

                        Repeater {
                            model: page3.state.families || []
                            delegate: Label {
                                required property var modelData
                                Layout.fillWidth:true; wrapMode:Text.Wrap; color:modelData.blocked_reason ? "#965a08" : "#087f75"
                                text:T.family(modelData.family) + " · " + T.label(modelData.state) + " · " + (modelData.blocked_reason ? T.message(modelData.blocked_reason) : "Quy tắc đã có bằng chứng")
                            }
                        }
                        StudioButton { text:"Lịch sử kiểm tra nguồn"; onClicked: { evidenceText.text=T.pretty(page3.state.access_events || []); evidenceDialog.open() } }
                        Text { text: "Kênh nguồn"; color: "#526477"; font.pixelSize: 13 }
                        Repeater {
                            model: page3.state.registry || []
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                height: 40
                                color: "#ffffff"
                                radius: 4
                                Row {
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.left: parent.left
                                    anchors.leftMargin: 10
                                    spacing: 22
                                    Text { text: T.label(modelData.source_key); color: "#172b3a"; font.pixelSize: 13; width: 170 }
                                    Text { text: T.label(modelData.display_name); color: "#334d60"; font.pixelSize: 13; width: 260 }
                                    Text {
                                        text: T.label(modelData.access_state)
                                        color: modelData.access_state === "ALLOWED_AND_VERIFIED" ? "#087f75"
                                             : (modelData.access_state === "BLOCKED" ? "#b42336" : "#965a08")
                                        font.pixelSize: 13
                                        width: 190
                                    }
                                    Text { text: modelData.verified_at_utc || ""; color: "#526477"; font.pixelSize: 13 }
                                }
                            }
                        }

                        StudioButton {text:"Xem quy tắc lọc và bằng chứng nguồn";onClicked:{evidenceText.text=T.pretty(page3.state.boundary_rules||[]);evidenceDialog.open()}}
                        Item { Layout.preferredHeight: 8 }
                        }
                    }
                }

                CollectionPage {
                    id:page4
                    synthetic:!!root.workspace.synthetic
                    onSelectDataRootRequested:root.pageIndex=0
                }
                CoveragePage { id:page5 }

                // ------------------------------------------------ UI-06 Data Explorer
                ExplorerPage {
                    id: page6
                    bridge: backend
                    onOpenRecord: function(recordId) { root.detailRecordId = recordId; root.pageIndex = 6 }
                    onExportRequested: root.startAction("export", "")
                }

                RecordPage {
                    id: page7
                    bridge: backend
                    recordId: root.detailRecordId
                    onBackRequested: root.pageIndex = 5
                    onIdentityRequested: function(requestJson) { root.startAction("identity", requestJson) }
                }

                // ------------------------------------------------ UI-08 Deep Collection
                Item {
                    id: page8
                    property var state: ({ rows: [], summary: {} })
                    onVisibleChanged: if (visible) reload()
                    function reload() { backend.requestRead("page8", "deep_queue", "{}") }

                    ScrollView {
                        anchors.fill: parent; anchors.margins: 18; clip: true
                        contentWidth: availableWidth
                        ColumnLayout {
                        width: parent.width
                        spacing: 6

                        Text {
                            text: {
                                var lanes = page8.state.lanes || []
                                return lanes.length
                                    ? "Nội dung thu thập: " + lanes.map(function (l) { return T.label(l.lane) + ": " + T.label(l.state) }).join(" · ")
                                    : ""
                            }
                            color: "#526477"
                            font.pixelSize: 13
                            wrapMode: Text.WrapAnywhere
                        }
                        Text {
                            text: page8.state.scope ? page8.state.scope + "\nĐã lấy: " + (page8.state.summary.FETCHED||0)
                                  + " · Nguồn rỗng: " + (page8.state.summary.SOURCE_EMPTY||0)
                                  + " · Lỗi parse: " + (page8.state.summary.PARSE_ERROR||0)
                                  + " · Bị chặn: " + (page8.state.summary.BLOCKED||0)
                                  + " · Lỗi lấy: " + (page8.state.summary.FAILED||0)
                                  + " · Chưa lấy: " + (page8.state.summary.NOT_COLLECTED||0)
                                  : "Đầy đủ: " + ((page8.state.summary || {}).FULL || 0)
                                  + " · Chưa đầy đủ: " + ((page8.state.summary || {}).PARTIAL || 0)
                                  + " · Chưa thu thập: " + ((page8.state.summary || {}).NOT_COLLECTED || 0)
                                  + " · Cần cập nhật: " + ((page8.state.summary || {}).stale || 0)
                            color: "#526477"
                            font.pixelSize: 13
                        }
                        Repeater {
                            model: page8.state.rows || []
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                height: 30
                                color: "#ffffff"
                                radius: 4
                                Row {
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.left: parent.left
                                    anchors.leftMargin: 10
                                    spacing: 18
                                    Text { text: T.familyShort(modelData.family); color: "#172b3a"; font.pixelSize: 13; width: 70 }
                                    Text { text: modelData.natural_key; color: "#334d60"; font.pixelSize: 13; width: 200 }
                                    Text { text: T.label(modelData.lane); color: "#526477"; font.pixelSize: 13; width: 170 }
                                    Text {
                                        text: T.label(modelData.completeness)
                                        color: modelData.completeness === "FULL" ? "#087f75" : "#965a08"
                                        font.pixelSize: 13; width: 110
                                    }
                                    Text {
                                        text: modelData.state ? T.label(modelData.state) : modelData.stale ? ("Cần cập nhật: " + T.message(modelData.stale_reason))
                                             : (modelData.needs_refresh ? "cần cập nhật" : "đã cập nhật")
                                        color: modelData.stale || modelData.needs_refresh ? "#965a08" : "#087f75"
                                        font.pixelSize: 13; width: 170
                                    }
                                    Text { text: "Thu thập lúc " + modelData.captured_at_utc; color: "#526477"; font.pixelSize: 13 }
                                }
                            }
                        }
                        Item { Layout.preferredHeight: 8 }
                        }
                    }
                }

                // ------------------------------------------------ UI-09 Data Integrity
                Item {
                    id: page9
                    property var state: ({ raw_objects: {}, quarantine: [], unresolved: {}, gaps: [], markers: [] })
                    onVisibleChanged: if (visible) reload()
                    function reload() { backend.requestRead("page9", "integrity", "{}") }

                    ScrollView {
                        anchors.fill: parent; anchors.margins: 18; clip: true
                        contentWidth: availableWidth
                        ColumnLayout {
                        width: parent.width
                        spacing: 8

                        RowLayout {
                            StudioButton { text:"Kiểm tra toàn vẹn"; enabled:!root.operation.running; onClicked:root.startAction("verify", "") }
                            StudioButton { text:"Chi tiết lỗi và kho cũ"; onClicked:{ evidenceText.text=T.pretty({inspection:page9.state.inspection,quarantine:page9.state.quarantine,scans:page9.state.markers}); evidenceDialog.open() } }
                        }
                        RowLayout {
                            StudioTextField { id:integrityHash; Layout.fillWidth:true; placeholderText:"Mã SHA256 của dữ liệu gốc cần kiểm tra"; Accessible.name:placeholderText }
                            StudioButton { text:"Xem dữ liệu gốc"; onClicked:backend.requestRead("integrityRaw","raw_object",JSON.stringify({sha:integrityHash.text})) }
                        }
                        Label { Layout.fillWidth:true; wrapMode:Text.Wrap; color:"#526477"; text:"Chi tiết hiển thị tối đa 50 lỗi và 20 lần nhập kho gần nhất; tổng đếm bên dưới là toàn kho." }
                        Text {
                            text: "Dữ liệu gốc: " + T.pretty(page9.state.raw_objects || {})
                                  + "   · Chưa xác định: " + ((page9.state.unresolved || {}).open || 0)
                                  + " / Đã xác định: " + ((page9.state.unresolved || {}).resolved || 0)
                            color: "#334d60"
                            font.pixelSize: 13
                        }
                        Text {
                            text: "Dữ liệu được cách ly để kiểm tra: " + (page9.state.quarantine_total || 0)
                            color: (page9.state.quarantine || []).length ? "#b42336" : "#087f75"
                            font.pixelSize: 13
                        }
                        Repeater {
                            model: page9.state.quarantine || []
                            delegate: Text {
                                required property var modelData
                                text: "  · " + T.message(modelData.failure) + " · " + modelData.sha12 + "… · " + modelData.quarantined_at_utc
                                color: "#965a08"
                                font.pixelSize: 13
                            }
                        }
                        Text { text: "Khoảng dữ liệu chênh lệch chưa được xác nhận"; color: "#526477"; font.pixelSize: 13; topPadding: 4 }
                        Repeater {
                            model: page9.state.gaps || []
                            delegate: Text {
                                required property var modelData
                                text: "  · " + T.label(modelData.kind) + " × " + modelData.count
                                color: "#965a08"
                                font.pixelSize: 13
                            }
                        }
                        Text {
                            visible: (page9.state.markers || []).length > 0
                            text: "Ghi nhận kiểm tra: " + T.pretty(page9.state.markers || [])
                            color: "#965a08"
                            font.pixelSize: 13
                            wrapMode: Text.WrapAnywhere
                        }
                        Item { Layout.preferredHeight: 8 }
                        }
                    }
                }

                // ------------------------------------------------ UI-10 Settings / Backup
                Item {
                    id: page10
                    property var state: ({ settings: [], backups: [], snapshot: {} })
                    onVisibleChanged: if (visible) reload()
                    function reload() { backend.requestRead("page10", "settings", "{}") }

                    Dialog {
                        id: restoreConfirm
                        title: "Khôi phục kho dữ liệu"
                        modal: true
                        anchors.centerIn: parent
                        footer: DialogButtonBox {
                            StudioButton { text:"Khôi phục dữ liệu"; DialogButtonBox.buttonRole:DialogButtonBox.AcceptRole }
                            StudioButton { text:"Hủy"; DialogButtonBox.buttonRole:DialogButtonBox.RejectRole }
                            onAccepted:restoreConfirm.accept(); onRejected:restoreConfirm.reject()
                        }
                        Text {
                            text: "Dừng bộ thu thập trước khi khôi phục. Kho hiện tại được giữ lại làm bản dự phòng.\nChỉ tiếp tục khi đúng bản sao lưu và thư mục dữ liệu đang hiển thị."
                            wrapMode: Text.WordWrap
                            width: 480
                        }
                        onAccepted: root.startAction("restore", restorePath.text)
                    }

                    ScrollView {
                        anchors.fill: parent; anchors.margins: 18; clip: true
                        contentWidth: availableWidth
                        ColumnLayout {
                        width: parent.width
                        spacing: 8

                        Text {
                            text: "Thư mục dữ liệu: " + (page10.state.data_root || "")
                                  + " · phiên bản " + (page10.state.app_version || "")
                            color: "#334d60"
                            font.pixelSize: 13
                        }
                        RowLayout {
                            StudioButton { text: "Tạo bản sao lưu"; enabled: !root.operation.running; onClicked: root.startAction("backup", "") }
                            StudioButton { text: "Kiểm tra dữ liệu"; enabled: !root.operation.running; onClicked: root.startAction("verify", "") }
                            StudioButton { text: "Xuất CSV"; enabled: !root.operation.running; onClicked: root.startAction("export", "") }
                        }
                        RowLayout {
                            StudioTextField {
                                id: restorePath
                                Layout.fillWidth: true
                                placeholderText: "Đường dẫn chứa bản sao lưu cần khôi phục"
                            }
                            StudioButton { text: "Khôi phục…"; enabled: !root.operation.running && restorePath.text.trim().length > 0; onClicked: restoreConfirm.open() }
                        }
                        RowLayout {
                            StudioButton { text: "Bật lịch 08:00 / 20:00"; enabled: !root.operation.running; onClicked: root.startAction("schedule-enable", "") }
                            StudioButton { text: "Tắt lịch"; enabled: !root.operation.running; onClicked: root.startAction("schedule-disable", "") }
                            StudioButton { text: "Xem lịch"; enabled: !root.operation.running; onClicked: root.startAction("schedule-status", "") }
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "Lịch tự động chỉ cập nhật các loại hồ sơ có quyền truy cập và quy tắc thu thập đã được xác minh."
                            color: "#526477"
                            font.pixelSize: 13
                            wrapMode: Text.WordWrap
                        }
                        Text {
                            Layout.fillWidth: true
                            text: root.operationMessage
                            color: root.operation.ok === false ? "#b42336" : "#087f75"
                            font.pixelSize: 13
                            wrapMode: Text.WrapAnywhere
                        }
                        Text {
                            text: {
                                var snap = page10.state.snapshot || {}
                                return snap.present
                                    ? ("Dữ liệu hiển thị cập nhật lúc " + (snap.built_at || "?") + (snap.stale ? " · Cần cập nhật" : " · Đã cập nhật"))
                                    : "Chưa có dữ liệu hiển thị"
                            }
                            color: "#526477"
                            font.pixelSize: 13
                        }

                        RowLayout {
                            StudioTextField { id: legacyPath; Layout.fillWidth: true; placeholderText: "Thư mục kho cũ hoặc gói dữ liệu đã xuất"; Accessible.name: placeholderText }
                            StudioButton { text: "Kiểm tra kho cũ"; enabled: !root.operation.running && legacyPath.text.length > 0; onClicked: root.startAction("inspect-legacy", legacyPath.text) }
                            StudioButton { text: "Nhập dữ liệu"; enabled: !root.operation.running && legacyPath.text.length > 0; onClicked: root.startAction("import-legacy", legacyPath.text) }
                        }
                        Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#965a08"; text: "Dữ liệu nhập từ kho cũ giữ nguyên lịch sử và nguồn gốc. Thông tin từng được kho cũ ghi nhận là đầy đủ vẫn cần bằng chứng mới để xác minh." }
                        RowLayout {
                            StudioButton { text:"Quy định truy cập và lưu trữ"; onClicked: { evidenceText.text=T.pretty({host_policy:page10.state.host_policy,snapshot:page10.state.snapshot,data_root:page10.state.data_root}); evidenceDialog.open() } }
                            StudioButton { text:"Nhật ký hoạt động"; onClicked: { evidenceText.text=T.pretty(page10.state.logs || []); evidenceDialog.open() } }
                            StudioButton { text:"Phiên bản và giấy phép"; onClicked: { evidenceText.text=page10.state.app_version + "\n" + T.message(page10.state.licensing); evidenceDialog.open() } }
                        }
                        StudioButton { visible:root.operation.running && root.operation.action === "import-legacy"; text:"Dừng nhập sau mục đang xử lý"; onClicked:backend.cancelMaintenance() }
                        StudioButton {text:"Chi tiết cấu hình kỹ thuật";onClicked:{evidenceText.text=T.pretty(page10.state.settings||[]);evidenceDialog.open()}}
                        Text { text: "Bản sao lưu"; color: "#526477"; font.pixelSize: 13; topPadding: 6 }
                        Repeater {
                            model: page10.state.backups || []
                            delegate: Text {
                                required property var modelData
                                text: "  · " + modelData.name + " · " + Math.round(modelData.bytes / 1024) + " KB · " + modelData.modified_at
                                color: "#334d60"
                                font.pixelSize: 13
                            }
                        }
                        Text {
                            visible: !(page10.state.backups || []).length
                            text: "  (chưa có bản sao lưu nào trong thư mục dữ liệu)"
                            color: "#526477"
                            font.pixelSize: 13
                        }
                        Item { Layout.preferredHeight: 8 }
                        }
                    }
                }
            }
        }
    }
}
