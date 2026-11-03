"""Production raw-store benchmark: the real RawObjectStore, both layouts (§5.1 decision input).

`bench_raw_store.py` compares two *modelled* layouts. This tool removes the model: it writes
synthetic page/record payloads through `RawObjectStore.put`, reads every object back through
`RawObjectStore.read`, verifies each one, and reports the on-disk footprint. Dual-read means both
layouts are readable regardless of which one wrote them, so the comparison changes no contract.

Run: uv run python tools/bench_production_store.py --pages 2000 --out _evidence/rc/prod-bench.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from santhau_studio.storage.rawstore import LAYOUT_OBJECTS, LAYOUT_SEGMENTS, RawObjectStore


def payloads(pages: int, records_per_page: int):
    for page_no in range(pages):
        content = [
            {
                "notifyNo": f"IB{page_no}-{i}",
                "notifyVersion": "00",
                "title": f"Gói thầu xây lắp {i}",
                "investorName": f"Ban QLDA {page_no % 300}",
            }
            for i in range(records_per_page)
        ]
        yield json.dumps({"page": page_no, "content": content}, ensure_ascii=False).encode()
        for record in content:
            yield json.dumps(record, ensure_ascii=False).encode()


def cluster_bytes(path: Path) -> int:
    if sys.platform == "win32":
        import ctypes
        import ctypes.wintypes as wt

        per_cluster = wt.DWORD()
        per_sector = wt.DWORD()
        free_clusters = wt.DWORD()
        total_clusters = wt.DWORD()
        root = str(Path(path).drive or "C:") + "\\"
        ok = ctypes.windll.kernel32.GetDiskFreeSpaceW(
            root, ctypes.byref(per_cluster), ctypes.byref(per_sector),
            ctypes.byref(free_clusters), ctypes.byref(total_clusters),
        )
        if ok and per_cluster.value and per_sector.value:
            return int(per_cluster.value) * int(per_sector.value)
    return int(path.stat().st_blksize or 4096)


def footprint(path: Path, cluster: int) -> tuple[int, int]:
    """(logical bytes, allocated bytes). NTFS reserves a full cluster per file."""
    logical = 0
    allocated = 0
    for item in path.rglob("*"):
        if item.is_file():
            size = item.stat().st_size
            logical += size
            allocated += max(cluster, ((size + cluster - 1) // cluster) * cluster)
    return logical, allocated


def measure(layout: str, root: Path, pages: int, records_per_page: int) -> dict:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    store = RawObjectStore(root, layout=layout)
    refs: list[str] = []
    started = time.perf_counter()
    try:
        for payload in payloads(pages, records_per_page):
            ref = store.put(payload)
            if not ref.already_existed:
                refs.append(ref.sha256)
        write_seconds = time.perf_counter() - started
        started = time.perf_counter()
        for sha in refs:
            store.read(sha)
        read_seconds = time.perf_counter() - started
        started = time.perf_counter()
        failures = sum(1 for sha in refs if store.verify(sha)[0] is False)
        verify_seconds = time.perf_counter() - started
    finally:
        store.close()
    objects = len(refs)
    files = sum(1 for item in root.rglob("*") if item.is_file())
    logical = sum(len(p) for p in payloads(pages, records_per_page))
    stored_logical, stored_allocated = footprint(root, cluster_bytes(root))
    return {
        "layout": layout,
        "objects": objects,
        "files_on_disk": files,
        "write_seconds": round(write_seconds, 3),
        "read_seconds": round(read_seconds, 3),
        "verify_seconds": round(verify_seconds, 3),
        "ms_per_object_read": round(read_seconds * 1000 / max(objects, 1), 4),
        "logical_payload_bytes": logical,
        "stored_logical_bytes": stored_logical,
        "stored_allocated_bytes": stored_allocated,
        "allocation_over_logical": round(stored_allocated / max(logical, 1), 3),
        "verify_failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=2000)
    parser.add_argument("--records-per-page", type=int, default=10)
    parser.add_argument("--workdir", type=Path, default=Path("_evidence/rc/prod-bench-work"))
    parser.add_argument("--out", type=Path, default=Path("_evidence/rc/prod-bench.json"))
    args = parser.parse_args(argv)

    report: dict[str, object] = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pages": args.pages,
        "records_per_page": args.records_per_page,
        "passes": [],
    }
    args.workdir.mkdir(parents=True, exist_ok=True)
    for layout in (LAYOUT_OBJECTS, LAYOUT_SEGMENTS):
        root = args.workdir / layout
        print(f"measuring {layout} ...", flush=True)
        entry = measure(layout, root, args.pages, args.records_per_page)
        print(json.dumps(entry, indent=2), flush=True)
        report["passes"].append(entry)

    by_layout = {entry["layout"]: entry for entry in report["passes"]}
    roomy = by_layout[LAYOUT_OBJECTS]
    snug = by_layout[LAYOUT_SEGMENTS]
    report["comparison"] = {
        "read_speedup_segments": round(roomy["read_seconds"] / max(snug["read_seconds"], 1e-9), 2),
        "write_speedup_segments": round(roomy["write_seconds"] / max(snug["write_seconds"], 1e-9), 2),
        "allocated_footprint_ratio_segments": round(
            snug["stored_allocated_bytes"] / max(roomy["stored_allocated_bytes"], 1), 3
        ),
        "logical_footprint_ratio_segments": round(
            snug["stored_logical_bytes"] / max(roomy["stored_logical_bytes"], 1), 3
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["comparison"], indent=2), flush=True)
    print(f"wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
