import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic
import QtQuick.Layouts

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
        operationMessage = result.ok ? "Đang thực hiện…" : (result.error || "Không khởi chạy được")
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
            if (root.operation.progress) root.operationMessage = "Import · " + JSON.stringify(root.operation.progress)
            if (!root.operation.running) {
                root.operationMessage = root.operation.ok
                    ? "Hoàn tất · " + (root.operation.path || JSON.stringify(root.operation))
                    : "Không hoàn tất · " + (root.operation.error || JSON.stringify(root.operation))
                page10.reload()
                if (root.pageIndex === 6) page7.reload()
            }
        }
    }
    property var navItems: [
        "01 · Onboarding", "02 · Control Center", "03 · Sources", "04 · Collection Jobs",
        "05 · Coverage Matrix", "06 · Data Explorer", "07 · Record Detail",
        "08 · Deep Collection", "09 · Data Integrity", "10 · Settings / Backup"
    ]

    function parse(text) {
        try { return JSON.parse(text) }
        catch (error) { return { error: "bad json: " + error } }
    }

    Connections {
        target: backend
        function onReadReady(key, serial, text) {
            var pages = {page1:page1,page2:page2,page3:page3,page4:page4,page5:page5,page8:page8,page9:page9,page10:page10}
            if (pages[key]) pages[key].state = root.parse(text)
            if (key === "proof") { evidenceText.text = JSON.stringify(root.parse(text), null, 2); evidenceDialog.open() }
            if (key === "jobAction") { page4.enqueueResult = text; page4.reload() }
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
        anchors.centerIn:parent; modal:true; standardButtons:Dialog.Close
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
                    text: "Data Studio"
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
                            Button { text: "Tạo kho / khởi tạo collector"; onClicked: { root.pageIndex = 3; backend.requestRead("jobAction", "start_worker", "{}") } }
                            Button { text: "Import kho harvester cũ"; onClicked: root.pageIndex = 9 }
                        }


                        Text { text: "Data root"; color: "#9aa4ae"; font.pixelSize: 12 }
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
                                        ? "OK · free " + Math.round(result.free_bytes / 1048576) + " MB"
                                        : "KHÔNG DÙNG ĐƯỢC · " + (result.error || "")
                                }
                            }
                            Button {
                                text: "Dùng thư mục này"
                                onClicked: {
                                    var result = root.parse(backend.selectDataRoot(rootPath.text))
                                    validation.text = result.ok ? "Đã chọn · " + result.path : result.error
                                }
                            }
                        }
                        Text { id: validation; color: "#7fd1a4"; font.pixelSize: 12 }

                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: "#2a333d" }

                        Text {
                            text: "Warehouse: " + (page1.state.warehouse_present ? "có" : "chưa")
                                  + "   ·   Serving snapshot: " + (page1.state.serving_present ? "có" : "chưa")
                                  + "   ·   Jobs chờ: " + (page1.state.queued_jobs || 0)
                            color: "#c8d0d8"
                            font.pixelSize: 13
                        }
                        Text {
                            text: "Worker: " + (page1.state.worker && page1.state.worker.running
                                  ? ("chạy · pid " + page1.state.worker.pid) : "không chạy")
                            color: page1.state.worker && page1.state.worker.running ? "#7fd1a4" : "#d1a47f"
                            font.pixelSize: 13
                        }
                        Text { text: page1.state.policy_note || ""; color: "#8b959f"; font.pixelSize: 11 }
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
                                if (!snap.present) return "Chưa có serving snapshot"
                                return "Snapshot " + (snap.built_at || "?")
                                     + (snap.stale ? ("  ·  STALE" + (snap.degraded ? " / DEGRADED" : "")) : "  ·  tươi")
                                     + (snap.degraded ? ("  ·  thiếu " + JSON.stringify(snap.degradation)) : "")
                            }
                            color: (page2.state.snapshot && page2.state.snapshot.stale) ? "#e0b46a" : "#7fd1a4"
                            font.pixelSize: 12
                        }
                        Text {
                            text: {
                                var worker = page2.state.worker || {}
                                var disk = page2.state.disk || {}
                                return "Worker: " + (worker.running ? "chạy" : "dừng")
                                     + " · pipe " + (worker.pipe_ok ? "OK" : "—")
                                     + " · đĩa " + Math.round((disk.bytes || 0) / 1048576) + " MB"
                                     + (disk.sampled ? " (mẫu " + disk.limit + " file)" : "")
                            }
                            color: "#9aa4ae"
                            font.pixelSize: 12
                        }

                        Text { text: "family"; color: "#6f7a84"; font.pixelSize: 11 }
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
                                    Text { text: modelData.family; color: "#e6e9ec"; font.pixelSize: 13; width: 90 }
                                    Text { text: modelData.records + " records"; color: "#c8d0d8"; font.pixelSize: 12; width: 120 }
                                    Text { text: modelData.versions + " versions"; color: "#c8d0d8"; font.pixelSize: 12; width: 120 }
                                    Text { text: modelData.raw_revisions + " raw rev"; color: "#c8d0d8"; font.pixelSize: 12; width: 120 }
                                    Text { text: modelData.observations + " obs"; color: "#c8d0d8"; font.pixelSize: 12; width: 100 }
                                    Text {
                                        text: modelData.last_verified_at_utc ? ("verified " + modelData.last_verified_at_utc) : "chưa có proof"
                                        color: modelData.last_verified_at_utc ? "#7fd1a4" : "#d1a47f"
                                        font.pixelSize: 12
                                    }
                                }
                            }
                        }
                        Text {
                            visible: !(page2.state.families || []).length
                            text: "Chưa có dữ liệu trong serving snapshot."
                            color: "#8b959f"
                            font.pixelSize: 12
                        }
                        Text {
                            visible: !!page2.state.error
                            text: page2.state.error || ""
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
                                text:modelData.family + " · " + modelData.state + " · " + (modelData.blocked_reason ? "BLOCKED · " + modelData.blocked_reason : "rule có bằng chứng")
                            }
                        }
                        Button { text:"Lịch sử canary / access"; onClicked: { evidenceText.text=JSON.stringify(page3.state.access_events || [],null,2); evidenceDialog.open() } }
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
                                    Text { text: modelData.source_key; color: "#e6e9ec"; font.pixelSize: 13; width: 170 }
                                    Text { text: modelData.display_name; color: "#c8d0d8"; font.pixelSize: 12; width: 260 }
                                    Text {
                                        text: modelData.access_state
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
                            text: "Hợp đồng biên filter (canary-verified = điều kiện để chạy)"
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
                                    Text { text: modelData.family; color: "#e6e9ec"; font.pixelSize: 12; width: 90 }
                                    Text { text: modelData.boundary_kind + " · " + modelData.precision; color: "#c8d0d8"; font.pixelSize: 12; width: 200 }
                                    Text { text: "v" + modelData.rule_version; color: "#8b959f"; font.pixelSize: 12; width: 50 }
                                    Text {
                                        text: modelData.canary_verified ? "canary OK" : "CHƯA VERIFY"
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
                            text: "Worker: " + (page4.state.worker && page4.state.worker.running
                                  ? "chạy · pipe " + (page4.state.worker.pipe_ok ? "OK" : "—")
                                  : "không chạy — job sẽ chờ trong hàng đợi")
                            color: page4.state.worker && page4.state.worker.running ? "#7fd1a4" : "#d1a47f"
                            font.pixelSize: 12
                        }

                        GridLayout {
                            columns: width < 1000 ? 2 : 3
                            columnSpacing: 8
                            rowSpacing: 8
                            ComboBox {
                                id: kindBox
                                model: ["BACKFILL", "INCREMENTAL", "RECONCILE"]
                                Layout.fillWidth: true
                            }
                            TextField {
                                id: familyField
                                placeholderText: "family (vd TBMT)"
                                text: "TBMT"
                                Layout.fillWidth: true
                                color: "#e6e9ec"
                                background: Rectangle { color: "#20262d"; border.color: "#333c45"; radius: 4 }
                            }
                            TextField {
                                id: fromField
                                placeholderText: "from (ISO Z)"
                                Layout.fillWidth: true
                                color: "#e6e9ec"
                                background: Rectangle { color: "#20262d"; border.color: "#333c45"; radius: 4 }
                            }
                            TextField {
                                id: toField
                                placeholderText: "to (ISO Z)"
                                Layout.fillWidth: true
                                color: "#e6e9ec"
                                background: Rectangle { color: "#20262d"; border.color: "#333c45"; radius: 4 }
                            }
                            Button {
                                text: "Xếp hàng job"
                                onClicked: {
                                    backend.requestRead("jobAction", "enqueue_collection", JSON.stringify({
                                        job_kind: kindBox.currentText,
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
                            Label { text: "Pages / job"; color:"#a9b4bf" }
                            SpinBox { id: pageBudget; from:1; to:10000; value:1000; editable:true; Accessible.name:"Ngân sách trang" }
                            Label { text: "Requests / job"; color:"#a9b4bf" }
                            SpinBox { id: requestBudget; from:1; to:30000; value:3000; editable:true; Accessible.name:"Ngân sách request" }
                            Label { text: "Thời gian (s)"; color:"#a9b4bf" }
                            SpinBox { id: timeBudget; from:1; to:86400; value:3600; editable:true; Accessible.name:"Ngân sách giây" }
                            Label { text: "Pacing (s)"; color:"#a9b4bf" }
                            SpinBox { id: pacingBudget; from:3; to:300; value:3; editable:true; Accessible.name:"Pacing giây" }
                        }
                        Label { Layout.fillWidth:true; wrapMode:Text.WrapAnywhere; color:"#9dbdae"; text: JSON.stringify(page4.state.active_status || {}) }
                        Text { Layout.fillWidth: true; wrapMode: Text.WrapAnywhere; text: page4.enqueueResult; color: "#d1a47f"; font.pixelSize: 11 }

                        RowLayout {
                            spacing: 8
                            Button {
                                text: "Khởi động worker"
                                onClicked: {
                                    backend.requestRead("jobAction", "start_worker", "{}")
                                    page4.reload()
                                }
                            }
                            Button {
                                text: "PAUSE"
                                onClicked: {
                                    var active = page4.state.active || []
                                    if (active.length) backend.requestRead("jobAction", "worker_command", JSON.stringify({command:"PAUSE",job_id:active[0].job_id}))
                                }
                            }
                            Button {
                                text: "RESUME"
                                onClicked: {
                                    var active = page4.state.active || []
                                    if (active.length) backend.requestRead("jobAction", "worker_command", JSON.stringify({command:"RESUME",job_id:active[0].job_id}))
                                }
                            }
                            Button {
                                text: "STOP_GRACEFUL"
                                onClicked: {
                                    var active = page4.state.active || []
                                    if (active.length) backend.requestRead("jobAction", "worker_command", JSON.stringify({command:"STOP_GRACEFUL",job_id:active[0].job_id}))
                                }
                            }
                            Button {
                                text: "FORCE_TERMINATE"
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
                                    Text { text: modelData.job_kind; color: "#e6e9ec"; font.pixelSize: 12; width: 110 }
                                    Text { text: modelData.job_id; color: "#c8d0d8"; font.pixelSize: 12; width: 190 }
                                    Text { text: (modelData.families || []).join(","); color: "#9aa4ae"; font.pixelSize: 12; width: 120 }
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
                                    Text { text: modelData.family; color: "#e6e9ec"; font.pixelSize: 13; width: 80 }
                                    Text { text: "claimed " + modelData.claimed; color: "#c8d0d8"; font.pixelSize: 12; width: 110 }
                                    Text { text: "walked " + modelData.walked; color: "#7fd1a4"; font.pixelSize: 12; width: 110 }
                                    Text { text: "partial " + modelData.partial; color: "#d1a47f"; font.pixelSize: 12; width: 100 }
                                    Text { text: "blocked " + modelData.blocked; color: "#e07a7a"; font.pixelSize: 12; width: 100 }
                                    Text { text: "fail " + modelData.failed; color: "#c8d0d8"; font.pixelSize: 12 }
                                }
                            }
                        }
                        Text {
                            visible: !(page5.state.summary || []).length
                            text: "Chưa có coverage interval nào."
                            color: "#8b959f"
                            font.pixelSize: 12
                        }

                        Text { text: "Interval gần nhất"; color: "#9aa4ae"; font.pixelSize: 12; topPadding: 6 }
                        Repeater {
                            model: (page5.state.intervals || []).slice(0, 60)
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                height: 36
                                Button { anchors.fill:parent; flat:true; Accessible.name:modelData.family + " " + modelData.status + " proof"; onClicked: {
                                    if (modelData.proof_id) backend.requestRead("proof","coverage_proof",JSON.stringify({proof_id:modelData.proof_id}))
                                    else { evidenceText.text=JSON.stringify(modelData,null,2); evidenceDialog.open() }
                                } }
                                color: modelData.status === "COMPLETE" ? "#193d30" : (modelData.status === "BLOCKED" ? "#472c2c" : "#302c23")
                                radius: 4
                                Row {
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.left: parent.left
                                    anchors.leftMargin: 10
                                    spacing: 16
                                    Text { text: modelData.family; color: "#e6e9ec"; font.pixelSize: 11; width: 70 }
                                    Text {
                                        text: modelData.interval_from_utc + " → " + modelData.interval_to_utc
                                        color: "#c8d0d8"; font.pixelSize: 11; width: 340
                                    }
                                    Text { text: modelData.granularity; color: "#8b959f"; font.pixelSize: 11; width: 80 }
                                    Text {
                                        text: modelData.status
                                        color: modelData.status === "COMPLETE" ? "#7fd1a4"
                                             : (modelData.status === "BLOCKED" ? "#e07a7a" : "#d1a47f")
                                        font.pixelSize: 11; width: 90
                                    }
                                    Text {
                                        text: modelData.proof_id ? ("proof · " + modelData.distinct_ids + " ids") : (modelData.reason || "")
                                        color: modelData.proof_id ? "#7fd1a4" : "#d1a47f"
                                        font.pixelSize: 11
                                    }
                                }
                            }
                        }
                        Button { text:"Coverage legacy (chưa xác minh)"; onClicked: { evidenceText.text=JSON.stringify(page5.state.legacy || [],null,2); evidenceDialog.open() } }
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
                                    ? "Lane: " + lanes.map(function (l) { return l.lane + "=" + l.state }).join(" · ")
                                    : ""
                            }
                            color: "#8b959f"
                            font.pixelSize: 11
                            wrapMode: Text.WrapAnywhere
                        }
                        Text {
                            text: "FULL " + ((page8.state.summary || {}).FULL || 0)
                                  + " · PARTIAL " + ((page8.state.summary || {}).PARTIAL || 0)
                                  + " · NOT_COLLECTED " + ((page8.state.summary || {}).NOT_COLLECTED || 0)
                                  + " · stale " + ((page8.state.summary || {}).stale || 0)
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
                                    Text { text: modelData.family; color: "#e6e9ec"; font.pixelSize: 11; width: 70 }
                                    Text { text: modelData.natural_key; color: "#c8d0d8"; font.pixelSize: 11; width: 200 }
                                    Text { text: modelData.lane + "/" + modelData.endpoint_kind; color: "#9aa4ae"; font.pixelSize: 11; width: 170 }
                                    Text {
                                        text: modelData.completeness
                                        color: modelData.completeness === "FULL" ? "#7fd1a4" : "#d1a47f"
                                        font.pixelSize: 11; width: 110
                                    }
                                    Text {
                                        text: modelData.stale ? ("STALE " + modelData.stale_reason)
                                             : (modelData.needs_refresh ? "cần refresh" : "fresh")
                                        color: modelData.stale || modelData.needs_refresh ? "#d1a47f" : "#7fd1a4"
                                        font.pixelSize: 11; width: 170
                                    }
                                    Text { text: "captured " + modelData.captured_at_utc; color: "#8b959f"; font.pixelSize: 11 }
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

                        Text {
                            text: "Raw objects: " + JSON.stringify(page9.state.raw_objects || {})
                                  + "   · unresolved open: " + ((page9.state.unresolved || {}).open || 0)
                                  + " / resolved: " + ((page9.state.unresolved || {}).resolved || 0)
                            color: "#c8d0d8"
                            font.pixelSize: 12
                        }
                        Text {
                            text: "Quarantine (không bao giờ ẩn khỏi UI): " + (page9.state.quarantine_total || 0)
                            color: (page9.state.quarantine || []).length ? "#e07a7a" : "#7fd1a4"
                            font.pixelSize: 12
                        }
                        Repeater {
                            model: page9.state.quarantine || []
                            delegate: Text {
                                required property var modelData
                                text: "  · " + modelData.failure + " · " + modelData.sha12 + "… · " + modelData.quarantined_at_utc
                                color: "#d1a47f"
                                font.pixelSize: 11
                            }
                        }
                        Text { text: "Reconcile gaps (chưa acknowledge)"; color: "#6f7a84"; font.pixelSize: 11; topPadding: 4 }
                        Repeater {
                            model: page9.state.gaps || []
                            delegate: Text {
                                required property var modelData
                                text: "  · " + modelData.kind + " × " + modelData.count
                                color: "#d1a47f"
                                font.pixelSize: 11
                            }
                        }
                        Text {
                            visible: (page9.state.markers || []).length > 0
                            text: "Marker: " + JSON.stringify(page9.state.markers || [])
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
                        standardButtons: Dialog.Ok | Dialog.Cancel
                        Text {
                            text: "Dừng worker trước khi khôi phục. Warehouse hiện tại được giữ làm bản dự phòng.\nChỉ tiếp tục khi đúng thư mục backup và Data Root đang hiển thị."
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
                            text: "Data root: " + (page10.state.data_root || "")
                                  + " · app " + (page10.state.app_version || "")
                                  + " · pipe " + (page10.state.worker_pipe || "")
                            color: "#c8d0d8"
                            font.pixelSize: 12
                        }
                        RowLayout {
                            Button { text: "Tạo backup"; enabled: !root.operation.running; onClicked: root.startAction("backup", "") }
                            Button { text: "Kiểm tra dữ liệu"; enabled: !root.operation.running; onClicked: root.startAction("verify", "") }
                            Button { text: "Xuất CSV"; enabled: !root.operation.running; onClicked: root.startAction("export", "") }
                        }
                        RowLayout {
                            TextField {
                                id: restorePath
                                Layout.fillWidth: true
                                placeholderText: "Đường dẫn thư mục backup cần khôi phục"
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
                            text: "Lịch chỉ thu tăng dần các family đã có quyền truy cập và quy tắc canary được xác minh."
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
                                    ? ("Snapshot " + (snap.built_at || "?") + (snap.stale ? " · STALE" : " · tươi"))
                                    : "Chưa có snapshot"
                            }
                            color: "#9aa4ae"
                            font.pixelSize: 12
                        }

                        RowLayout {
                            TextField { id: legacyPath; Layout.fillWidth: true; placeholderText: "Thư mục harvester cũ / export bundle"; Accessible.name: placeholderText }
                            Button { text: "Kiểm tra kho cũ"; enabled: !root.operation.running && legacyPath.text.length > 0; onClicked: root.startAction("inspect-legacy", legacyPath.text) }
                            Button { text: "Import"; enabled: !root.operation.running && legacyPath.text.length > 0; onClicked: root.startAction("import-legacy", legacyPath.text) }
                        }
                        Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#d1a47f"; text: "Legacy giữ lịch sử và nguồn gốc. COMPLETE cũ chưa có proof mới luôn là LEGACY_UNVERIFIED." }
                        RowLayout {
                            Button { text:"Host policy / storage"; onClicked: { evidenceText.text=JSON.stringify({host_policy:page10.state.host_policy,snapshot:page10.state.snapshot,data_root:page10.state.data_root},null,2); evidenceDialog.open() } }
                            Button { text:"Logs"; onClicked: { evidenceText.text=JSON.stringify(page10.state.logs || [],null,2); evidenceDialog.open() } }
                            Button { text:"Version / license"; onClicked: { evidenceText.text=page10.state.app_version + "\n" + page10.state.licensing; evidenceDialog.open() } }
                        }
                        Button { visible:root.operation.running && root.operation.action === "import-legacy"; text:"Dừng import sau event hiện tại"; onClicked:backend.cancelMaintenance() }
                        Text { text: "Settings"; color: "#6f7a84"; font.pixelSize: 11; topPadding: 4 }
                        Repeater {
                            model: page10.state.settings || []
                            delegate: Text {
                                required property var modelData
                                text: "  · " + modelData.key + " = " + modelData.value_json.slice(0, 140)
                                color: "#c8d0d8"
                                font.pixelSize: 11
                                wrapMode: Text.WrapAnywhere
                            }
                        }

                        Text { text: "Backup"; color: "#6f7a84"; font.pixelSize: 11; topPadding: 6 }
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
                            text: "  (chưa có bản backup nào trong data root)"
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
