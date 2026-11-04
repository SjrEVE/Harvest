"""Inspect preserved source fields without rewriting canonical history."""
import json
from collections import Counter, defaultdict
from contextlib import closing

from santhau_studio.collectors.offline_acceptance import enabled
from santhau_studio.storage.rawstore import RawObjectStore
from santhau_studio.storage.warehouse import connect_read_only

NAMES = {
    "bidField":"Lĩnh vực đấu thầu", "bidId":"Định danh gói thầu", "bidNamePlanNew":"Tên gói theo kế hoạch mới",
    "bidNo":"Mã gói thầu", "caseKHKQ":"Phân loại kế hoạch, kết quả theo nguồn", "createdBy":"Tài khoản công bố",
    "districtCode":"Mã quận, huyện", "id":"Định danh trên hệ thống nguồn", "investField":"Lĩnh vực đầu tư",
    "isInternet":"Đấu thầu qua mạng", "isMedicine":"Phân loại thuốc", "isMultiLot":"Gói thầu nhiều lô",
    "isPass":"Trạng thái đạt theo nguồn", "medicines":"Thông tin thuốc", "notifyId":"Định danh thông báo",
    "numClarifyReq":"Số yêu cầu làm rõ", "numPetition":"Số kiến nghị", "numPetitionHsmt":"Kiến nghị hồ sơ mời thầu",
    "numPetitionKqlcnt":"Kiến nghị kết quả lựa chọn", "numPetitionLcnt":"Kiến nghị lựa chọn nhà thầu",
    "originalPublicDate":"Ngày công bố ban đầu", "planNoStand":"Số kế hoạch chuẩn", "pnoStand":"Mã dự án chuẩn",
    "priceInit":"Giá ban đầu theo nguồn", "priceStep":"Bước giá", "processApply":"Quy trình áp dụng",
    "procuringEntityCode":"Mã bên mời thầu", "provCode":"Mã tỉnh, thành phố", "score":"Điểm do nguồn cung cấp",
    "stepCode":"Mã bước xử lý", "type":"Loại bản ghi nguồn", "ventureName":"Tên liên danh", "workType":"Loại công việc",
    "notifyNo":"Số thông báo", "notifyVersion":"Phiên bản thông báo", "bidName":"Tên gói thầu",
    "publicDate":"Ngày công bố", "publicDateKqlcnt":"Ngày công bố kết quả", "bidCloseDate":"Đóng thầu",
    "bidOpenDate":"Mở thầu", "investorName":"Chủ đầu tư", "investorCode":"Mã chủ đầu tư",
    "procuringEntityName":"Bên mời thầu", "bidPrice":"Giá gói thầu", "bidWinningPrice":"Giá trúng thầu",
    "ctPriceAfter":"Giá hợp đồng", "currency":"Đơn vị tiền tệ", "currencyCode":"Mã tiền tệ",
    "locations":"Địa điểm", "provinceName":"Tỉnh, thành phố", "bidForm":"Hình thức lựa chọn",
    "bidMode":"Phương thức lựa chọn", "planNo":"Số kế hoạch", "planVersion":"Phiên bản kế hoạch",
    "pno":"Mã dự án", "pname":"Tên dự án", "pversion":"Phiên bản dự án", "name":"Tên",
    "contractCode":"Mã hợp đồng", "contractVersion":"Phiên bản hợp đồng", "contractNo":"Số hợp đồng",
    "contractSignDate":"Ngày ký hợp đồng", "ctName":"Tên hợp đồng", "winningCode":"Mã nhà thầu trúng",
    "winningContractorName":"Nhà thầu trúng", "contractorName":"Nhà thầu", "decisionDate":"Ngày quyết định",
    "investTotal":"Tổng mức đầu tư", "investTotalUnit":"Đơn vị tổng mức đầu tư", "bidPriceUnit":"Đơn vị giá",
    "status":"Trạng thái nguồn", "statusForNotify":"Trạng thái thông báo", "inputResultId":"Mã kết quả",
    "numBidderJoin":"Số nhà thầu tham dự", "numBidderTech":"Số nhà thầu đạt kỹ thuật",
    "isDomestic":"Trong nước", "approvalStatus":"Trạng thái phê duyệt", "planType":"Loại kế hoạch",
    "notifyNoStand":"Số thông báo chuẩn", "pgroup":"Nhóm dự án", "location":"Địa điểm thực hiện",
    "haveBidNotNotify":"Có gói thầu chưa thông báo",
}


def source_fields(root, sha):
    try:
        with closing(RawObjectStore(root, read_only=True)) as store:
            raw = store.read(sha)
        if len(raw) > 16 * 1024 * 1024:
            return {"verified": True, "fields": [], "error": "Dữ liệu nguồn quá lớn để xem trực tiếp; dùng bản xuất."}
        values = json.loads(raw)
        if not isinstance(values, dict):
            return {"verified": True, "fields": [], "values": values}
        return {"verified": True, "sha256": sha, "values": values, "fields": [
            {"key":key, "label":NAMES.get(key, "Thuộc tính nguồn · " + key), "value":value,
             "presence":"UNKNOWN" if value is None or value == "" or value == [] else "KNOWN"}
            for key,value in values.items()]}
    except (OSError, ValueError, RuntimeError) as exc:
        return {"verified": False, "fields": [], "error": f"Không đọc được dữ liệu nguồn: {exc}"}


def audit_root(root):
    """Full local hash/referential audit; source completeness is a separate question."""
    findings, families, fields = [], Counter(), defaultdict(Counter)
    with closing(connect_read_only(root / "warehouse.sqlite")) as conn, closing(RawObjectStore(root, read_only=True)) as store:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        objects = conn.execute("SELECT sha256 FROM raw_objects").fetchall()
        for row in objects:
            try:
                store.read(row[0])
            except (OSError, ValueError, RuntimeError) as exc:
                findings.append({"sha256":row[0], "error":str(exc)})
        for row in conn.execute("SELECT family,current_record_object_sha FROM source_records"):
            families[row["family"]] += 1
            try:
                value = json.loads(store.read(row["current_record_object_sha"]))
                if isinstance(value, dict):
                    fields[row["family"]].update(value.keys())
            except (OSError, ValueError, RuntimeError):
                continue  # Already named by the hash audit; do not invent a field count.
        duplicates = conn.execute("SELECT count(*) FROM (SELECT identity_key FROM source_records GROUP BY identity_key HAVING count(*)>1)").fetchone()[0]
        unresolved = conn.execute("SELECT count(*) FROM unresolved_records WHERE resolved_at_utc IS NULL").fetchone()[0]
        quarantine = conn.execute("SELECT count(*) FROM quarantined_responses").fetchone()[0]
        synthetic_records = conn.execute("SELECT count(*) FROM source_records WHERE natural_key LIKE 'SYNTH-%'").fetchone()[0]
    return {"integrity":integrity, "foreign_key_errors":foreign, "objects_checked":len(objects),
            "raw_failures":findings, "duplicate_identities":duplicates, "unresolved":unresolved,
            "quarantined":quarantine, "synthetic":enabled(root), "synthetic_records":synthetic_records,
            "families":dict(families), "source_field_counts":{family:dict(counts) for family,counts in fields.items()},
            "technical_status":"PASS" if integrity=="ok" and not (foreign or findings or duplicates or unresolved or quarantine) else "FAIL",
            "business_completeness":"NOT_VERIFIED",
            "note":"Kiểm tra toàn bộ dữ liệu đã lưu; không chứng minh nguồn đã cung cấp đầy đủ mọi hồ sơ, trường và tài liệu."}
