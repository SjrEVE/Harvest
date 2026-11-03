import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "UiText.js" as T

Window {
    id: root
    width: 1180
    height: 760
    minimumWidth: 960
    minimumHeight: 620
    visible: true
    title: appTitle
    color: "#14181d"

    property int pageIndex: 0
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
        "01 · Bắt đầu", "02 · Tổng quan", "03 · Nguồn dữ liệu", "04 · Tác vụ thu thập",
        "05 · Mức độ thu thập", "06 · Tra cứu hồ sơ", "07 · Chi tiết hồ sơ",
        "08 · Thu thập chi tiết", "09 · Kiểm tra dữ liệu", "10 · Cài đặt và sao lưu"
    ]

    function parse(text) {
        try { return JSON.parse(text) }
        catch (error) { return { error: "Không đọc được phản hồi: " + error } }
    }

    Connections {
        target: backend
        function onReadReady(key, serial, text) {
            var pages = {page1:page1,page2:page2,page3:page3,page4:page4,page5:page5,page8:page8,page9:page9,page10:page10}
            if (pages[key]) pages[key].state = root.parse(text)
            if (key === "integrityRaw") { evidenceText.text=T.rawPreview(root.parse(text)); evidenceDialog.open() }
            if (key === "proof") { evidenceText.text = T.pretty(root.parse(text)); evidenceDialog.open() }
            if (key === "jobAction") { page4.enqueueResult = T.result(root.parse(text)); page4.reload() }
        }
        function onDataRootChanged() {
            root.detailRecordId = 0
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
            Button { text:"Đóng"; DialogButtonBox.buttonRole:DialogButtonBox.RejectRole }
            onRejected:evidenceDialog.close()
        }
        ScrollView { anchors.fill:parent; TextArea { id:evidenceText; readOnly:true; selectByMouse:true; wrapMode:TextEdit.WrapAnywhere } }
    }
    RowLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.preferredWidth: 216
            Layout.fillHeight: true
            color: "#181d23"

            Column {
                id: navColumn
                anchors.fill: parent
                anchors.margins: 12
                spacing: 6
                focus: true
                Keys.onDownPressed: root.pageIndex = Math.min(root.pageIndex + 1, root.navItems.length - 1)
                Keys.onUpPressed: root.pageIndex = Math.max(root.pageIndex - 1, 0)

                Text {
                    text: "SĂN THẦU"
                    color: "#7fd1a4"
                    font.pixelSize: 18
                    font.bold: true
                }
                Text {
                    text: "Kho dữ liệu"
                    color: "#8b959f"
                    font.pixelSize: 12
                    bottomPadding: 12
                }

                Repeater {
                    model: root.navItems
                    delegate: Rectangle {
                        required property int index
                        required property string modelData
                        width: parent.width
                        height: 36
                        radius: 4
                        color: root.pageIndex === index ? "#2a333d" : "transparent"
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.left: parent.left
                            anchors.leftMargin: 10
                            text: modelData
                            color: root.pageIndex === index ? "#e6e9ec" : "#9aa4ae"
                            font.pixelSize: 13
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: root.pageIndex = index
                        }
                    }
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 52
                color: "#1b2026"
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: 18
                    text: root.navItems[root.pageIndex]
                    color: "#e6e9ec"
                    font.pixelSize: 16
                    font.bold: true
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
                    function reload() { backend.requestRead("page1", "onboarding", "{}") }

                    ScrollView {
                        anchors.fill: parent; anchors.margins: 18; clip: true
                        contentWidth: availableWidth
                        ColumnLayout {
                        width: parent.width
                        spacing: 10
                        RowLayout {
                            Button { text: "Tạo kho / bật bộ thu thập"; onClicked: { root.pageIndex = 3; backend.requestRead("jobAction", "start_worker", "{}") } }
                            Button { text: "Nhập kho dữ liệu cũ"; onClicked: root.pageIndex = 9 }
                        }


                        Text { text: "Thư mục dữ liệu"; color: "#9aa4ae"; font.pixelSize: 12 }
                        RowLayout {
                            TextField {
                                id: rootPath
                                Layout.fillWidth: true
                                text: backend.dataRootPath()
                                color: "#e6e9ec"
                                background: Rectangle { color: "#20262d"; border.color: "#333c45"; radius: 4 }
                            }
                            Button {
                                text: "Kiểm tra"
                                onClicked: {
                                    var result = root.parse(backend.validateDataRoot(rootPath.text))
                                    validation.text = result.ok
                                        ? "Thư mục dùng được · còn trống " + Math.round(result.free_bytes / 1048576) + " MB"
                                        : "Không dùng được thư mục · " + T.message(result.error)
                                }
                            }
                            Button {
                                text: "Dùng thư mục này"
                                onClicked: {
                                    var result = root.parse(backend.selectDataRoot(rootPath.text))
                                    validation.text = result.ok ? "Đã chọn · " + result.path : T.message(result.error)
                                }
                            }
                        }
                        Text { id: validation; color: "#7fd1a4"; font.pixelSize: 12 }

                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: "#2a333d" }

                        Text {
                            text: "Kho dữ liệu: " + (page1.state.warehouse_present ? "có" : "chưa")
                                  + "   ·   Dữ liệu hiển thị: " + (page1.state.serving_present ? "có" : "chưa")
                                  + "   ·   Tác vụ đang chờ: " + (page1.state.queued_jobs || 0)
                            color: "#c8d0d8"
                            font.pixelSize: 13
                        }
                        Text {
                            text: "Bộ thu thập: " + (page1.state.worker && page1.state.worker.running
                                  ? ("đang chạy · mã tiến trình " + page1.state.worker.pid) : "không chạy")
                            color: page1.state.worker && page1.state.worker.running ? "#7fd1a4" : "#d1a47f"
                            font.pixelSize: 13
                        }
                        Text { text: T.message(page1.state.policy_note); color: "#8b959f"; font.pixelSize: 11 }
                        Button { text: "Tải lại"; onClicked: page1.reload() }
                        Item { Layout.preferredHeight: 8 }
                        }
                    }
                }

                // ------------------------------------------------ UI-02 Control Center
                Item {
                    id: page2
                    property var state: ({ families: [] })
                    Component.onCompleted: reload()
                    onVisibleChanged: if (visible) reload()
                    function reload() { backend.requestRead("page2", "control_center", "{}") }

                    Timer {
                        interval: 3000
                        running: page2.visible
                        repeat: true
                        onTriggered: page2.reload()
                    }

                    ScrollView {
                        anchors.fill: parent; anchors.margins: 18; clip: true
                        contentWidth: availableWidth
                        ColumnLayout {
                        width: parent.width
                        spacing: 8

                        Text {
                            text: {
                                var snap = page2.state.snapshot || {}
                                if (!snap.present) return "Chưa có bản dữ liệu hiển thị"
                                return "Dữ liệu hiển thị cập nhật lúc " + (snap.built_at || "?")
                                     + (snap.stale ? ("  ·  Cần cập nhật" + (snap.degraded ? " / Dữ liệu bị thiếu hoặc lỗi" : "")) : "  ·  Đã cập nhật")
                                     + (snap.degraded ? ("  ·  thiếu " + T.pretty(snap.degradation)) : "")
                            }
                            color: (page2.state.snapshot && page2.state.snapshot.stale) ? "#e0b46a" : "#7fd1a4"
                            font.pixelSize: 12
                        }
                        Text {
                            text: {
                                var worker = page2.state.worker || {}
                                var disk = page2.state.disk || {}
                                return "Bộ thu thập: " + (worker.running ? "chạy" : "dừng")
                                     + " · kết nối " + (worker.pipe_ok ? "đã thông" : "chưa có")
                                     + " · đĩa " + Math.round((disk.bytes || 0) / 1048576) + " MB"
                                     + (disk.sampled ? " (mẫu " + disk.limit + " tệp)" : "")
                            }
                            color: "#9aa4ae"
                            font.pixelSize: 12
                        }

                        Text { text: "Loại hồ sơ"; color: "#6f7a84"; font.pixelSize: 11 }
                        Repeater {
                            model: page2.state.families || []
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                height: 34
                                color: "#1b2128"
                                radius: 4
                                Row {
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.left: parent.left
                                    anchors.leftMargin: 10
                                    spacing: 26
                                    Text { text: T.familyShort(modelData.family); color: "#e6e9ec"; font.pixelSize: 13; width: 90 }
                                    Text { text: modelData.records + " hồ sơ"; color: "#c8d0d8"; font.pixelSize: 12; width: 120 }
                                    Text { text: modelData.versions + " phiên bản nội dung"; color: "#c8d0d8"; font.pixelSize: 12; width: 120 }
                                    Text { text: modelData.raw_revisions + " lần đổi dữ liệu gốc"; color: "#c8d0d8"; font.pixelSize: 12; width: 120 }
                                    Text { text: modelData.observations + " lần ghi nhận"; color: "#c8d0d8"; font.pixelSize: 12; width: 100 }
                                    Text {
                                        text: modelData.last_verified_at_utc ? ("Xác minh lúc " + modelData.last_verified_at_utc) : "chưa có bằng chứng"
                                        color: modelData.last_verified_at_utc ? "#7fd1a4" : "#d1a47f"
                                        font.pixelSize: 12
                                    }
                                }
                            }
                        }
                        Text {
                            visible: !(page2.state.families || []).length
                            text: "Chưa có dữ liệu hiển thị. Hãy thu thập hoặc nhập kho dữ liệu cũ."
                            color: "#8b959f"
                            font.pixelSize: 12
                        }
                        Text {
                            visible: !!page2.state.error
                            text: T.message(page2.state.error)
                            color: "#e07a7a"
                            font.pixelSize: 11
                        }
                        Item { Layout.preferredHeight: 8 }
                        }
                    }
                }

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
                                Layout.fillWidth:true; wrapMode:Text.Wrap; color:modelData.blocked_reason ? "#edbc8b" : "#9dd9b4"
                                text:T.family(modelData.family) + " · " + T.label(modelData.state) + " · " + (modelData.blocked_reason ? T.message(modelData.blocked_reason) : "Quy tắc đã có bằng chứng")
                            }
                        }
                        Button { text:"Lịch sử kiểm tra nguồn"; onClicked: { evidenceText.text=T.pretty(page3.state.access_events || []); evidenceDialog.open() } }
                        Text { text: "Kênh nguồn"; color: "#9aa4ae"; font.pixelSize: 12 }
                        Repeater {
                            model: page3.state.registry || []
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                height: 40
                                color: "#1b2128"
                                radius: 4
                                Row {
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.left: parent.left
                                    anchors.leftMargin: 10
                                    spacing: 22
                                    Text { text: T.label(modelData.source_key); color: "#e6e9ec"; font.pixelSize: 13; width: 170 }
                                    Text { text: T.label(modelData.display_name); color: "#c8d0d8"; font.pixelSize: 12; width: 260 }
                                    Text {
                                        text: T.label(modelData.access_state)
                                        color: modelData.access_state === "ALLOWED_AND_VERIFIED" ? "#7fd1a4"
                                             : (modelData.access_state === "BLOCKED" ? "#e07a7a" : "#d1a47f")
                                        font.pixelSize: 12
                                        width: 190
                                    }
                                    Text { text: modelData.verified_at_utc || ""; color: "#8b959f"; font.pixelSize: 11 }
                                }
                            }
                        }

                        Text {
                            text: "Quy tắc lọc thời gian — cần xác minh với nguồn trước khi chạy"
                            color: "#9aa4ae"
                            font.pixelSize: 12
                            topPadding: 8
                        }
                        Repeater {
                            model: page3.state.boundary_rules || []
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                height: 34
                                color: "#1b2128"
                                radius: 4
                                Row {
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.left: parent.left
                                    anchors.leftMargin: 10
                                    spacing: 22
                                    Text { text: T.familyShort(modelData.family); color: "#e6e9ec"; font.pixelSize: 12; width: 90 }
                                    Text { text: T.label(modelData.boundary_kind) + " · " + T.label(modelData.precision); color: "#c8d0d8"; font.pixelSize: 12; width: 200 }
                                    Text { text: "Bản " + modelData.rule_version; color: "#8b959f"; font.pixelSize: 12; width: 50 }
                                    Text {
                                        text: modelData.canary_verified ? "Đã xác minh" : "Chưa xác minh"
                                        color: modelData.canary_verified ? "#7fd1a4" : "#e07a7a"
                                        font.pixelSize: 12
                                    }
                                }
                            }
                        }
                        Item { Layout.preferredHeight: 8 }
                        }
                    }
                }

                // ------------------------------------------------ UI-04 Collection Jobs
                Item {
                    id: page4
                    property var state: ({ queued: [], active: [], done: [] })
                    property string enqueueResult: ""
                    Component.onCompleted: reload()
                    onVisibleChanged: if (visible) reload()
                    function reload() { backend.requestRead("page4", "jobs", "{}") }
                    Timer { interval:1500; repeat:true; running:page4.visible; onTriggered:page4.reload() }

                    ScrollView {
                        anchors.fill: parent; anchors.margins: 18; clip: true
                        contentWidth: availableWidth
                        ColumnLayout {
                        width: parent.width
                        spacing: 8

                        Text {
                            text: "Bộ thu thập: " + (page4.state.worker && page4.state.worker.running
                                  ? "chạy · kết nối " + (page4.state.worker.pipe_ok ? "đã thông" : "chưa có")
                                  : "chưa chạy — tác vụ sẽ chờ trong hàng đợi")
                            color: page4.state.worker && page4.state.worker.running ? "#7fd1a4" : "#d1a47f"
                            font.pixelSize: 12
                        }

                        GridLayout {
                            columns: width < 1000 ? 2 : 3
                            columnSpacing: 8
                            rowSpacing: 8
                            ComboBox {
                                id: kindBox
                                objectName: "collectionMode"
                                textRole: "label"; valueRole: "value"
                                model: T.options(["BACKFILL", "INCREMENTAL", "RECONCILE"])
                                Layout.fillWidth: true
                            }
                            TextField {
                                id: familyField
                                placeholderText: "Loại hồ sơ (ví dụ: TBMT)"
                                text: "TBMT"
                                Layout.fillWidth: true
                                color: "#e6e9ec"
                                background: Rectangle { color: "#20262d"; border.color: "#333c45"; radius: 4 }
                            }
                            TextField {
                                id: fromField
                                placeholderText: "Từ: 2026-11-01T17:00:00Z"
                                Layout.fillWidth: true
                                color: "#e6e9ec"
                                background: Rectangle { color: "#20262d"; border.color: "#333c45"; radius: 4 }
                            }
                            TextField {
                                id: toField
                                placeholderText: "Đến: 2026-11-02T17:00:00Z"
                                Layout.fillWidth: true
                                color: "#e6e9ec"
                                background: Rectangle { color: "#20262d"; border.color: "#333c45"; radius: 4 }
                            }
                            Button {
                                text: "Thêm tác vụ"
                                onClicked: {
                                    backend.requestRead("jobAction", "enqueue_collection", JSON.stringify({
                                        job_kind: kindBox.currentValue,
                                        family: familyField.text,
                                        from: fromField.text,
                                        to: toField.text,
                                        request_limits: {max_pages:pageBudget.value,max_requests:requestBudget.value,max_seconds:timeBudget.value,pacing_seconds:pacingBudget.value}
                                    }))
                                    page4.reload()
                                }
                            }
                        }
                        GridLayout {
                            columns: 4; Layout.fillWidth: true
                            Label { text: "Giới hạn số trang"; color:"#a9b4bf" }
                            SpinBox { id: pageBudget; from:1; to:10000; value:1000; editable:true; Accessible.name:"Số trang tối đa" }
                            Label { text: "Giới hạn yêu cầu"; color:"#a9b4bf" }
                            SpinBox { id: requestBudget; from:1; to:30000; value:3000; editable:true; Accessible.name:"Số yêu cầu tối đa" }
                            Label { text: "Thời gian (s)"; color:"#a9b4bf" }
                            SpinBox { id: timeBudget; from:1; to:86400; value:3600; editable:true; Accessible.name:"Thời gian tối đa tính bằng giây" }
                            Label { text: "Giãn cách yêu cầu (giây)"; color:"#a9b4bf" }
                            SpinBox { id: pacingBudget; from:3; to:300; value:3; editable:true; Accessible.name:"Khoảng cách giữa hai yêu cầu, tính bằng giây" }
                        }
                        Label {
                            Layout.fillWidth:true; Layout.preferredHeight:68; wrapMode:Text.WrapAnywhere
                            maximumLineCount:4; elide:Text.ElideRight; color:"#9dbdae"
                            text: {
                                var status = page4.state.active_status || {}; var runs = status.runs || []
                                var latest = runs.length ? runs[runs.length-1] : {}; var p = status.progress || {}
                                return (status.job_id || "Chưa có tác vụ") + " · " + T.label(latest.lifecycle) + " · " + T.label(latest.status)
                                    + (p.family ? "\n" + T.family(p.family) + " · " + p.from + " → " + p.to + " · " + T.label(p.granularity) + "\nSố trang: " + p.pages + " · số dòng: " + p.rows + " · số yêu cầu: " + p.requests : "")
                            }
                        }
                        Text { Layout.fillWidth: true; wrapMode: Text.WrapAnywhere; text: page4.enqueueResult; color: "#d1a47f"; font.pixelSize: 11 }

                        ComboBox { id:jobTarget; Layout.fillWidth:true; model:(page4.state.active || []).map(j=>j.job_id); Accessible.name:"Chọn tác vụ để điều khiển" }
                        RowLayout {
                            spacing: 8
                            Button {
                                text: "Bật bộ thu thập"
                                onClicked: {
                                    backend.requestRead("jobAction", "start_worker", "{}")
                                    page4.reload()
                                }
                            }
                            Button {
                                text: "Tạm dừng"
                                onClicked: {
                                    var active = page4.state.active || []
                                    if (active.length) backend.requestRead("jobAction", "worker_command", JSON.stringify({command:"PAUSE",job_id:jobTarget.currentText || active[0].job_id}))
                                }
                            }
                            Button {
                                text: "Tiếp tục"
                                onClicked: {
                                    var active = page4.state.active || []
                                    if (active.length) backend.requestRead("jobAction", "worker_command", JSON.stringify({command:"RESUME",job_id:jobTarget.currentText || active[0].job_id}))
                                }
                            }
                            Button {
                                text: "Dừng an toàn"
                                onClicked: {
                                    var active = page4.state.active || []
                                    if (active.length) backend.requestRead("jobAction", "worker_command", JSON.stringify({command:"STOP_GRACEFUL",job_id:jobTarget.currentText || active[0].job_id}))
                                }
                            }
                            Button {
                                text: "Tắt bộ thu thập"
                                onClicked: backend.requestRead("jobAction", "worker_command", JSON.stringify({command:"FORCE_TERMINATE"}))
                            }
                            Button { text: "Tải lại"; onClicked: page4.reload() }
                        }

                        Text {
                            text: "Hàng đợi: " + (page4.state.queued || []).length
                                  + " · đang chạy: " + (page4.state.active || []).length
                                  + " · xong: " + (page4.state.done || []).length
                            color: "#9aa4ae"
                            font.pixelSize: 12
                        }
                        Repeater {
                            model: (page4.state.queued || []).concat(page4.state.active || [])
                                          .concat(page4.state.done || [])
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                height: 30
                                color: "#1b2128"
                                radius: 4
                                Row {
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.left: parent.left
                                    anchors.leftMargin: 10
                                    spacing: 20
                                    Text { text: T.label(modelData.job_kind); color: "#e6e9ec"; font.pixelSize: 12; width: 110 }
                                    Text { text: modelData.job_id; color: "#c8d0d8"; font.pixelSize: 12; width: 190 }
                                    Text { text: (modelData.families || []).map(T.familyShort).join(", "); color: "#9aa4ae"; font.pixelSize: 12; width: 120 }
                                    Text {
                                        text: modelData.scope ? (modelData.scope.from + " → " + modelData.scope.to) : ""
                                        color: "#8b959f"
                                        font.pixelSize: 11
                                    }
                                }
                            }
                        }
                        Item { Layout.preferredHeight: 8 }
                        }
                    }
                }

                // ------------------------------------------------ UI-05 Coverage Matrix
                Item {
                    id: page5
                    property var state: ({ intervals: [], summary: [], watermarks: [] })
                    onVisibleChanged: if (visible) reload()
                    function reload() { backend.requestRead("page5", "coverage", "{}") }

                    ScrollView {
                        anchors.fill: parent; anchors.margins: 18; clip: true
                        contentWidth: availableWidth
                        ColumnLayout {
                        width: parent.width
                        spacing: 8

                        Label { text:"Mức độ thu thập theo ngày Việt Nam — 7 ngày gần nhất"; color:"#a9b4bf" }
                        GridLayout {
                            columns:7; Layout.fillWidth:true; rowSpacing:4; columnSpacing:4
                            Repeater {
                                model:page5.state.heatmap || []
                                delegate:Button {
                                    required property var modelData
                                    Layout.fillWidth:true; Layout.preferredHeight:64; Layout.minimumWidth:70
                                    text:T.familyShort(modelData.family) + "\n" + modelData.day.slice(5) + "\n" + T.label(modelData.status)
                                    font.pixelSize:10
                                    background:Rectangle { radius:3; color:parent.modelData.status === "COMPLETE" ? "#205039" : (parent.modelData.status === "BLOCKED" ? "#5a3030" : "#30383e") }
                                    onClicked:{ evidenceText.text=T.pretty(modelData); evidenceDialog.open() }
                                }
                            }
                        }
                        Repeater {
                            model: page5.state.summary || []
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                height: 34
                                color: modelData.status === "COMPLETE" ? "#193d30" : (modelData.status === "BLOCKED" ? "#472c2c" : "#302c23")
                                radius: 4
                                Row {
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.left: parent.left
                                    anchors.leftMargin: 10
                                    spacing: 22
                                    Text { text: T.familyShort(modelData.family); color: "#e6e9ec"; font.pixelSize: 13; width: 80 }
                                    Text { text: "Đã ghi nhận: " + modelData.claimed; color: "#c8d0d8"; font.pixelSize: 12; width: 110 }
                                    Text { text: "Có bằng chứng: " + modelData.walked; color: "#7fd1a4"; font.pixelSize: 12; width: 110 }
                                    Text { text: "Chưa đủ: " + modelData.partial; color: "#d1a47f"; font.pixelSize: 12; width: 100 }
                                    Text { text: "Bị chặn: " + modelData.blocked; color: "#e07a7a"; font.pixelSize: 12; width: 100 }
                                    Text { text: "Lỗi: " + modelData.failed; color: "#c8d0d8"; font.pixelSize: 12 }
                                }
                            }
                        }
                        Text {
                            visible: !(page5.state.summary || []).length
                            text: "Chưa có khoảng thời gian nào được thu thập."
                            color: "#8b959f"
                            font.pixelSize: 12
                        }

                        Text { text: "Các khoảng thời gian gần nhất"; color: "#9aa4ae"; font.pixelSize: 12; topPadding: 6 }
                        Repeater {
                            model: (page5.state.intervals || []).slice(0, 60)
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                height: 36
                                Button { anchors.fill:parent; flat:true; Accessible.name:T.family(modelData.family) + " " + T.label(modelData.status) + " — bằng chứng"; onClicked: {
                                    if (modelData.proof_id) backend.requestRead("proof","coverage_proof",JSON.stringify({proof_id:modelData.proof_id}))
                                    else { evidenceText.text=T.pretty(modelData); evidenceDialog.open() }
                                } }
                                color: modelData.status === "COMPLETE" ? "#193d30" : (modelData.status === "BLOCKED" ? "#472c2c" : "#302c23")
                                radius: 4
                                Row {
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.left: parent.left
                                    anchors.leftMargin: 10
                                    spacing: 16
                                    Text { text: T.familyShort(modelData.family); color: "#e6e9ec"; font.pixelSize: 11; width: 70 }
                                    Text {
                                        text: modelData.interval_from_utc + " → " + modelData.interval_to_utc
                                        color: "#c8d0d8"; font.pixelSize: 11; width: 340
                                    }
                                    Text { text: T.label(modelData.granularity); color: "#8b959f"; font.pixelSize: 11; width: 80 }
                                    Text {
                                        text: T.label(modelData.status)
                                        color: modelData.status === "COMPLETE" ? "#7fd1a4"
                                             : (modelData.status === "BLOCKED" ? "#e07a7a" : "#d1a47f")
                                        font.pixelSize: 11; width: 90
                                    }
                                    Text {
                                        text: modelData.proof_id ? ("Bằng chứng: " + modelData.distinct_ids + " hồ sơ riêng biệt") : T.message(modelData.reason)
                                        color: modelData.proof_id ? "#7fd1a4" : "#d1a47f"
                                        font.pixelSize: 11
                                    }
                                }
                            }
                        }
                        Button { text:"Mức đầy đủ do kho cũ ghi nhận (chưa xác minh)"; onClicked: { evidenceText.text=T.pretty(page5.state.legacy || []); evidenceDialog.open() } }
                        Item { Layout.preferredHeight: 8 }
                        }
                    }
                }

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
                            color: "#8b959f"
                            font.pixelSize: 11
                            wrapMode: Text.WrapAnywhere
                        }
                        Text {
                            text: "Đầy đủ: " + ((page8.state.summary || {}).FULL || 0)
                                  + " · Chưa đầy đủ: " + ((page8.state.summary || {}).PARTIAL || 0)
                                  + " · Chưa thu thập: " + ((page8.state.summary || {}).NOT_COLLECTED || 0)
                                  + " · Cần cập nhật: " + ((page8.state.summary || {}).stale || 0)
                            color: "#9aa4ae"
                            font.pixelSize: 12
                        }
                        Repeater {
                            model: page8.state.rows || []
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                height: 30
                                color: "#1b2128"
                                radius: 4
                                Row {
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.left: parent.left
                                    anchors.leftMargin: 10
                                    spacing: 18
                                    Text { text: T.familyShort(modelData.family); color: "#e6e9ec"; font.pixelSize: 11; width: 70 }
                                    Text { text: modelData.natural_key; color: "#c8d0d8"; font.pixelSize: 11; width: 200 }
                                    Text { text: T.label(modelData.lane); color: "#9aa4ae"; font.pixelSize: 11; width: 170 }
                                    Text {
                                        text: T.label(modelData.completeness)
                                        color: modelData.completeness === "FULL" ? "#7fd1a4" : "#d1a47f"
                                        font.pixelSize: 11; width: 110
                                    }
                                    Text {
                                        text: modelData.stale ? ("Cần cập nhật: " + T.message(modelData.stale_reason))
                                             : (modelData.needs_refresh ? "cần cập nhật" : "đã cập nhật")
                                        color: modelData.stale || modelData.needs_refresh ? "#d1a47f" : "#7fd1a4"
                                        font.pixelSize: 11; width: 170
                                    }
                                    Text { text: "Thu thập lúc " + modelData.captured_at_utc; color: "#8b959f"; font.pixelSize: 11 }
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
                            Button { text:"Kiểm tra toàn vẹn"; enabled:!root.operation.running; onClicked:root.startAction("verify", "") }
                            Button { text:"Chi tiết lỗi và kho cũ"; onClicked:{ evidenceText.text=T.pretty({inspection:page9.state.inspection,quarantine:page9.state.quarantine,scans:page9.state.markers}); evidenceDialog.open() } }
                        }
                        RowLayout {
                            TextField { id:integrityHash; Layout.fillWidth:true; placeholderText:"Mã SHA256 của dữ liệu gốc cần kiểm tra"; Accessible.name:placeholderText }
                            Button { text:"Xem dữ liệu gốc"; onClicked:backend.requestRead("integrityRaw","raw_object",JSON.stringify({sha:integrityHash.text})) }
                        }
                        Label { Layout.fillWidth:true; wrapMode:Text.Wrap; color:"#a9b4bf"; text:"Chi tiết hiển thị tối đa 50 lỗi và 20 lần nhập kho gần nhất; tổng đếm bên dưới là toàn kho." }
                        Text {
                            text: "Dữ liệu gốc: " + T.pretty(page9.state.raw_objects || {})
                                  + "   · Chưa xác định: " + ((page9.state.unresolved || {}).open || 0)
                                  + " / Đã xác định: " + ((page9.state.unresolved || {}).resolved || 0)
                            color: "#c8d0d8"
                            font.pixelSize: 12
                        }
                        Text {
                            text: "Dữ liệu được cách ly để kiểm tra: " + (page9.state.quarantine_total || 0)
                            color: (page9.state.quarantine || []).length ? "#e07a7a" : "#7fd1a4"
                            font.pixelSize: 12
                        }
                        Repeater {
                            model: page9.state.quarantine || []
                            delegate: Text {
                                required property var modelData
                                text: "  · " + T.message(modelData.failure) + " · " + modelData.sha12 + "… · " + modelData.quarantined_at_utc
                                color: "#d1a47f"
                                font.pixelSize: 11
                            }
                        }
                        Text { text: "Khoảng dữ liệu chênh lệch chưa được xác nhận"; color: "#6f7a84"; font.pixelSize: 11; topPadding: 4 }
                        Repeater {
                            model: page9.state.gaps || []
                            delegate: Text {
                                required property var modelData
                                text: "  · " + T.label(modelData.kind) + " × " + modelData.count
                                color: "#d1a47f"
                                font.pixelSize: 11
                            }
                        }
                        Text {
                            visible: (page9.state.markers || []).length > 0
                            text: "Ghi nhận kiểm tra: " + T.pretty(page9.state.markers || [])
                            color: "#e0b46a"
                            font.pixelSize: 11
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
                            Button { text:"Khôi phục dữ liệu"; DialogButtonBox.buttonRole:DialogButtonBox.AcceptRole }
                            Button { text:"Hủy"; DialogButtonBox.buttonRole:DialogButtonBox.RejectRole }
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
                            color: "#c8d0d8"
                            font.pixelSize: 12
                        }
                        RowLayout {
                            Button { text: "Tạo bản sao lưu"; enabled: !root.operation.running; onClicked: root.startAction("backup", "") }
                            Button { text: "Kiểm tra dữ liệu"; enabled: !root.operation.running; onClicked: root.startAction("verify", "") }
                            Button { text: "Xuất CSV"; enabled: !root.operation.running; onClicked: root.startAction("export", "") }
                        }
                        RowLayout {
                            TextField {
                                id: restorePath
                                Layout.fillWidth: true
                                placeholderText: "Đường dẫn chứa bản sao lưu cần khôi phục"
                            }
                            Button { text: "Khôi phục…"; enabled: !root.operation.running && restorePath.text.trim().length > 0; onClicked: restoreConfirm.open() }
                        }
                        RowLayout {
                            Button { text: "Bật lịch 08:00 / 20:00"; enabled: !root.operation.running; onClicked: root.startAction("schedule-enable", "") }
                            Button { text: "Tắt lịch"; enabled: !root.operation.running; onClicked: root.startAction("schedule-disable", "") }
                            Button { text: "Xem lịch"; enabled: !root.operation.running; onClicked: root.startAction("schedule-status", "") }
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "Lịch tự động chỉ cập nhật các loại hồ sơ có quyền truy cập và quy tắc thu thập đã được xác minh."
                            color: "#8b959f"
                            font.pixelSize: 11
                            wrapMode: Text.WordWrap
                        }
                        Text {
                            Layout.fillWidth: true
                            text: root.operationMessage
                            color: root.operation.ok === false ? "#e07a7a" : "#7fd1a4"
                            font.pixelSize: 12
                            wrapMode: Text.WrapAnywhere
                        }
                        Text {
                            text: {
                                var snap = page10.state.snapshot || {}
                                return snap.present
                                    ? ("Dữ liệu hiển thị cập nhật lúc " + (snap.built_at || "?") + (snap.stale ? " · Cần cập nhật" : " · Đã cập nhật"))
                                    : "Chưa có dữ liệu hiển thị"
                            }
                            color: "#9aa4ae"
                            font.pixelSize: 12
                        }

                        RowLayout {
                            TextField { id: legacyPath; Layout.fillWidth: true; placeholderText: "Thư mục kho cũ hoặc gói dữ liệu đã xuất"; Accessible.name: placeholderText }
                            Button { text: "Kiểm tra kho cũ"; enabled: !root.operation.running && legacyPath.text.length > 0; onClicked: root.startAction("inspect-legacy", legacyPath.text) }
                            Button { text: "Nhập dữ liệu"; enabled: !root.operation.running && legacyPath.text.length > 0; onClicked: root.startAction("import-legacy", legacyPath.text) }
                        }
                        Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#d1a47f"; text: "Dữ liệu nhập từ kho cũ giữ nguyên lịch sử và nguồn gốc. Thông tin từng được kho cũ ghi nhận là đầy đủ vẫn cần bằng chứng mới để xác minh." }
                        RowLayout {
                            Button { text:"Quy định truy cập và lưu trữ"; onClicked: { evidenceText.text=T.pretty({host_policy:page10.state.host_policy,snapshot:page10.state.snapshot,data_root:page10.state.data_root}); evidenceDialog.open() } }
                            Button { text:"Nhật ký hoạt động"; onClicked: { evidenceText.text=T.pretty(page10.state.logs || []); evidenceDialog.open() } }
                            Button { text:"Phiên bản và giấy phép"; onClicked: { evidenceText.text=page10.state.app_version + "\n" + T.message(page10.state.licensing); evidenceDialog.open() } }
                        }
                        Button { visible:root.operation.running && root.operation.action === "import-legacy"; text:"Dừng nhập sau mục đang xử lý"; onClicked:backend.cancelMaintenance() }
                        Text { text: "Cấu hình"; color: "#6f7a84"; font.pixelSize: 11; topPadding: 4 }
                        Repeater {
                            model: page10.state.settings || []
                            delegate: Text {
                                required property var modelData
                                text: "  · " + (T.fields[modelData.key] || T.label(modelData.key)) + ": " + T.pretty(root.parse(modelData.value_json))
                                color: "#c8d0d8"
                                font.pixelSize: 11
                                wrapMode: Text.WrapAnywhere
                            }
                        }

                        Text { text: "Bản sao lưu"; color: "#6f7a84"; font.pixelSize: 11; topPadding: 6 }
                        Repeater {
                            model: page10.state.backups || []
                            delegate: Text {
                                required property var modelData
                                text: "  · " + modelData.name + " · " + Math.round(modelData.bytes / 1024) + " KB · " + modelData.modified_at
                                color: "#c8d0d8"
                                font.pixelSize: 11
                            }
                        }
                        Text {
                            visible: !(page10.state.backups || []).length
                            text: "  (chưa có bản sao lưu nào trong thư mục dữ liệu)"
                            color: "#8b959f"
                            font.pixelSize: 11
                        }
                        Item { Layout.preferredHeight: 8 }
                        }
                    }
                }
            }
        }
    }
}
