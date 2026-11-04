.pragma library

// Presentation only. Stored values, command names and original source bytes stay unchanged.
var words = {
    TESTED_OFFLINE:"Đã kiểm tra bằng dữ liệu giả lập",
    ALL:"Tất cả 6 loại hồ sơ",
    BACKFILL:"Thu thập theo khoảng ngày", INCREMENTAL:"Cập nhật tự động đến hiện tại", RECONCILE:"Đối chiếu lại dữ liệu",
    INDEX:"Lập chỉ mục", DETAIL:"Chi tiết hồ sơ", HSMT:"Hồ sơ mời thầu", REOFFER:"Lịch sử chào lại",
    KQLCNT_FULL:"Kết quả đầy đủ", BOQ:"Bảng khối lượng", OTHER:"Khác", VERIFY:"Kiểm tra", IMPORT:"Nhập dữ liệu",
    EXPORT:"Xuất dữ liệu", BACKUP:"Sao lưu", PAUSE:"Tạm dừng", RESUME:"Tiếp tục",
    STOP_GRACEFUL:"Dừng an toàn", FORCE_TERMINATE:"Tắt bộ thu thập",
    PENDING:"Đang chờ", QUEUED:"Đã xếp hàng", RUNNING:"Đang chạy", COLLECTING:"Đang thu thập",
    PAUSING:"Đang tạm dừng", PAUSED:"Đã tạm dừng", RESUMING:"Đang tiếp tục", STOPPING:"Đang dừng",
    FINISHED:"Đã kết thúc", TERMINATED:"Đã dừng", INTERRUPTED:"Bị gián đoạn", RECOVERABLE:"Có thể tiếp tục",
    COMPLETE:"Đủ bằng chứng", COMPLETED:"Hoàn tất", PARTIAL:"Chưa đầy đủ", FAILED:"Thất bại",
    BLOCKED:"Đang bị chặn", NOT_TESTED:"Chưa kiểm thử", PASS:"Đạt", FAIL:"Không đạt",
    FULL:"Đầy đủ", NOT_COLLECTED:"Chưa thu thập", NOT_ACCESSED:"Chưa thu thập", UNSUPPORTED:"Chưa hỗ trợ", FRESH:"Đã cập nhật", STALE:"Cần cập nhật", DEGRADED:"Dữ liệu bị thiếu hoặc lỗi",
    KNOWN:"Đã có dữ liệu", UNKNOWN:"Chưa xác định", MISSING:"Chưa có dữ liệu", UNPARSABLE:"Không đọc được giá trị",
    OK:"Bình thường", SUSPECT:"Cần kiểm tra", CORRUPT:"Dữ liệu bị lỗi", NEW:"Mới", UPDATED:"Đã đổi nội dung",
    UPDATED_RAW:"Đã đổi dữ liệu gốc", UNCHANGED:"Không thay đổi", DUPLICATE_IN_RUN:"Trùng trong lần thu thập",
    UNRESOLVED:"Chưa xác định được hồ sơ", NEW_RECORD:"Hồ sơ mới", SEMANTIC_CHANGE:"Thay đổi nội dung",
    RAW_ONLY_CHANGE:"Chỉ thay đổi dữ liệu gốc", REPLAY:"Xử lý lại theo quy tắc bảo mật",
    ALLOWED_AND_VERIFIED:"Được phép truy cập", NEEDS_USER_SESSION:"Cần đăng nhập hợp lệ", UNVERIFIED:"Chưa xác minh", DISABLED:"Đã tắt",
    PROBE_UNVERIFIED:"Chưa xác minh cách thu thập", RULE_VERIFIED:"Đã xác minh quy tắc lọc",
    COLLECTION_PROVEN:"Đã có bằng chứng thu thập", LEGACY_UNVERIFIED:"Dữ liệu cũ chưa được xác minh",
    LEGACY_COMPLETE_UNVERIFIED:"Kho cũ báo đủ, chưa có bằng chứng mới", LEGACY_PARTIAL:"Kho cũ chưa đầy đủ",
    IMPORTED:"Đã nhập", ALREADY_IMPORTED:"Đã nhập trước đó, không thêm trùng", VALIDATED:"Đã kiểm tra", REJECTED:"Chưa được chấp nhận", ROLLED_BACK:"Đã hoàn tác",
    DAY:"Ngày", HOUR:"Giờ", MINUTE:"Phút", SECOND:"Giây", MILLISECOND:"Mili giây",
    CLOSED_LOCAL:"Gồm cả hai mốc, theo giờ nguồn", CLOSED_UTC:"Gồm cả hai mốc, theo giờ quốc tế",
    HALF_OPEN_UTC:"Gồm mốc đầu, không gồm mốc cuối", OPEN_UTC:"Không gồm hai mốc",
    EMPTY_PAGE:"Trang rỗng kết thúc kết quả", SHORT_PAGE:"Trang cuối ít kết quả hơn giới hạn",
    NO_PROOF:"Chưa có bằng chứng đầy đủ", TOTAL_UNTRUSTED:"Chưa xác minh tổng số kết quả", NO_TOTAL_OBSERVED:"Nguồn chưa cung cấp tổng số kết quả",
    INTERVAL_NOT_CLOSED:"Khoảng thời gian chưa kết thúc; dữ liệu đã lấy vẫn được lưu nhưng chưa thể xác nhận đầy đủ.",
    NO_TERMINAL_EVIDENCE:"Chưa xác nhận đã đọc hết các trang", COMPLETE_WITHOUT_PROOF:"Trạng thái đầy đủ thiếu bằng chứng, cần kiểm tra",
    STOP_REQUESTED:"Đã nhận yêu cầu dừng", SPLIT_INCOMPLETE:"Một số khoảng thời gian chưa thu thập đủ",
    BOUNDARY_RULE_UNVERIFIED:"Loại hồ sơ này chưa được xác minh nguồn. Cần chủ hệ thống phê duyệt kiểm tra nguồn trước khi thu thập.",
    LIVE_DEEP_ENDPOINT_UNVERIFIED:"Chưa xác minh nguồn dữ liệu chi tiết. Cần chủ hệ thống phê duyệt trước khi thu thập thật.",
    DEEP_SPEC_UNVERIFIED:"Chưa xác minh cấu trúc dữ liệu chi tiết", SOURCE_VERSION_CHANGED:"Nguồn đã có phiên bản mới",
    NEEDS_RECONCILE:"Cần đối chiếu lại", COUNT_DRIFT:"Số lượng đã thay đổi", MISSING_IN_RESCAN:"Không thấy khi đối chiếu lại",
    DATA_REVISED_AFTER_PROOF:"Dữ liệu đã thay đổi sau lần xác minh", TTL_EXPIRED:"Đã đến hạn cập nhật",
    HTTP_401:"Nguồn yêu cầu đăng nhập", HTTP_403:"Nguồn từ chối truy cập", CAPTCHA:"Nguồn yêu cầu xác minh người dùng",
    WAF:"Nguồn đang chặn truy cập tự động", RATE_LIMIT_429:"Nguồn yêu cầu giảm tốc độ truy cập",
    TIMEOUT:"Hết thời gian chờ phản hồi", TLS_FAILURE:"Không thiết lập được kết nối bảo mật",
    REDIRECT_OFF_ALLOWLIST:"Nguồn chuyển đến địa chỉ chưa được phép", USER_DISABLED:"Người dùng đã tắt nguồn",
    CANARY_FAILED:"Lần kiểm tra nguồn chưa đạt", POLICY:"Bị chặn bởi quy định truy cập", NO_TARGETS:"Không có hồ sơ cần xử lý",
    JOB_TARGET_NOT_RUNNING:"Tác vụ được chọn không còn chạy. Hãy chọn lại tác vụ đang chạy.",
    JOB_TARGET_NOT_PAUSED:"Tác vụ được chọn chưa ở trạng thái tạm dừng.",
    READ_FAILED:"Không đọc được dữ liệu. Hãy thử tải lại hoặc kiểm tra dữ liệu.",
    EMPTY_QUERY:"Nhập nội dung hoặc chọn bộ lọc để tìm hồ sơ.", NO_MATCH:"Không có hồ sơ phù hợp.",
    NO_SNAPSHOT:"Chưa có dữ liệu hiển thị. Hãy khởi tạo bộ thu thập hoặc nhập kho cũ.",
    CURSOR_EXPIRED:"Dữ liệu đã thay đổi. Hãy tìm lại từ trang đầu.", INVALID_CURSOR:"Trang kết quả không khớp bộ lọc. Hãy tìm lại từ đầu.",
    INVALID_HASH:"Mã kiểm tra không hợp lệ. Cần đủ 64 ký tự của mã SHA256.", NOT_FOUND:"Không tìm thấy dữ liệu.",
    PREVIEW_TOO_LARGE:"Dữ liệu quá lớn để xem trước. Hãy dùng bản xuất để đọc toàn bộ.",
    MERGE:"Gộp liên kết hồ sơ", SPLIT:"Tách liên kết hồ sơ", CONFIRM_LINK:"Xác nhận liên kết", REJECT_LINK:"Bỏ liên kết", UNDO:"Hoàn tác",
    EXACT:"Khớp định danh", LINKED:"Đã liên kết", AMBIGUOUS:"Chưa rõ liên kết", CONFLICT:"Có mâu thuẫn",
    MANUAL:"Xác nhận thủ công", ALIAS:"Theo mã liên quan", GRAPH:"Theo quan hệ hồ sơ", EXACT_NATURAL_KEY:"Trùng mã gốc",
    SAME_AS:"Cùng hồ sơ", PLAN_HAS_NOTICE:"Kế hoạch có thông báo", NOTICE_HAS_RESULT:"Thông báo có kết quả",
    RESULT_HAS_CONTRACT:"Kết quả có hợp đồng", NOTICE_HAS_HSMT:"Thông báo có hồ sơ mời thầu", EXECUTED_BY:"Đơn vị thực hiện", OFFERED_BY:"Đơn vị chào giá",
    PUBLICATION_VERSIONS:"Các lần công bố", SAME_NOTICE_OTHER_CHANNEL:"Cùng thông báo ở nguồn khác",
    RC3_SYNTHETIC_OFFLINE:"Dữ liệu thử nghiệm ngoại tuyến", SYNTHETIC_OFFLINE:"Dữ liệu thử nghiệm ngoại tuyến", "SYNTHETIC OFFLINE":"Dữ liệu thử nghiệm ngoại tuyến",
    SYNTHETIC:"Dữ liệu thử nghiệm", NONE:"Không có", REFUSED:"Không được phép", PUBLIC:"Công khai", SESSION:"Cần đăng nhập",
    warehouse:"Kho dữ liệu", bundle:"Gói dữ liệu xuất", backup:"Sao lưu", restore:"Khôi phục", verify:"Kiểm tra dữ liệu", export:"Xuất dữ liệu",
    "import-legacy":"Nhập kho cũ", "inspect-legacy":"Kiểm tra kho cũ", identity:"Cập nhật liên kết", "schedule-enable":"Bật lịch thu thập",
    "schedule-disable":"Tắt lịch thu thập", "schedule-status":"Xem lịch thu thập", true:"Có", false:"Không",
    egp_home:"Hệ thống đấu thầu quốc gia", egp_v2:"Nguồn đấu thầu cần đăng nhập", moh_quotes:"Báo giá ngành y tế", mof_quotes:"Báo giá ngành tài chính"
};
var families = {TBMT:"Mời thầu",KHLCNT:"Kế hoạch lựa chọn nhà thầu",KQLCNT:"Kết quả lựa chọn nhà thầu",DU_AN:"Dự án",YCBG:"Yêu cầu báo giá",HOP_DONG:"Hợp đồng"};
var fields = {
    ok:"Thành công",error:"Thông báo lỗi",error_code:"Loại lỗi",reason:"Lý do",reasons:"Các lý do",note:"Ghi chú",status:"Kết quả",state:"Trạng thái",
    record:"Hồ sơ",records:"Số hồ sơ",record_id:"Mã hồ sơ nội bộ",source_record_id:"Mã hồ sơ nguồn",source_key:"Nguồn dữ liệu",family:"Loại hồ sơ",
    natural_key:"Mã hồ sơ gốc",version_state:"Trạng thái phiên bản",version_raw:"Phiên bản do nguồn cung cấp",identity_key:"Mã định danh",
    first_seen_at_utc:"Lần đầu ghi nhận",last_seen_at_utc:"Lần gần nhất ghi nhận",current_content_version:"Phiên bản nội dung hiện tại",
    current_content_hash:"Mã kiểm tra nội dung hiện tại",integrity_status:"Tình trạng dữ liệu",versions:"Các phiên bản nội dung",raw_revisions:"Lịch sử dữ liệu gốc",
    content_version_seq:"Số thứ tự phiên bản",content_hash:"Mã kiểm tra nội dung",change_kind:"Loại thay đổi",fetched_at_utc:"Thời điểm thu thập",
    normalized_json:"Nội dung đã chuẩn hóa",normalize_schema:"Phiên bản quy tắc chuẩn hóa",revision_no:"Lần đổi dữ liệu gốc",
    record_object_sha:"Mã kiểm tra dữ liệu hồ sơ",response_object_sha:"Mã kiểm tra phản hồi nguồn",response_sha:"Mã kiểm tra phản hồi",
    introducing_record_sha:"Dữ liệu gốc tạo phiên bản",introducing_response_sha:"Phản hồi tạo phiên bản",first_observed_at_utc:"Lần đầu ghi nhận thay đổi",
    observations:"Số lần ghi nhận",observed_at_utc:"Thời điểm ghi nhận",run_id:"Mã lần chạy",job_id:"Mã tác vụ",job_kind:"Loại tác vụ",lifecycle:"Trạng thái chạy",
    started_at_utc:"Bắt đầu lúc",finished_at_utc:"Kết thúc lúc",runs:"Các lần chạy",tasks:"Các bước xử lý",task_kind:"Loại bước xử lý",detail:"Chi tiết",
    progress:"Tiến độ",pages:"Số trang",rows:"Số dòng",requests:"Số yêu cầu",request_count:"Số yêu cầu",attempt_count:"Số lượt thử",
    attempt_error_count:"Số lượt gặp lỗi",unrecovered_request_count:"Số yêu cầu chưa xử lý được",malformed_count:"Số mục không đọc được",
    total_hint:"Tổng số do nguồn báo",total_first:"Tổng số lúc bắt đầu",total_last:"Tổng số lúc kết thúc",total_trusted:"Tổng số đã xác minh",
    distinct_ids:"Số hồ sơ riêng biệt",ids_union_hash:"Mã kiểm tra tập hồ sơ",proof:"Bằng chứng",proof_id:"Mã bằng chứng",proof_ids:"Các mã bằng chứng",
    proof_version:"Phiên bản bằng chứng",boundary_rule:"Quy tắc lọc thời gian",boundary_rule_id:"Mã quy tắc lọc",boundary_kind:"Cách xử lý mốc thời gian",
    rule_key:"Mã quy tắc",rule_version:"Phiên bản quy tắc",precision:"Độ chính xác thời gian",granularity:"Mức chia thời gian",proof_granularity:"Mức chia trong bằng chứng",
    source_timezone:"Múi giờ nguồn",offset_minutes:"Độ lệch múi giờ theo phút",conversion_params:"Quy tắc chuyển đổi",canary_verified:"Đã kiểm tra quy tắc với nguồn",
    verified_at_utc:"Thời điểm xác minh",verified_by_run_id:"Lần chạy xác minh",adapter_sha:"Mã kiểm tra bộ đọc nguồn",source_query_fingerprint:"Mã kiểm tra truy vấn nguồn",
    endpoint:"Địa chỉ nguồn",endpoint_kind:"Loại dữ liệu chi tiết",terminal_evidence_json:"Bằng chứng đã đọc hết trang",page_size:"Số dòng tối đa mỗi trang",
    page_no:"Số trang",max_pages_hit:"Số trang khi chạm giới hạn",max_records_hit:"Số hồ sơ khi chạm giới hạn",kind:"Loại",from:"Từ thời điểm",to:"Đến thời điểm",
    interval_id:"Mã khoảng thời gian",interval_from_utc:"Mốc bắt đầu",interval_to_utc:"Mốc kết thúc",intervals:"Số khoảng thời gian",blockers:"Lý do chưa đủ",
    day:"Ngày",claim:"Thông tin kho cũ đã ghi nhận",classification:"Phân loại",modern_proof:"Có bằng chứng mới",batch:"Mã lần nhập",batch_key:"Mã lần nhập",
    seen:"Số mục đã đọc",imported:"Số mục đã nhập",duplicates:"Số mục trùng đã bỏ qua",rejected:"Số mục cần kiểm tra",errors:"Các lỗi",unchanged:"Không thêm dữ liệu",
    rows_seen:"Số mục đã đọc",rows_imported:"Số mục đã nhập",rows_rejected:"Số mục chưa nhập được",receipt:"Đường dẫn biên nhận",catalog:"Đường dẫn danh mục gốc",
    source:"Nguồn nhập",source_path:"Đường dẫn nguồn",legacy_proof_promoted:"Đã coi thông tin cũ là bằng chứng mới",snapshot_published:"Đã cập nhật dữ liệu hiển thị",
    legacy_imports:"Các lần nhập kho cũ",legacy_rejections:"Dữ liệu kho cũ cần kiểm tra",metadata:"Thông tin nguồn gốc",raw_failures:"Dữ liệu gốc bị lỗi",
    deep_captures:"Các lần thu thập chi tiết",deep_history:"Lịch sử thu thập chi tiết",lane:"Nội dung thu thập",completeness:"Mức đầy đủ",stale:"Cần cập nhật",
    stale_reason:"Lý do cần cập nhật",captured_at_utc:"Thời điểm thu thập chi tiết",refresh_due_at_utc:"Hạn cập nhật",latest_capture_seq:"Lần thu thập gần nhất",
    capture_seq:"Số thứ tự lần thu thập",source_version_json:"Phiên bản phía nguồn",captured_by_run_id:"Lần chạy thu thập",deep_capture_id:"Mã bản thu thập chi tiết",
    lots:"Các lô",lot_no_raw:"Mã lô từ nguồn",lot_label:"Tên lô",lot_version:"Phiên bản lô",goods_rows:"Danh mục hàng hóa và khối lượng",goods_count:"Số hàng hóa",
    row_ordinal:"Số thứ tự dòng",name:"Tên",title:"Tiêu đề",specification:"Yêu cầu kỹ thuật",unit:"Đơn vị tính",quantity:"Số lượng",quantity_presence:"Trạng thái số lượng",quantity_raw:"Số lượng do nguồn cung cấp",
    documents:"Tài liệu",document_kind:"Loại tài liệu",media_type:"Định dạng",byte_len:"Dung lượng theo byte",contractor_memberships:"Các nhà thầu",party_name:"Tên đơn vị",
    role:"Vai trò",party_tax_id_presence:"Trạng thái mã số thuế",party_tax_id:"Mã số thuế",membership_share_presence:"Trạng thái tỷ lệ tham gia",membership_share:"Tỷ lệ tham gia",ranking:"Thứ hạng",
    notice_groups:"Nhóm thông báo",group_id:"Mã nhóm",group_kind:"Loại nhóm",publication_label:"Lần công bố",aliases:"Các mã liên quan",alias_kind:"Loại mã liên quan",alias_value:"Mã liên quan",
    entity_links:"Liên kết hồ sơ",entity_id:"Mã nhóm hồ sơ",relation:"Quan hệ",method:"Cách xác định",evidence_json:"Bằng chứng kèm theo",history_counts:"Số mục lịch sử",
    identity_audit:"Nhật ký liên kết",action:"Thao tác",action_id:"Mã nhật ký",actor_sid:"Mã tài khoản Windows thực hiện",undo_action_id:"Thao tác được hoàn tác",acted_at_utc:"Thời điểm thao tác",target_ref:"Mã đối tượng",target_kind:"Loại đối tượng",
    inspection:"Thông tin kiểm tra",quarantine:"Dữ liệu được cách ly",quarantined_at_utc:"Thời điểm cách ly",failure:"Lỗi",unresolved:"Hồ sơ chưa xác định",gaps:"Khoảng dữ liệu cần đối chiếu",scans:"Các lần kiểm tra",count:"Số lượng",
    sha256:"Mã kiểm tra SHA256",verified:"Đã kiểm tra mã dữ liệu",truncated:"Bản xem trước bị rút gọn",text:"Nội dung",path:"Đường dẫn",file:"Tệp",files:"Số tệp",bytes:"Dung lượng theo byte",
    format:"Định dạng",schema_version:"Phiên bản cấu trúc dữ liệu",counts:"Số lượng",backup:"Bản sao lưu",restored:"Đã khôi phục",objects:"Số đối tượng dữ liệu gốc",objects_verified:"Số đối tượng đã kiểm tra",
    integrity:"Kiểm tra toàn vẹn",foreign_key_errors:"Số lỗi liên kết dữ liệu",recovery:"Kết quả khôi phục",missing:"Bị thiếu",corrupt:"Bị lỗi",orphan_objects:"Dữ liệu gốc chưa được quản lý",orphan_staging:"Tệp tạm còn sót",
    corrupt_objects:"Đối tượng dữ liệu bị lỗi",quarantined_pages:"Trang bị cách ly",unresolved_records:"Hồ sơ chưa xác định",host_policy:"Quy định truy cập nguồn",host:"Máy chủ nguồn",
    minimum_pacing_seconds:"Thời gian tối thiểu giữa các yêu cầu, tính bằng giây",max_attempts:"Số lần thử tối đa",redirects:"Chuyển hướng",session:"Phiên đăng nhập",
    snapshot:"Dữ liệu hiển thị",snapshot_built_at_utc:"Thời điểm tạo dữ liệu hiển thị",built_at:"Thời điểm cập nhật",present:"Đã có dữ liệu",data_root:"Thư mục dữ liệu",row_counts:"Số dòng từng loại",
    task:"Tác vụ định kỳ",task_name:"Tên tác vụ định kỳ",times:"Giờ chạy",exists:"Đã tồn tại",deleted:"Đã tắt",enabled:"Đang bật",profile:"Chế độ",key:"Tên mục",value_json:"Giá trị",updated_at_utc:"Cập nhật lúc",reason_kind:"Loại nguyên nhân",detail_scrubbed:"Nội dung thông báo",tail:"Phần cuối nhật ký"
};

