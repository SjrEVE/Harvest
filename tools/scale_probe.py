"""Scale probe: 100k listing pages x 10 records = 1M source records, through the production writer.

The point is a reproducible end-to-end number on real code paths, not a synthetic microbenchmark:
every page goes through `WarehouseWriter.commit_page` (objects buffered in segments, content
versions, observations), then a serving snapshot is built (FTS over 1M rows), then the desktop
backend answers the dashboard/search queries the UI issues.

Run in the background:
  uv run python tools/scale_probe.py --pages 100000 --out _evidence/rc/scale-1m.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from santhau_studio.collectors.egp_home import Page
from santhau_studio.collectors.extraction import extract_page
from santhau_studio.collectors.source_registry import SOURCE_EGP_HOME, SOURCES, binding_for
from santhau_studio.storage import serving
from santhau_studio.storage.warehouse import WarehouseWriter

PER_PAGE = 10


def page_of(page_no: int, rows: int) -> Page:
    content = [
        {"notifyNo": f"IB-SCALE-{page_no}-{i}", "notifyVersion": "00",
         "bidName": f"Gói thầu quy mô {page_no}-{i}", "investorName": f"Ban QLDA {page_no % 500}"}
        for i in range(rows)
    ]
    return Page(payload={"page": {"content": content, "totalElements": rows}}, page_no=page_no,
                rows=rows, has_more=False, total_hint=rows, total_hint_trusted=True,
                http_status=200, attempt_log=())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=100_000)
    parser.add_argument("--records-per-page", type=int, default=PER_PAGE)
    parser.add_argument("--data-root", type=Path, default=Path("_evidence/rc/scale-1m"))
    parser.add_argument("--out", type=Path, default=Path("_evidence/rc/scale-1m.json"))
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    root = args.data_root
    if root.exists():
        shutil.rmtree(root)
    writer = WarehouseWriter.open(root, create_schema=True, role="scale", raw_layout="segments")
    report: dict[str, object] = {"pages": args.pages, "records_per_page": args.records_per_page,
                                 "layout": "segments"}
    try:
        writer.conn.execute(
            "INSERT INTO filter_boundary_rules (rule_key, rule_version, source_key, family, endpoint,"
            " adapter_sha, boundary_kind, precision, source_timezone, offset_minutes, conversion_params,"
            " canary_verified, verified_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("scale", 1, SOURCE_EGP_HOME, "TBMT", SOURCES[SOURCE_EGP_HOME].endpoint,
             "0" * 64, "CLOSED_LOCAL", "DAY", "Asia/Ho_Chi_Minh", 420, '{"to_minus":"1s"}', 1,
             "2026-10-31T00:00:00Z"),
        )
        writer.conn.commit()
        writer.register_run("run-scale", "job-scale", "BACKFILL")
        started = time.perf_counter()
        for page_no in range(args.pages):
            page = page_of(page_no, args.records_per_page)
            response_ref = writer.store_response(page.payload)
            attempt = writer.ensure_attempt(
                run_id="run-scale", family="TBMT", endpoint="scale",
                interval_from="2026-11-01T00:00:00Z", interval_to="2026-11-02T00:00:00Z",
                page_no=page_no, attempt_no=1, outcome="OK", response=response_ref,
            )
            report_batch = extract_page(page.payload, binding_for("TBMT").spec)
            writer.commit_page(run_id="run-scale", attempt_id=attempt,
                               response_ref=response_ref, records=report_batch.records)
        report["write_seconds"] = round(time.perf_counter() - started, 1)
        report["records"] = writer.conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0]
        report["observations"] = writer.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    finally:
        writer.close()

    started = time.perf_counter()
    published = serving.build_serving_db(root / "warehouse.sqlite", root / "serving.sqlite")
    report["serving_publish_seconds"] = round(time.perf_counter() - started, 1)
    report["serving_published"] = published.published
    report["serving_reason"] = published.reason

    from santhau_studio.desktop.backend import StudioBackend

    backend = StudioBackend(root)
    probes = {}
    for name, call in (
        ("control_center", lambda: backend.control_center()),
        ("coverage", lambda: backend.coverage(None)),
        ("search_common", lambda: backend.search({"text": "gói thầu", "limit": 50})),
        ("search_family", lambda: backend.search({"text": "gói thầu", "family": "TBMT", "limit": 50})),
        ("record_detail", lambda: backend.record_detail(1)),
    ):
        started = time.perf_counter()
        result = call()
        probes[name] = {"seconds": round(time.perf_counter() - started, 3), "count": len(result) if hasattr(result, "__len__") else None}
    report["ui_probes"] = probes
    report["search_hits"] = len(backend.search({"text": "gói thầu", "limit": 50})["hits"])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    if not args.keep:
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
