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
    property var navItems: [
        "01 · Onboarding", "02 · Control Center", "03 · Sources", "04 · Collection Jobs",
        "05 · Coverage Matrix", "06 · Data Explorer", "07 · Record Detail",
        "08 · Deep Collection", "09 · Data Integrity", "10 · Settings / Backup"
    ]

    function parse(text) {
        try { return JSON.parse(text) }
        catch (error) { return { error: "bad json: " + error } }
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
                    function reload() { state = root.parse(backend.onboarding()) }

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 18
                        spacing: 10

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
                        Item { Layout.fillHeight: true }
                    }
                }

                // ------------------------------------------------ UI-02 Control Center
                Item {
                    id: page2
                    property var state: ({ families: [] })
                    Component.onCompleted: reload()
                    onVisibleChanged: if (visible) reload()
                    function reload() { state = root.parse(backend.controlCenter()) }

                    Timer {
                        interval: 3000
                        running: page2.visible
                        repeat: true
                        onTriggered: page2.reload()
                    }

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 18
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
                        Item { Layout.fillHeight: true }
                    }
                }

                // ------------------------------------------------ UI-03 Sources
                Item {
                    id: page3
                    property var state: ({ registry: [], boundary_rules: [] })
                    Component.onCompleted: reload()
                    onVisibleChanged: if (visible) reload()
                    function reload() { state = root.parse(backend.sources()) }

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 18
                        spacing: 8

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
                        Item { Layout.fillHeight: true }
                    }
                }

                // ------------------------------------------------ UI-04 Collection Jobs
                Item {
                    id: page4
                    property var state: ({ queued: [], active: [], done: [] })
                    property string enqueueResult: ""
                    Component.onCompleted: reload()
                    onVisibleChanged: if (visible) reload()
                    function reload() { state = root.parse(backend.jobs()) }

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 18
                        spacing: 8

                        Text {
                            text: "Worker: " + (page4.state.worker && page4.state.worker.running
                                  ? "chạy · pipe " + (page4.state.worker.pipe_ok ? "OK" : "—")
                                  : "không chạy — job sẽ chờ trong hàng đợi")
                            color: page4.state.worker && page4.state.worker.running ? "#7fd1a4" : "#d1a47f"
                            font.pixelSize: 12
                        }

                        RowLayout {
                            spacing: 8
                            ComboBox {
                                id: kindBox
                                model: ["BACKFILL", "INCREMENTAL", "RECONCILE"]
                                Layout.preferredWidth: 150
                            }
                            TextField {
                                id: familyField
                                placeholderText: "family (vd TBMT)"
                                text: "TBMT"
                                Layout.preferredWidth: 120
                                color: "#e6e9ec"
                                background: Rectangle { color: "#20262d"; border.color: "#333c45"; radius: 4 }
                            }
                            TextField {
                                id: fromField
                                placeholderText: "from (ISO Z)"
                                Layout.preferredWidth: 220
                                color: "#e6e9ec"
                                background: Rectangle { color: "#20262d"; border.color: "#333c45"; radius: 4 }
                            }
                            TextField {
                                id: toField
                                placeholderText: "to (ISO Z)"
                                Layout.preferredWidth: 220
                                color: "#e6e9ec"
                                background: Rectangle { color: "#20262d"; border.color: "#333c45"; radius: 4 }
                            }
                            Button {
                                text: "Xếp hàng job"
                                onClicked: {
                                    page4.enqueueResult = backend.enqueueCollection(JSON.stringify({
                                        job_kind: kindBox.currentText,
                                        family: familyField.text,
                                        from: fromField.text,
                                        to: toField.text
                                    }))
                                    page4.reload()
                                }
                            }
                        }
                        Text { text: page4.enqueueResult; color: "#d1a47f"; font.pixelSize: 11 }

                        RowLayout {
                            spacing: 8
                            Button {
                                text: "Khởi động worker"
                                onClicked: {
                                    page4.enqueueResult = backend.startWorker()
                                    page4.reload()
                                }
                            }
                            Button {
                                text: "PAUSE"
                                onClicked: {
                                    var active = page4.state.active || []
                                    if (active.length) page4.enqueueResult = backend.workerCommand("PAUSE", active[0].job_id)
                                }
                            }
                            Button {
                                text: "RESUME"
                                onClicked: {
                                    var active = page4.state.active || []
                                    if (active.length) page4.enqueueResult = backend.workerCommand("RESUME", active[0].job_id)
                                }
                            }
                            Button {
                                text: "STOP_GRACEFUL"
                                onClicked: {
                                    var active = page4.state.active || []
                                    if (active.length) page4.enqueueResult = backend.workerCommand("STOP_GRACEFUL", active[0].job_id)
                                }
                            }
                            Button {
                                text: "FORCE_TERMINATE"
                                onClicked: page4.enqueueResult = backend.workerCommand("FORCE_TERMINATE", "")
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
                        Item { Layout.fillHeight: true }
                    }
                }

                // ------------------------------------------------ UI-05 Coverage Matrix
                Item {
                    id: page5
                    property var state: ({ intervals: [], summary: [], watermarks: [] })
                    onVisibleChanged: if (visible) reload()
                    function reload() { state = root.parse(backend.coverage("")) }

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 18
                        spacing: 8

                        Repeater {
                            model: page5.state.summary || []
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
                                height: 28
                                color: "#1b2128"
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
                        Item { Layout.fillHeight: true }
                    }
                }

                // ------------------------------------------------ UI-06 Data Explorer
                Item {
                    id: page6
                    property var state: ({ hits: [] })
                    function run() {
                        state = root.parse(backend.search(JSON.stringify({
                            text: queryField.text, family: familyBox.text, limit: 50, offset: 0
                        })))
                    }
                    function loadMore() {
                        var next = root.parse(backend.search(JSON.stringify({
                            text: queryField.text, family: familyBox.text, limit: 50,
                            offset: (state.hits || []).length
                        })))
                        state = { hits: (state.hits || []).concat(next.hits || []), note: next.note }
                    }

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 18
                        spacing: 8

                        RowLayout {
                            spacing: 8
                            TextField {
                                id: queryField
                                Layout.fillWidth: true
                                placeholderText: "mã / tên / CĐT / MST / tỉnh / loại …"
                                color: "#e6e9ec"
                                background: Rectangle { color: "#20262d"; border.color: "#333c45"; radius: 4 }
                                onAccepted: page6.run()
                            }
                            TextField {
                                id: familyBox
                                placeholderText: "family"
                                Layout.preferredWidth: 110
                                color: "#e6e9ec"
                                background: Rectangle { color: "#20262d"; border.color: "#333c45"; radius: 4 }
                            }
                            Button { text: "Tìm"; onClicked: page6.run() }
                            Button { text: "Tải thêm"; onClicked: page6.loadMore() }
                        }

                        Text { text: page6.state.note || ""; color: "#8b959f"; font.pixelSize: 11 }
                        Text {
                            visible: !!page6.state.error
                            text: page6.state.error || ""
                            color: "#e07a7a"
                            font.pixelSize: 11
                        }
                        Repeater {
                            model: page6.state.hits || []
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                height: 44
                                color: "#1b2128"
                                radius: 4
                                Column {
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.left: parent.left
                                    anchors.leftMargin: 10
                                    spacing: 2
                                    Text {
                                        text: (modelData.title_display || "(không tiêu đề)") + "  ·  " + modelData.natural_key
                                        color: "#e6e9ec"
                                        font.pixelSize: 12
                                    }
                                    Text {
                                        text: modelData.family + " · " + (modelData.investor_display || "—")
                                              + " · version " + modelData.version_state
                                              + (modelData.version_raw ? (" (" + modelData.version_raw + ")") : "")
                                        color: "#9aa4ae"
                                        font.pixelSize: 11
                                    }
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        root.detailRecordId = modelData.record_id
                                        root.pageIndex = 6
                                    }
                                }
                            }
                        }
                        Item { Layout.fillHeight: true }
                    }
                }

                // ------------------------------------------------ UI-07 Record Detail
                Item {
                    id: page7
                    property var state: ({ present: false })
                    onVisibleChanged: if (visible) reload()
                    function reload() {
                        state = root.detailRecordId > 0 ? root.parse(backend.recordDetail(root.detailRecordId))
                                                        : ({ present: false })
                    }

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 18
                        spacing: 6

                        Text {
                            text: page7.state.present ? ("" + page7.state.record.family + " · " + page7.state.record.natural_key) : "Chọn một hồ sơ ở Data Explorer"
                            color: "#e6e9ec"
                            font.pixelSize: 15
                            font.bold: true
                        }
                        Text {
                            text: page7.state.present
                                ? ("version " + page7.state.record.version_state
                                   + (page7.state.record.version_raw ? (" (" + page7.state.record.version_raw + ")") : "")
                                   + " · integrity " + page7.state.record.integrity_status
                                   + " · observations " + page7.state.observations)
                                : ""
                            color: "#9aa4ae"
                            font.pixelSize: 12
                        }

                        Text { text: "Timeline content version"; color: "#6f7a84"; font.pixelSize: 11 }
                        Repeater {
                            model: page7.state.versions || []
                            delegate: Text {
                                required property var modelData
                                text: "v" + modelData.content_version_seq + " · " + modelData.change_kind
                                      + " · " + modelData.content_hash.slice(0, 18) + "… · " + modelData.fetched_at_utc
                                color: "#c8d0d8"
                                font.pixelSize: 11
                            }
                        }
                        Text { text: "Raw revision (bytes thay đổi không đổi nghĩa cũng hiện ở đây)"; color: "#6f7a84"; font.pixelSize: 11; topPadding: 4 }
                        Repeater {
                            model: page7.state.raw_revisions || []
                            delegate: Text {
                                required property var modelData
                                text: "#" + modelData.revision_no + " · " + modelData.reason
                                      + " · " + modelData.record_object_sha.slice(0, 18) + "… · " + modelData.first_observed_at_utc
                                color: "#c8d0d8"
                                font.pixelSize: 11
                            }
                        }
                        Text {
                            text: "Deep capture · lots · goods · documents"
                            color: "#6f7a84"
                            font.pixelSize: 11
                            topPadding: 4
                        }
                        Repeater {
                            model: page7.state.deep_captures || []
                            delegate: Text {
                                required property var modelData
                                text: modelData.lane + "/" + modelData.endpoint_kind + " · " + modelData.completeness
                                      + (modelData.stale ? " · STALE " + modelData.stale_reason : " · fresh")
                                      + " · captured " + modelData.captured_at_utc
                                color: modelData.stale ? "#d1a47f" : "#7fd1a4"
                                font.pixelSize: 11
                            }
                        }
                        Repeater {
                            model: page7.state.lots || []
                            delegate: Text {
                                required property var modelData
                                text: "Lô " + modelData.lot_no_raw + " · " + (modelData.lot_label || "—")
                                      + " · goods " + (modelData.goods_count === null ? "?" : modelData.goods_count)
                                color: "#c8d0d8"
                                font.pixelSize: 11
                            }
                        }
                        Repeater {
                            model: page7.state.goods_rows || []
                            delegate: Text {
                                required property var modelData
                                text: "  · " + modelData.name + " · " + (modelData.unit || "—")
                                      + " · SL " + modelData.quantity_presence
                                      + (modelData.quantity_presence === "KNOWN" ? (" = " + modelData.quantity)
                                         : (modelData.quantity_raw ? (" (" + modelData.quantity_raw + ")") : ""))
                                color: "#9aa4ae"
                                font.pixelSize: 11
                            }
                        }
                        Repeater {
                            model: page7.state.documents || []
                            delegate: Text {
                                required property var modelData
                                text: "  · " + modelData.document_kind + " · " + modelData.name
                                color: "#9aa4ae"
                                font.pixelSize: 11
                            }
                        }
                        Repeater {
                            model: page7.state.contractor_memberships || []
                            delegate: Text {
                                required property var modelData
                                text: "  · " + modelData.role + " · " + modelData.party_name
                                      + " · MST " + modelData.party_tax_id_presence
                                      + " · share " + modelData.membership_share_presence
                                color: "#9aa4ae"
                                font.pixelSize: 11
                            }
                        }
                        Item { Layout.fillHeight: true }
                    }
                }

                // ------------------------------------------------ UI-08 Deep Collection
                Item {
                    id: page8
                    property var state: ({ rows: [], summary: {} })
                    onVisibleChanged: if (visible) reload()
                    function reload() { state = root.parse(backend.deepQueue()) }

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 18
                        spacing: 6

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
                        Item { Layout.fillHeight: true }
                    }
                }

                // ------------------------------------------------ UI-09 Data Integrity
                Item {
                    id: page9
                    property var state: ({ raw_objects: {}, quarantine: [], unresolved: {}, gaps: [], markers: [] })
                    onVisibleChanged: if (visible) reload()
                    function reload() { state = root.parse(backend.integrity()) }

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 18
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
                        Item { Layout.fillHeight: true }
                    }
                }

                // ------------------------------------------------ UI-10 Settings / Backup
                Item {
                    id: page10
                    property var state: ({ settings: [], backups: [], snapshot: {} })
                    onVisibleChanged: if (visible) reload()
                    function reload() { state = root.parse(backend.settings()) }

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 18
                        spacing: 8

                        Text {
                            text: "Data root: " + (page10.state.data_root || "")
                                  + " · app " + (page10.state.app_version || "")
                                  + " · pipe " + (page10.state.worker_pipe || "")
                            color: "#c8d0d8"
                            font.pixelSize: 12
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
                        Item { Layout.fillHeight: true }
                    }
                }
            }
        }
    }
}