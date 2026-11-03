"""Measure the serving read path the UI actually uses, plus EXPLAIN QUERY PLAN for its SQL.

Run against a fixture root (tools/fixture_serving.py) so the numbers are about queries, not about
collection cost. Reports p50/p95 over repeated calls, and the process working set, so a single
lucky run cannot be presented as the p95.

Run: uv run python tools/perf_probe.py --data-root _evidence/rc/perf-1m --out _evidence/rc/perf-before.json
"""

from __future__ import annotations

import argparse
import ctypes
import json
import random
import statistics
import sys
import time
from pathlib import Path

from santhau_studio.desktop.backend import StudioBackend


def working_set_bytes() -> int | None:
    if sys.platform != "win32":
        return None
    class _Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = _Counters()
    counters.cb = ctypes.sizeof(_Counters)
    ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]  # secret-scan:allow Win32 API signature, not a credential
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)  # secret-scan:allow Win32 API name
    return int(counters.WorkingSetSize) if ok else None


def timed(call, runs: int) -> list[float]:
    times = []
    for _ in range(runs):
        started = time.perf_counter()
        call()
        times.append((time.perf_counter() - started) * 1000)
    return times


def stats(times: list[float]) -> dict:
    return {
        "runs": len(times),
        "p50_ms": round(statistics.median(times), 2),
        "p95_ms": round(statistics.quantiles(times, n=20)[-1] if len(times) >= 20 else max(times), 2),
        "max_ms": round(max(times), 2),
    }


def explain(conn, sql: str, params: tuple = ()) -> list[str]:
    return [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    started = time.perf_counter()
    backend = StudioBackend(args.data_root)
    onboarding = backend.onboarding()
    startup_ms = (time.perf_counter() - started) * 1000

    search = timed(lambda: backend.search({"text": "gói thầu", "limit": 50}), 25)
    search_family = timed(lambda: backend.search({"text": "thiết bị y tế", "family": "KHLCNT", "limit": 50}), 15)
    dashboard = timed(backend.control_center, 10)
    coverage = timed(lambda: backend.coverage(None), 10)
    rng = random.Random(7)
    from santhau_studio.storage.warehouse import connect_read_only

    conn = connect_read_only(args.data_root / "serving.sqlite")
    try:
        max_id = conn.execute("SELECT MAX(id) FROM source_records").fetchone()[0]
        detail_ids = [rng.randrange(1, max_id + 1) for _ in range(20)]
        cursor = iter(detail_ids * 2)
        detail = timed(lambda: backend.record_detail(next(cursor)), 20)

        hits = backend.search({"text": "gói thầu", "limit": 50})["hits"]
        search_plan = explain(
            conn,
            "SELECT s.record_id FROM search_index s WHERE search_index MATCH ?"
            " AND (? IS NULL OR s.family = ?) ORDER BY rank LIMIT ? OFFSET ?",
            ("goi* AND thau*", None, None, 50, 0),
        )
        detail_plan = explain(
            conn,
            "SELECT id FROM source_record_versions WHERE source_record_id=? ORDER BY content_version_seq",
            (1,),
        )
        index_rows = conn.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type='index' ORDER BY name"
        ).fetchall()
    finally:
        conn.close()

    report = {
        "records": onboarding["snapshot"].get("row_counts", {}).get("source_records"),
        "startup_ms": round(startup_ms, 2),
        "search": stats(search),
        "search_family": stats(search_family),
        "dashboard": stats(dashboard),
        "coverage": stats(coverage),
        "record_detail": stats(detail),
        "hits_returned": len(hits),
        "working_set_bytes": working_set_bytes(),
        "plans": {"search": search_plan, "detail_versions": detail_plan},
        "serving_indexes": [tuple(row) for row in index_rows],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("search", "search_family", "dashboard", "record_detail",
                                             "startup_ms", "working_set_bytes")}, indent=2))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
