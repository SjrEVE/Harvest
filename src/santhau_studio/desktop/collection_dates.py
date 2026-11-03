"""Translate calendar days to the existing half-open, timezone-aware job scope."""
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

VN = ZoneInfo("Asia/Ho_Chi_Minh")


class DateRangeError(ValueError):
    """An actionable calendar-input error for the collection form."""


def _day(value: object, label: str) -> date:
    text = str(value or "").strip()
    if not text:
        raise DateRangeError(f"Hãy chọn {label.lower()}.")
    try:
        return datetime.strptime(text, "%d/%m/%Y").date() if "/" in text else date.fromisoformat(text)
    except ValueError as exc:
        raise DateRangeError(f"{label} không hợp lệ. Nhập ngày/tháng/năm, ví dụ 01/11/2026, hoặc chọn trên lịch.") from exc


def collection_scope(payload: dict, kind: str) -> dict[str, str]:
    if "date_from" in payload or "date_to" in payload:
        if kind == "INCREMENTAL":
            raise DateRangeError("Cập nhật tự động không dùng khoảng ngày. Hãy chọn Thu thập theo khoảng ngày để lấy đúng các ngày bạn chọn.")
        first = _day(payload.get("date_from"), "Ngày bắt đầu")
        last = _day(payload.get("date_to"), "Ngày kết thúc")
        if first > last:
            raise DateRangeError("Ngày kết thúc phải bằng hoặc sau ngày bắt đầu.")
        if last > datetime.now(VN).date():
            raise DateRangeError("Không thể thu thập ngày chưa tới. Ngày kết thúc phải là hôm nay hoặc trước đó.")
        try:
            end = datetime.combine(last + timedelta(days=1), time.min, VN)
        except OverflowError as exc:
            raise DateRangeError("Ngày kết thúc nằm ngoài phạm vi lịch được hỗ trợ.") from exc
        start = datetime.combine(first, time.min, VN)
        return {"from": start.astimezone(UTC).isoformat(), "to": end.astimezone(UTC).isoformat()}
    if payload.get("auto_range") is True:
        if kind != "INCREMENTAL":
            raise DateRangeError("Hãy chọn khoảng ngày cho tác vụ này.")
        # Same request envelope as the scheduler. Runner.incremental resolves the actual
        # watermark/overlap at execution time; the UI explicitly labels this automatic.
        now = datetime.now(UTC)
        return {"from": (now - timedelta(days=3)).isoformat(), "to": now.isoformat()}
    # Preserve precise timestamp callers and the worker's immutable request contract.
    return {"from": str(payload["from"]), "to": str(payload["to"])}
