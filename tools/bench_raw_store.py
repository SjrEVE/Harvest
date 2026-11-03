"""Raw storage benchmark (§5.1) — object-per-page+record versus byte-offset segments.

The spec mandates the measurement before the layout is chosen, on synthetic pages only.
Nothing here touches real data, and nothing here is used by the product: the product ships
layout A, and this tool exists to show whether that default still wins once the numbers are in.

Measured per layout:
* write wall time, with and without per-file fsync (durability is the expensive part);
* number of filesystem objects created;
* logical bytes versus *allocated* bytes (NTFS reserves a cluster per file, which is where a
  store of 200k small files pays);
* sequential read of every object;
* full sha256 verification pass over every object.

Run:  uv run python tools/bench_raw_store.py --pages 100000 --out _evidence/g1/bench-raw.json
"""

from __future__ import annotations

import argparse
import ctypes
import gzip
import hashlib
import json
import os
import random
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

CHUNK = 1 << 16
SEGMENT_BYTES = 64 * 1024 * 1024  # the size a segment rolls at, per §5.1's alternative


@dataclass
class Stats:
    layout: str
    pages: int
    records_per_page: int
    objects_written: int = 0
    write_seconds: float = 0.0
    durable: bool = True
    files: int = 0
    logical_bytes: int = 0
    allocated_bytes: int = 0
    read_seconds: float = 0.0
    verify_seconds: float = 0.0
    verify_failures: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        read = self.read_seconds or 1e-9
        return {
            "layout": self.layout,
            "pages": self.pages,
            "records_per_page": self.records_per_page,
            "files_on_disk": self.files,
            "objects_written": self.objects_written,
            "durable_write": self.durable,
            "write_seconds": round(self.write_seconds, 3),
            "objects_per_second": round(self.objects_written / max(self.write_seconds, 1e-9), 1),
            "mean_logical_bytes_per_object": round(self.logical_bytes / max(self.objects_written, 1), 1),
            "logical_bytes": self.logical_bytes,
            "allocated_bytes": self.allocated_bytes,
            "allocation_overhead_pct": round(100 * (self.allocated_bytes / max(self.logical_bytes, 1) - 1), 1),
            "sequential_read_mb_per_s": round(self.logical_bytes / read / 1e6, 1),
            "read_seconds_scaled": round(self.read_seconds, 3),
            "verify_seconds_scaled": round(self.verify_seconds, 3),
            "verify_failures": self.verify_failures,
            "notes": self.notes,
        }


def cluster_size(path: Path) -> int:
    """Filesystem allocation unit: NTFS reserves this much per file, whatever its size."""
    if sys.platform == "win32":
        import ctypes.wintypes as wt

        sectors_per_cluster = wt.DWORD()
        bytes_per_sector = wt.DWORD()
        free_clusters = wt.DWORD()
        total_clusters = wt.DWORD()
        root = str(Path(path).drive or "C:") + "\\"
        ok = ctypes.windll.kernel32.GetDiskFreeSpaceW(
            root,
            ctypes.byref(sectors_per_cluster),
            ctypes.byref(bytes_per_sector),
            ctypes.byref(free_clusters),
            ctypes.byref(total_clusters),
        )
        if ok and sectors_per_cluster.value and bytes_per_sector.value:
            return int(sectors_per_cluster.value) * int(bytes_per_sector.value)
        return 4096
    return int(Path(path).stat().st_blksize or 4096)


