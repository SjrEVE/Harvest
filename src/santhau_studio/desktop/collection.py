"""Vietnamese collection feedback, kept separate from coverage proof."""
from datetime import datetime, time, timedelta

from santhau_studio.collectors.source_registry import FAMILY_ORDER
from santhau_studio.desktop.collection_dates import VN
from santhau_studio.storage.coverage import resolve

FAMILY_NAMES = {
    "TBMT": "Mời thầu", "KQLCNT": "Kết quả lựa chọn nhà thầu",
    "HOP_DONG": "Hợp đồng", "KHLCNT": "Kế hoạch lựa chọn nhà thầu",
    "DU_AN": "Dự án", "YCBG": "Yêu cầu báo giá",
}


def unverified(family):
    return {"family": family, "status": "BLOCKED", "error_code": "BOUNDARY_RULE_UNVERIFIED",
            "error": f"{FAMILY_NAMES.get(family, family)}: nguồn chưa được xác minh cho loại hồ sơ này. "
                     "Cần chủ hệ thống phê duyệt kiểm tra nguồn trước khi chạy."}


def overview(backend):
    yesterday = datetime.now(VN).date() - timedelta(days=1)
    start = datetime.combine(yesterday, time.min, VN)
    end = start + timedelta(days=1)
    result = {"yesterday": yesterday.strftime("%d/%m/%Y"), "families": []}
    # Do not hold one Windows snapshot handle while opening six more. A reader
    # spanning those calls prevents the worker from replacing the snapshot.
    for family in FAMILY_ORDER:
        ready = backend._verified_rule("egp_home", family) is not None
        conn = backend._serving()
        try:
            status = resolve(conn, "egp_home", family, start, end).status if conn else "NOT_ACCESSED"
        finally:
            if conn:
                conn.close()
        result["families"].append({
            "family": family, "readiness": "RULE_VERIFIED" if ready else "BLOCKED",
            "reason": "" if ready else unverified(family)["error"],
            "yesterday_status": status,
        })
    return result
