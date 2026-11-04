"""Prepare or explicitly run a bounded source census, without modifying the Data Root.

No --approved-requests means no network. Live mode refuses an active collector and
synthetic roots. Each invocation needs an owner-approved request allowance; retries
consume that same allowance. Output is SOURCE_COUNT_ONLY, never coverage COMPLETE.
"""
import argparse
import hashlib
import json
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from santhau_studio.collectors.boundary import BoundaryRule, source_window_for
from santhau_studio.collectors.budget import RequestBudget
from santhau_studio.collectors.egp_home import EgpHomeLane, SearchWindow
from santhau_studio.collectors.http_client import FetchLane
from santhau_studio.collectors.network_policy import scrub_exception
from santhau_studio.collectors.source_count import count_windows, new_report
from santhau_studio.collectors.source_registry import FAMILY_ORDER, query_contract_for
from santhau_studio.desktop.backend import StudioBackend
from santhau_studio.desktop.collection_dates import collection_scope
from santhau_studio.domain.coverage import Interval
from santhau_studio.domain.redaction import redact_payload
from santhau_studio.storage.coverage import boundary_rule


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--date-from", required=True)
    parser.add_argument("--date-to", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--approved-requests", type=int, default=0)
    parser.add_argument("--max-seconds", type=int, default=600)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.approved_requests <= 3000 or not 1 <= args.max_seconds <= 86400:
        parser.error("Giới hạn: 0-3000 yêu cầu, 1-86400 giây.")
    scope = collection_scope({"date_from": args.date_from, "date_to": args.date_to}, "BACKFILL")
    span = Interval(*(datetime.fromisoformat(scope[k]) for k in ("from", "to")))
    report = new_report(span, FAMILY_ORDER)
    if args.approved_requests == 0:
        print(json.dumps({"mode": "PLAN_ONLY_NO_NETWORK", "scope": scope, "families": list(FAMILY_ORDER),
            "operation": "Đọc trang đầu, chia đôi khoảng đạt trần 10.000; không tải tài liệu, không ghi kho.",
            "blocked_until": "Owner duyệt hạn mức mới; bộ thu thập hiện tại đã dừng."}, ensure_ascii=False))
        return 0
    backend = StudioBackend(args.data_root)
    if backend.workspace_identity()["synthetic"] or backend.worker_status()["running"]:
        parser.error("Chỉ đo nguồn thật khi bộ thu thập đã dừng; không dùng kho thử nghiệm.")
    if args.output.resolve().is_relative_to(args.data_root.resolve()):
        parser.error("Lưu báo cáo ngoài Data Root đang sử dụng.")
    rules = {}
    with closing(backend._serving()) as conn:
        if conn is None:
            parser.error("Kho chưa có dữ liệu hiển thị để kiểm tra quy tắc nguồn.")
        state = conn.execute("SELECT access_state FROM source_registry WHERE source_key='egp_home'").fetchone()
        if not state or state[0] != "ALLOWED_AND_VERIFIED":
            parser.error("Nguồn chưa được phép truy cập hoặc đang bị chặn.")
        for family in FAMILY_ORDER:
            verified = backend._verified_rule("egp_home", family)
            if not verified:
                parser.error(f"{family}: chưa xác minh quy tắc thời gian.")
            rule = boundary_rule(conn, source_key="egp_home", family=family,
                endpoint=verified["endpoint"], adapter_sha=verified["adapter_sha"])
            if not rule:
                parser.error(f"{family}: quy tắc nguồn không khớp.")
            rules[family] = rule
    contract = {f: {"adapter_sha": backend._verified_rule("egp_home", f)["adapter_sha"],
        "boundary_rule_id": rules[f]["id"], "query_contract": query_contract_for(f)} for f in FAMILY_ORDER}
    if args.resume:
        report = json.loads(args.output.read_text(encoding="utf-8"))
        if (report["from"], report["to"], report.get("contracts")) != (span.from_utc.isoformat(), span.to_utc.isoformat(), contract):
            parser.error("Báo cáo cũ khác khoảng ngày hoặc quy tắc nguồn; không thể cộng chung.")
        for node in report["windows"]:
            window = Interval(datetime.fromisoformat(node["from"]), datetime.fromisoformat(node["to"]))
            if node["family"] not in FAMILY_ORDER or not span.contains(window):
                parser.error("Cửa sổ tiếp tục nằm ngoài phạm vi đã duyệt.")
    elif args.output.exists():
        parser.error("Báo cáo đã tồn tại; dùng --resume để lấy tiếp, không ghi đè.")
    report["contracts"] = contract
    budget = RequestBudget(args.approved_requests, args.max_seconds)
    raw_dir = args.output.parent / (args.output.stem + "-responses")
    raw_dir.mkdir(parents=True, exist_ok=True)
    previous_requests = report.get("requests_used", 0)
    def save(value):
        value["requests_used"] = previous_requests + budget.used
        value["updated_at"] = datetime.now(UTC).isoformat()
        part = args.output.with_suffix(".part")
        part.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        part.replace(args.output)
    # One lane for all families preserves pacing and one shared budget including retries.
    with closing(FetchLane(url="https://muasamcong.mof.gov.vn/o/egp-portal-home/services/smart/search",
                           source_key="egp_home", budget=budget)) as lane:
        def fetch(family, window):
            if backend.worker_status()["running"]:
                raise RuntimeError("Bộ thu thập vừa khởi động; dừng đo tổng để tránh chạy đồng thời.")
            a, b = source_window_for(BoundaryRule.from_row(rules[family]), window)
            home = EgpHomeLane(family=family, lane=lane, pacing_seconds=3,
                              window=SearchWindow(date_filter_verified=True, from_local=a, to_local=b))
            page = home.fetch_page(0)
            rows = page.payload["page"]["content"]
            if not all(isinstance(row, dict) and home.binding.spec.accepts_record(row) for row in rows):
                raise ValueError("Nguồn trả hồ sơ khác loại; không dùng tổng này.")
            raw = json.dumps(redact_payload(page.payload), sort_keys=True, ensure_ascii=False).encode("utf-8")
            digest = hashlib.sha256(raw).hexdigest()
            (raw_dir / (digest + ".json")).write_bytes(raw)
            return {"total": page.total_hint, "trusted": page.total_hint_trusted,
                    "measured_at": datetime.now(UTC).isoformat(), "response_sha256": digest}
        minimum = max({"DAY": timedelta(days=1), "HOUR": timedelta(hours=1), "MINUTE": timedelta(minutes=1),
                       "SECOND": timedelta(seconds=1), "MILLISECOND": timedelta(milliseconds=1)}[r["precision"]]
                      for r in rules.values())
        try:
            count_windows(report, fetch, max_windows=args.approved_requests, minimum=minimum, checkpoint=save)
        except Exception as exc:
            report["stop_reason"] = scrub_exception(exc)
        save(report)
    print(json.dumps({"families": report.get("families"), "requests_used": budget.used,
                      "output": str(args.output), "evidence_class": "SOURCE_COUNT_ONLY"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