def allocated_bytes(path: Path, cluster: int) -> int:
    size = path.stat().st_size
    return max(cluster, ((size + cluster - 1) // cluster) * cluster)


def make_pages(pages: int, records_per_page: int, seed: int = 20261103) -> list[tuple[bytes, list[bytes]]]:
    """Synthetic pages shaped like the real payload: shared keys, varied Vietnamese text.

    Compressibility and object size are the whole game for layout B, so the generator keeps the
    key names repeating and the values diverse, and each record lands around 1-3 KB - the order
    of magnitude a TBMT notify row has. A 200-byte record would flatter one-file-per-object on
    cluster overhead and on nothing else.
    """
    rng = random.Random(seed)
    verbs = ("xây lắp", "thiết bị y tế", "tư vấn giám sát", "sửa chữa đường", "mua sắm tập trung")
    out: list[tuple[bytes, list[bytes]]] = []
    for page_no in range(pages):
        records = [
            {
                "notifyNo": f"IB{rng.randrange(10 ** 6, 10 ** 7)}",
                "notifyVersion": "00",
                "title": f"Gói thầu {rng.choice(verbs)} {page_no}",
                "investorName": f"Ban QLDA {rng.randrange(1, 400)}",
                "winBidPackage": {"packagePrice": rng.randrange(10 ** 6, 10 ** 10), "currency": "VND"},
                "description": " ".join(rng.choice(verbs) for _ in range(24)),
                "serviceList": [
                    {"name": f"Hạng mục {i}", "detail": rng.choice(verbs), "unit": "cái", "qty": rng.randrange(1, 999)}
                    for i in range(9)
                ],
                "contractDetail": {
                    "planNo": f"KH{rng.randrange(10 ** 5, 10 ** 6)}",
                    "decisionNo": f"QĐ{rng.randrange(10 ** 4, 10 ** 5)}",
                    "source": "Ngân sách tỉnh",
                    "createdAt": f"2026-{rng.randrange(1, 13):02d}-{rng.randrange(1, 28):02d}T09:00:00Z",
                },
            }
            for _ in range(records_per_page)
        ]
        page = {"code": 200, "page": {"number": page_no, "size": records_per_page, "content": records}}
        out.append(
            (
                json.dumps(page, ensure_ascii=False).encode("utf-8"),
                [json.dumps(record, ensure_ascii=False).encode("utf-8") for record in records],
            )
        )
    return out


def compress(payload: bytes) -> bytes:
    return gzip.compress(payload, 6, mtime=0)


def _fsync_file(path: Path, data: bytes, *, fsync: bool) -> None:
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        if fsync:
            os.fsync(handle.fileno())


# ------------------------------------------------------------------ layout A


def run_object_layout(
    root: Path,
    pages: list[tuple[bytes, list[bytes]]],
    cluster: int,
    *,
    fsync: bool = True,
    read_sample: float = 1.0,
) -> Stats:
    """One file per object, sharded by hash prefix: the shipped design (§5.1)."""
    stats = Stats(layout="object-per-page+record", pages=len(pages), records_per_page=len(pages[0][1]))
    base = root / "objects"
    index: dict[str, Path] = {}

    def write_one(payload: bytes) -> None:
        blob = compress(payload)
        sha = hashlib.sha256(payload).hexdigest()
        if sha in index:
            return  # content addressing: duplicate bytes are not written twice
        target = base / sha[:2] / f"{sha}.bin.gz"
        target.parent.mkdir(parents=True, exist_ok=True)
        _fsync_file(target, blob, fsync=fsync)
        index[sha] = target
        stats.logical_bytes += len(blob)

    start = time.perf_counter()
    for page, records in pages:
        write_one(page)
        for record in records:
            write_one(record)
    stats.write_seconds = time.perf_counter() - start
    stats.durable = fsync

    stats.files = len(index)
    stats.objects_written = len(index)
    stats.allocated_bytes = sum(allocated_bytes(p, cluster) for p in index.values())

    sampled = _sample(list(index.values()), read_sample)
    start = time.perf_counter()
    read_bytes = 0
    for path in sampled:
        with gzip.open(path, "rb") as handle:
            read_bytes += len(handle.read())
    stats.read_seconds = _scale(time.perf_counter() - start, len(sampled), len(index))
    stats.notes.append(
        f"sequential read over {len(sampled)} of {len(index)} files, {read_bytes} bytes decompressed,"
        " time scaled to the full set"
    )

    verified = _sample([payload for payload, _records in pages], read_sample)
    start = time.perf_counter()
    for payload in verified:
        sha = hashlib.sha256(payload).hexdigest()
        try:
            with gzip.open(index[sha], "rb") as handle:
                if hashlib.sha256(handle.read()).hexdigest() != sha:
                    stats.verify_failures += 1
        except (OSError, EOFError, gzip.BadGzipFile, KeyError):
            stats.verify_failures += 1
    stats.verify_seconds = _scale(time.perf_counter() - start, len(verified), len(pages))
    return stats


def _scale(seconds: float, measured: int, total: int) -> float:
    if measured == 0 or measured == total:
        return seconds
    return seconds * total / measured


def _sample(sequence: list, fraction: float) -> list:
    """Deterministic prefix slice. A prefix of a hash-ordered store is not a biased sample:
    the layout under test does not order by anything the query cares about."""
    if fraction >= 1.0:
        return sequence
    return sequence[: max(1, int(len(sequence) * fraction))]


# ------------------------------------------------------------------ layout B


def run_segment_layout(
    root: Path,
    pages: list[tuple[bytes, list[bytes]]],
    cluster: int,
    *,
    fsync: bool = True,
    read_sample: float = 1.0,
) -> Stats:
    """Appends into fixed-size segment files, addressed by (segment, offset, length)."""
    stats = Stats(layout="byte-offset segments", pages=len(pages), records_per_page=len(pages[0][1]))
    base = root / "segments"
    base.mkdir(parents=True, exist_ok=True)
    index: dict[str, tuple[Path, int, int]] = {}

    class Writer:
        def __init__(self, name: str) -> None:
            self.name = name
            self.number = 0
            self.handle_path = base / f"{name}-{self.number:05d}.seg"
            self.handle = self.handle_path.open("wb")
            self.size = 0

        def append(self, blob: bytes) -> tuple[Path, int, int]:
            offset = self.size
            self.handle.write(blob)
            self.size += len(blob)
            if self.size >= SEGMENT_BYTES:
                self.handle.close()
                self.number += 1
                self.handle_path = base / f"{self.name}-{self.number:05d}.seg"
                self.handle = self.handle_path.open("wb")
                self.size = 0
            return self.handle_path, offset, len(blob)

    pages_writer = Writer("page")
    records_writer = Writer("record")

    def write_one(payload: bytes, writer: Writer) -> None:
        blob = compress(payload)
        sha = hashlib.sha256(payload).hexdigest()
        if sha in index:
            return
        index[sha] = writer.append(blob)
        stats.logical_bytes += len(blob)

    start = time.perf_counter()
    for page, records in pages:
        write_one(page, pages_writer)
        for record in records:
            write_one(record, records_writer)
        if fsync:  # segments flush once per page, not per object - that is their argument
            for writer in (pages_writer, records_writer):
                writer.handle.flush()
                os.fsync(writer.handle.fileno())
    for writer in (pages_writer, records_writer):
        writer.handle.flush()
        os.fsync(writer.handle.fileno())
        writer.handle.close()
    stats.write_seconds = time.perf_counter() - start
    stats.durable = fsync

    seg_files = sorted(base.glob("*.seg"))
    stats.files = len(seg_files)
    stats.objects_written = len(index)
    stats.allocated_bytes = sum(allocated_bytes(p, cluster) for p in seg_files)
    stats.notes.append(f"{len(index)} objects packed into {len(seg_files)} files")

    per_file: dict[Path, list[tuple[int, int, str]]] = {}
    for sha, (path, offset, length) in index.items():
        per_file.setdefault(path, []).append((offset, length, sha))
    spans_total = sum(len(spans) for spans in per_file.values())
    budget = max(1, int(spans_total * read_sample))
    start = time.perf_counter()
    read_bytes = 0
    measured = 0
    for path, spans in per_file.items():
        spans.sort()
        take = spans[:budget]
        budget -= len(take)
        measured += len(take)
        with path.open("rb") as handle:
            for offset, length, _sha in take:
                handle.seek(offset)
                read_bytes += len(gzip.decompress(handle.read(length)))
        if budget <= 0:
            break
    stats.read_seconds = _scale(time.perf_counter() - start, measured, spans_total)
    stats.notes.append(
        f"seek read over {measured} of {spans_total} spans, {read_bytes} bytes decompressed,"
        " time scaled to the full set"
    )

    verified = _sample([payload for payload, _records in pages], read_sample)
    start = time.perf_counter()
    for page in verified:
        sha = hashlib.sha256(page).hexdigest()
        path, offset, length = index[sha]
        with path.open("rb") as handle:
            handle.seek(offset)
            blob = handle.read(length)
        try:
            if hashlib.sha256(gzip.decompress(blob)).hexdigest() != sha:
                stats.verify_failures += 1
        except (OSError, EOFError, gzip.BadGzipFile, KeyError):
            stats.verify_failures += 1
    stats.verify_seconds = _scale(time.perf_counter() - start, len(verified), len(pages))
    return stats


# ------------------------------------------------------------------ harness


def measure_free_space_drop(run, root: Path) -> tuple[object, int]:
    before = shutil.disk_usage(root.parent).used if root.parent.exists() else 0
    result = run(root)
    after = shutil.disk_usage(root.parent).used
    result.notes.append(f"disk-used delta {max(after - before, 0)} bytes (noisy on a shared volume)")
    return result, abs(after - before)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=100_000)
    parser.add_argument("--records-per-page", type=int, default=10)
    parser.add_argument("--workdir", type=Path, default=Path("_evidence/g1/bench-work"))
    parser.add_argument("--out", type=Path, default=Path("_evidence/g1/bench-raw.json"))
    parser.add_argument("--db-check", action="store_true", help="also report SQLite index build cost")
    parser.add_argument(
        "--read-sample",
        type=float,
        default=1.0,
        help="fraction of objects to read and verify; the measured time is scaled back to the full set",
    )
    parser.add_argument("--keep", action="store_true", help="leave the synthetic stores on disk")
    args = parser.parse_args(argv)

    args.workdir.mkdir(parents=True, exist_ok=True)
    cluster = cluster_size(args.workdir)
    print(f"generating {args.pages} synthetic pages x {args.records_per_page} records (cluster={cluster})", flush=True)
    pages = make_pages(args.pages, args.records_per_page)

    report: dict[str, object] = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cluster_size_bytes": cluster,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "pages": args.pages,
        "records_per_page": args.records_per_page,
        "passes": [],
    }
    for name, run in (("objects", run_object_layout), ("segments", run_segment_layout)):
        for durable in (False, True):
            tag = f"{name}-{'fsync' if durable else 'buffered'}"
            root = args.workdir / tag
            if root.exists():
                shutil.rmtree(root)
            root.mkdir(parents=True)
            print(f"running {tag} ...", flush=True)
            stats, delta = measure_free_space_drop(
                lambda r, run=run, durable=durable: run(
                    r, pages, cluster, fsync=durable, read_sample=args.read_sample
                ),
                root,
            )
            entry = stats.as_dict()
            entry["disk_used_delta_bytes"] = delta
            entry["pass"] = tag
            report["passes"].append(entry)
            print(json.dumps(entry, indent=2), flush=True)
            shutil.rmtree(root, ignore_errors=True)  # the numbers are the evidence, not the bytes

    report["layout_comparison"] = {}
    by_name = {entry["layout"]: entry for entry in report["passes"] if entry["durable_write"]}
    for layout, entry in by_name.items():
        buffered = next(e for e in report["passes"] if e["layout"] == layout and not e["durable_write"])
        objects = max(entry["objects_written"], 1)
        report["layout_comparison"][layout] = {
            "fsync_cost_multiplier": round(entry["write_seconds"] / max(buffered["write_seconds"], 1e-9), 2),
            "allocated_over_logical_ratio": round(entry["allocated_bytes"] / max(entry["logical_bytes"], 1), 3),
            "write_ms_per_object": round(entry["write_seconds"] * 1000 / objects, 3),
            "read_ms_per_object": round(entry["read_seconds_scaled"] * 1000 / objects, 3),
            "verify_ms_per_object": round(entry["verify_seconds_scaled"] * 1000 / objects, 3),
        }

    if args.db_check:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE idx (sha TEXT PRIMARY KEY, path TEXT, off INTEGER, len INTEGER)")
        start = time.perf_counter()
        conn.executemany(
            "INSERT OR IGNORE INTO idx VALUES (?,?,?,?)",
            [(hashlib.sha256(p).hexdigest(), "s", i, 1000) for i, (p, _r) in enumerate(pages)],
        )
        conn.commit()
        report["sqlite_index_seconds"] = round(time.perf_counter() - start, 3)

    report["read_sample_fraction"] = args.read_sample
    report["synthetic_only"] = True
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {args.out}", flush=True)
    if not args.keep:
        shutil.rmtree(args.workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