function label(value) {
    if (value === null || value === undefined || value === "") return "Chưa có";
    var s = String(value);
    if (families[s]) return families[s];
    if (words[s]) return words[s];
    if (s.indexOf("legacy:") === 0) return "Kho cũ · " + label(s.slice(7));
    var prefixes = {"coverage:evidence:":"Bằng chứng khoảng thu thập ","legacy:rejected:":"Mục kho cũ cần kiểm tra ","legacy:import:":"Lần nhập kho cũ ","identity:audit:":"Nhật ký liên kết "};
    for (var key in prefixes) if (s.indexOf(key) === 0) return prefixes[key] + s.slice(key.length);
    if (s === "evidence_class") return "Loại dữ liệu thử nghiệm";
    if (s === "last_integrity_scan") return "Lần kiểm tra dữ liệu gần nhất";
    return s;
}
function family(value) { return families[value] || value || "Chưa rõ loại hồ sơ"; }
function timestampVN(value) {
    var d=new Date(Date.parse(value)+7*3600000);
    if(isNaN(d.getTime())) return "Chưa có";
    function pad(n){return n<10?"0"+n:String(n)}
    return pad(d.getUTCDate())+"/"+pad(d.getUTCMonth()+1)+"/"+d.getUTCFullYear()+" "+pad(d.getUTCHours())+":"+pad(d.getUTCMinutes());
}
function vietnamDay(offset) {
    var d = new Date(Date.now() + 7*3600000 + offset*86400000);
    function pad(n) { return n < 10 ? "0"+n : String(n); }
    return pad(d.getUTCDate()) + "/" + pad(d.getUTCMonth()+1) + "/" + d.getUTCFullYear();
}
function collectionResult(value) {
    var jobs = value.jobs || [], blocked = value.blocked || [];
    var lines = [];
    if (jobs.length && value.cadence === "WEEK") lines.push("Đã xếp hàng thu thập theo tuần, đủ 6 loại mỗi tuần. Ngày đã có bằng chứng đầy đủ sẽ được bỏ qua.");
    else if (jobs.length) lines.push("Đã xếp hàng " + jobs.length + "/" + (value.requested_families || jobs.length)
        + " loại: " + jobs.map(function(j) {return family(j.family)}).join(", ") + ".");
    if (value.worker_started) lines.push("Bộ thu thập xử lý lần lượt. Theo dõi tiến độ bên dưới.");
    if (value.error) lines.push(message(value.error));
    else if (!jobs.length && !blocked.length) lines.push(result(value));
    blocked.forEach(function(b) { if (b.error !== value.error) lines.push(b.error); });
    return lines.join("\n");
}
function collectionSpan(from, to, kind) {
    if (kind === "INCREMENTAL") return "Tự cập nhật đến hiện tại";
    var a = new Date(Date.parse(from) + 7*3600000);
    var b = new Date(Date.parse(to) + 7*3600000);
    if (isNaN(a.getTime()) || isNaN(b.getTime())) return "Chưa rõ khoảng thời gian";
    function pad(n) { return n < 10 ? "0"+n : String(n); }
    function day(d) { return pad(d.getUTCDate())+"/"+pad(d.getUTCMonth()+1)+"/"+d.getUTCFullYear(); }
    function midnight(d) { return d.getUTCHours()===0 && d.getUTCMinutes()===0 && d.getUTCSeconds()===0 && d.getUTCMilliseconds()===0; }
    if (midnight(a) && midnight(b)) return day(a) + " → hết " + day(new Date(b.getTime()-1)) + " (giờ Việt Nam)";
    function stamp(d) { return day(d)+" "+pad(d.getUTCHours())+":"+pad(d.getUTCMinutes()); }
    return stamp(a)+" → "+stamp(b)+" (giờ Việt Nam)";
}
function familyShort(value) {
    return ({TBMT:"Mời thầu",KHLCNT:"Kế hoạch",KQLCNT:"Kết quả",DU_AN:"Dự án",YCBG:"Báo giá",HOP_DONG:"Hợp đồng"})[value] || family(value);
}
function options(values) { return values.map(function(value) { return {label:label(value),value:value}; }); }
function message(value) {
    if (!value) return "";
    var s = String(value);
    if (words[s]) return words[s];
    if (s.indexOf(";") >= 0) return s.split(";").map(function(part){return message(part.trim());}).filter(function(part,i,all){return all.indexOf(part)===i;}).join(" · ");
    var prefixes = {MAX_PAGES_HIT:"Đã chạm giới hạn số trang", MAX_RECORDS_HIT:"Đã chạm giới hạn số hồ sơ", TOTAL_CHANGED:"Tổng số kết quả của nguồn đã thay đổi", DISTINCT_VS_TOTAL:"Số hồ sơ riêng biệt chưa khớp tổng số", ACCESS_BLOCKED:"Nguồn đã chặn truy cập", REQUEST_UNRECOVERED:"Một số yêu cầu chưa xử lý được", SOURCE_ACCESS_BLOCKED:"Nguồn đang bị chặn", SOURCE_ACCESS_DISABLED:"Nguồn đã bị tắt"};
    for (var key in prefixes) if (s.indexOf(key) === 0) return prefixes[key] + (s.indexOf("=") >= 0 ? ": " + s.split("=")[1] : ".");
    for (var code in words) if (s.indexOf(code + ":") === 0) return words[code];
    var rules = [
        [/DataRootBusy|is held by|database is locked/i,"Kho dữ liệu đang được sử dụng. Hãy dừng bộ thu thập rồi thử lại."],
        [/worker unreachable|PipeTransport|PipeClosed|not connected/i,"Chưa kết nối được bộ thu thập. Hãy khởi động bộ thu thập rồi thử lại."],
        [/PermissionError|Access is denied|access denied|not writable|quyền ghi/i,"Không có quyền ghi vào thư mục. Hãy chọn thư mục bạn được phép sử dụng."],
        [/not an ISO|Invalid isoformat|naive datetime|timezone|must be aware/i,"Thời gian chưa đúng định dạng. Dùng năm-tháng-ngàyTgiờ:phút:giâyZ, ví dụ 2026-11-01T17:00:00Z."],
        [/max_pages|pacing_seconds|max_records|max_requests|max_seconds/i,"Giới hạn thu thập chưa hợp lệ. Hãy nhập số dương; khoảng cách giữa hai yêu cầu ít nhất 3 giây."],
        [/checksum mismatch|hash mismatch|corrupt|unreadable object|missing raw/i,"Dữ liệu bị thiếu hoặc mã kiểm tra không khớp. Hãy kiểm tra lại kho nguồn hoặc chọn bản sao lưu khác."],
        [/FileNotFound|No such file|cannot find|does not exist|not found/i,"Không tìm thấy tệp hoặc thư mục. Hãy kiểm tra lại đường dẫn."],
        [/legacy.*unsupported|unsupported legacy|not a supported legacy|no such table|not a database|serving unreadable|Snapshot unreadable/i,"Không đọc được kho dữ liệu hoặc định dạng này chưa được hỗ trợ. Hãy kiểm tra dữ liệu hoặc chọn đúng thư mục kho."],
        [/escapes archive root|outside.*archive|outside.*source/i,"Đường dẫn dữ liệu không nằm đúng trong kho nguồn. Hãy kiểm tra lại thư mục nhập."],
        [/Reason and explicit evidence|required.*evidence/i,"Cần nhập lý do và bằng chứng trước khi thay đổi liên kết."],
        [/Select 1\.\.100|merge requires/i,"Chọn từ 1 đến 100 hồ sơ; thao tác gộp cần ít nhất 2 hồ sơ."],
        [/Unknown entity|Select the entity/i,"Cần chọn đúng nhóm hồ sơ để thực hiện thao tác."],
        [/already undone/i,"Thao tác này đã được hoàn tác."],
        [/newer edits/i,"Có thay đổi mới hơn. Hãy hoàn tác thay đổi mới nhất trước."],
        [/unknown.*kind|Unsupported|Unknown read/i,"Thao tác này chưa được hỗ trợ."],
        [/SYNTHETIC_ROOT/i,"Đây là kho dữ liệu thử nghiệm. Chỉ mở bằng chế độ thử nghiệm ngoại tuyến."],
        [/Collection in progress/i,"Đang thu thập; tạm hiển thị dữ liệu ở lần cập nhật trước."],
        [/Read-only UI/i,"Màn hình dùng bản dữ liệu hiển thị riêng để việc xem hồ sơ không làm gián đoạn thu thập."],
        [/live.*unverified|endpoint.*unverified/i,"Nguồn dữ liệu thật chưa được xác minh. Cần chủ hệ thống phê duyệt trước khi thu thập."],
        [/owner|commercial/i,"Giấy phép phần mềm cần chủ hệ thống xác nhận trước khi phân phối. Xem thư mục giấy phép đi kèm."],
        [/Collector exit|Process crashed|Failed to start/i,"Bộ xử lý đã dừng hoặc không khởi động được. Hãy xem nhật ký và thử lại."]
    ];
    for (var i=0;i<rules.length;i++) if (rules[i][0].test(s)) return rules[i][1];
    if (/[ăâđêôơưáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ]/i.test(s))
        return s.replace(/snapshot/gi,"dữ liệu hiển thị").replace(/collector|worker/gi,"bộ thu thập").replace(/\braw\b/gi,"dữ liệu gốc").replace(/\bjob\b/gi,"tác vụ").replace(/\bimport\b/gi,"nhập dữ liệu").replace(/\blegacy\b/gi,"kho cũ");
    return "Chưa xử lý được yêu cầu. Hãy kiểm tra dữ liệu hoặc xem nhật ký để biết chi tiết.";
}
function pretty(value, depth) {
    depth = depth || 0;
    if (value === null || value === undefined) return "Chưa có dữ liệu";
    if (typeof value !== "object") return label(value);
    var pad = new Array(depth+1).join("  ");
    if (Array.isArray(value)) return value.length ? value.map(function(v,i) { return pad + (i+1) + ". " + pretty(v,depth+1); }).join("\n") : "Chưa có mục nào";
    return Object.keys(value).map(function(k) {
        var item = value[k];
        if (/_json$/.test(k) && typeof item === "string") { try { item=JSON.parse(item); } catch (_) {} }
        var shown = (k === "error" || k === "reason" || k === "blocked_reason") ? message(item) : pretty(item,depth+1);
        return pad + (fields[k] || families[k] || words[k] || k) + ": " + (typeof item === "object" && item !== null ? "\n" : "") + shown;
    }).join("\n");
}
function result(value) {
    if (typeof value === "string") { try { value=JSON.parse(value); } catch (_) { return message(value); } }
    value = value || {};
    if (value.progress && value.running) return "Đang nhập: " + (value.progress.seen || 0) + " mục đã đọc · " + (value.progress.imported || 0) + " mục đã nhập · " + (value.progress.rejected || 0) + " mục cần kiểm tra.";
    if (value.error) return message(value.error);
    if (value.job_id && value.ok) return "Đã xếp hàng tác vụ " + value.job_id + ".";
    if (value.queued) return "Đã gửi yêu cầu. Trạng thái sẽ cập nhật khi bộ thu thập xử lý xong trang hiện tại.";
    if (value.spawned) return "Đang khởi động bộ thu thập.";
    if (value.attached) return "Bộ thu thập đang chạy và đã được kết nối.";
    if (value.running) return "Đang thực hiện: " + label(value.action) + "…";
    if (value.status) return label(value.status) + (value.seen !== undefined ? " · Đã đọc " + value.seen + " mục; đã nhập " + value.imported + "; trùng " + value.duplicates + "; cần kiểm tra " + value.rejected + "." : "");
    if (value.action_id) return "Đã ghi nhận thao tác. Mã nhật ký: " + value.action_id + ".";
    if (value.ok === false) return "Thao tác chưa hoàn tất. " + pretty(value);
    if (value.path) return "Hoàn tất. Đường dẫn: " + value.path + (value.records !== undefined ? " · " + value.records + " hồ sơ." : "");
    return (value.ok ? "Hoàn tất.\n" : "") + pretty(value);
}
function rawPreview(value) {
    if (value.error) return message(value.error);
    return "Đã kiểm tra mã SHA256: " + value.sha256 + "\nDữ liệu gốc bên dưới được giữ nguyên văn.\n" + (value.truncated ? "Bản xem trước đã được rút gọn.\n" : "") + (value.text || "");
}
