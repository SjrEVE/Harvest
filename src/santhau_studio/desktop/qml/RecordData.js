.pragma library

function value(v) {
    return v === null || v === undefined || v === "" ? "Chưa có" : String(v);
}
function evidence(row) {
    try { return JSON.parse(row.provenance_json || "{}"); }
    catch (e) { return {}; }
}
function quantity(row) {
    if (row.quantity_presence === "KNOWN" && row.quantity !== null && row.quantity !== undefined)
        return String(row.quantity) + (row.unit ? " " + row.unit : "");
    if (row.quantity_presence === "UNPARSABLE") return "Chưa đọc được số lượng · nguồn ghi: " + value(row.quantity_raw);
    return "Chưa có số lượng";
}
function role(row) {
    var names = {OFFEROR:"Đơn vị chào giá",WINNER:"Nhà thầu trúng thầu",BIDDER:"Nhà thầu tham dự",MEMBER:"Thành viên liên danh",LEAD:"Thành viên đứng đầu"};
    return names[row.role] || value(row.role);
}
function title(kind, row) {
    if (kind === "goods_rows") return value(row.name);
    if (kind === "lots") return value(row.lot_no_raw) + " · " + value(row.lot_label);
    if (kind === "documents") return value(row.name);
    if (kind === "contractor_memberships") return value(row.party_name);
    if (kind === "versions") return "Phiên bản nội dung " + value(row.content_version_seq);
    if (kind === "raw_revisions") return "Dữ liệu gốc lần " + value(row.revision_no);
    return "Lần lấy " + value(row.capture_seq) + " · " + value(row.lane);
}
function subtitle(kind, row) {
    if (kind === "goods_rows") return quantity(row) + " · " + (row.lot_no_raw || "Chưa gắn lô");
    if (kind === "lots") return value(row.goods_count) + " dòng hàng hóa";
    if (kind === "documents") return row.object_sha ? "Đã lưu tệp · xem thông tin kiểm tra" : "Chưa tải tệp · chỉ có tham chiếu";
    if (kind === "contractor_memberships") return role(row);
    return value(row.fetched_at_utc || row.first_observed_at_utc || row.captured_at_utc);
}
function describe(kind, row) {
    var p = evidence(row), lines = [title(kind,row), ""], raw = p.raw_row || {};
    if (kind === "goods_rows") {
        lines.push("Số lượng: " + quantity(row));
        lines.push("Lô: " + (row.lot_no_raw ? row.lot_no_raw + (row.lot_label ? " · " + row.lot_label : "") : "Chưa gắn lô trong dữ liệu đã lấy"));
        lines.push("", "Yêu cầu kỹ thuật", value(row.specification));
        var extra = {brand:"Nhãn hiệu",manufacturer:"Nhà sản xuất",origin:"Xuất xứ",code:"Mã hàng",model:"Model"};
        for (var k in extra) if (raw[k] !== null && raw[k] !== undefined && raw[k] !== "") lines.push("", extra[k] + ": " + value(raw[k]));
        if ((p.problems || []).length) lines.push("", "Dữ liệu có phần chưa phân tích được. Xem Thông tin kỹ thuật để đối chiếu nguồn.");
    } else if (kind === "lots") {
        lines.push("Số dòng hàng hóa: " + value(row.goods_count), "Phiên bản lô: " + value(row.lot_version));
    } else if (kind === "documents") {
        lines.push(subtitle(kind,row));
        if (!row.object_sha) lines.push("Ứng dụng đang giữ mã và tên tệp do nguồn công bố. Chưa có nội dung tệp để mở.");
        if (p.reference_current === false) lines.push("Nguồn không còn xác nhận tham chiếu này trong lần lấy mới; tệp đã lưu được giữ lại.");
        lines.push("", "Loại tài liệu: " + (row.document_kind === "PLAN_DECISION" ? "Quyết định kế hoạch" : value(row.document_kind)),
                   "Thuộc hồ sơ: " + value(p.owner_key || row.owner_key),
                   "Dung lượng đã lưu: " + (row.byte_len === null || row.byte_len === undefined ? "Chưa có" : row.byte_len + " byte"));
    } else if (kind === "contractor_memberships") {
        lines.push("Vai trò: " + role(row), "Mã số thuế: " + (row.party_tax_id_presence === "KNOWN" ? value(row.party_tax_id) : "Nguồn chưa cung cấp"));
        if (row.membership_share_presence === "KNOWN") lines.push("Tỷ lệ tham gia: " + value(row.membership_share));
    } else {
        lines.push("Thời điểm ghi nhận: " + subtitle(kind,row));
        lines.push("", "Chọn Thông tin kỹ thuật để xem toàn bộ trường và mã kiểm tra.");
    }
    if (p.lane) lines.push("", "Nội dung nguồn: " + ({DETAIL:"Chi tiết hồ sơ",HSMT:"Hồ sơ mời thầu",REOFFER:"Thông tin chào giá"}[p.lane] || p.lane) + " · lần lưu " + value(p.capture_seq));
    return lines.join("\n");
}
