.pragma library

function describe(state, fresh) {
    var worker = state.worker || {};
    var runtime = worker.runtime || {};
    var counts = state.counts || {};
    var progress = runtime.progress || (state.active_status || {}).progress || {};
    var phase = runtime.phase;
    if (!fresh || state.error) return {title:"Chưa nhận được trạng thái mới", detail:"Không thể xác nhận lúc này có đang thu thập hay không. Đang kiểm tra lại kết nối.", tone:"warning", busy:false};
    if (!worker.running) return {title:"Đã tắt bộ thu thập", detail:"Ứng dụng vẫn đang mở. Không có dữ liệu mới được thu thập; bật bộ thu thập để xử lý hàng đợi.", tone:"neutral", busy:false};
    if (!worker.pipe_ok) return {title:"Chưa kết nối được bộ thu thập", detail:"Tiến trình còn tồn tại nhưng chưa trả lời. Chưa xác nhận được đang lấy dữ liệu hay đang chuẩn bị.", tone:"warning", busy:false};
    if (!phase) {
        var runs = (state.active_status || {}).runs || [];
        if (runs.length && runs[runs.length-1].lifecycle === "RUNNING") phase = "COLLECTING";
    }
    var states = {
        STARTING:["Đang khởi động bộ thu thập","Chưa bắt đầu lấy dữ liệu. Hàng đợi được giữ nguyên.","busy"],
        RECOVERING:["Đang kiểm tra kho dữ liệu","Đang kiểm tra dữ liệu đã lưu trước khi nhận tác vụ. Chưa lấy dữ liệu mới.","busy"],
        PUBLISHING_SNAPSHOT:["Đang cập nhật dữ liệu hiển thị","Đang dựng và kiểm tra bản dữ liệu cho giao diện. Tạm thời chưa lấy trang mới; hàng đợi sẽ tiếp tục sau bước này.","busy"],
        COLLECTING:["Đang thu thập dữ liệu","Bộ thu thập đang xử lý tác vụ. Số dòng và số trang bên dưới là tiến độ của khoảng đang lấy.","busy"],
        PAUSING:["Đang tạm dừng","Đã nhận yêu cầu. Chờ xử lý và lưu xong bước hiện tại; chưa tạm dừng hoàn toàn.","warning"],
        STOPPING_JOB:["Đang dừng tác vụ","Đã nhận yêu cầu. Chờ lưu xong bước hiện tại. Bộ thu thập có thể tiếp tục tác vụ khác trong hàng đợi.","warning"],
        STOPPING_WORKER:["Đang tắt bộ thu thập","Chờ lưu xong bước hiện tại. Khi tắt xong, hàng đợi sẽ ngừng xử lý; ứng dụng vẫn mở.","warning"],
        FAILED:["Bộ thu thập gặp lỗi","Chưa thể tiếp tục. Xem thông báo lỗi và kiểm tra lại bộ thu thập.","error"],
        STOPPED:["Đã tắt bộ thu thập","Không thu thập dữ liệu mới.","neutral"]
    };
    if (phase === "IDLE") {
        if (counts.queued > 0) states.IDLE = ["Đang chờ nhận tác vụ","Bộ thu thập đã sẵn sàng; chưa có trang dữ liệu mới.","busy"];
        else if (counts.paused > 0) states.IDLE = ["Đã tạm dừng thu thập","Không lấy dữ liệu mới. Chọn tác vụ và bấm Tiếp tục khi cần.","warning"];
        else states.IDLE = ["Sẵn sàng · chưa có tác vụ chạy","Bộ thu thập đang bật nhưng không lấy dữ liệu. Có thể tạo tác vụ mới.","neutral"];
    }
    var chosen = states[phase] || ["Bộ thu thập đang bật · chưa rõ bước xử lý","Bản worker này chưa cung cấp trạng thái chi tiết. Cần cập nhật worker để phân biệt chuẩn bị và thu thập.","warning"];
    return {title:chosen[0], detail:chosen[1], tone:chosen[2], busy:chosen[2] === "busy",
            phase:phase, progress:phase === "COLLECTING" ? progress : {}};
}

function jobLabel(job) {
    if (job.pending_command === "PAUSE") return "Đang tạm dừng";
    if (job.pending_command === "RESUME") return "Đang tiếp tục";
    if (job.pending_command === "STOP_GRACEFUL") return "Đang dừng";
    var labels = {UNREADABLE:"Không đọc được tác vụ", QUEUED:"Đang chờ", RUNNING:"Đang chạy", PAUSED:"Đã tạm dừng",
        WORKER_STOPPED:"Bộ thu thập đã tắt", STATUS_UNKNOWN:"Chưa xác nhận trạng thái",
        COMPLETED:"Hoàn tất", PARTIAL:"Kết thúc · chưa đầy đủ", FAILED:"Thất bại", BLOCKED:"Bị chặn",
        INTERRUPTED:"Bị gián đoạn", TERMINATED:"Đã kết thúc"};
    return labels[job.display_state] || "Chưa xác nhận trạng thái";
}
