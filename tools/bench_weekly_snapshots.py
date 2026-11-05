"""Offline before/after snapshot cadence measurement on disposable synthetic roots."""
import argparse
import json
import os
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "integration"))
from test_serving_snapshot import commit_page
from test_weekly_collection import fast_builder, queue, run

from santhau_studio.collectors.offline_acceptance import initialize
from santhau_studio.desktop.backend import StudioBackend
from santhau_studio.storage.warehouse import WarehouseWriter, connect_read_only
from santhau_studio.workers import jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="snapshot-fixture-", dir=args.output.parent) as directory:
        root = Path(directory).resolve()
        os.environ["SANTHAU_OFFLINE_ACCEPTANCE_ROOT"] = str(root)
        initialize(root)
        with closing(WarehouseWriter.open(root, raw_layout="segments")) as writer:
            writer.register_run("run-1", "seed", "BACKFILL")
            for page in range(20):
                commit_page(writer, [{"notifyNo": f"SYNTH-SEED-{page}-{i}", "notifyVersion": "00",
                    "bidName": "Ho so thu snapshot"} for i in range(100)], page_no=page)
            writer.finish_run("run-1", "COMPLETED")
        original = jobs.build_serving_db
        assert original(root / "warehouse.sqlite", root / "serving.sqlite").published
        samples = []
        def measured(warehouse, target):
            started = time.perf_counter()
            result = original(warehouse, target)
            samples.append({"seconds": time.perf_counter()-started,
                "published": result.published, "rows": result.snapshot_rows})
            assert result.published, result.reason
            return result
        jobs.build_serving_db = measured
        studio = StudioBackend(root)
        assert queue(studio)["ok"]
        started = time.perf_counter()
        loop = run(studio, fast_builder([]))
        elapsed = time.perf_counter()-started
        assert loop.last_error is None
        with closing(connect_read_only(root / "serving.sqlite")) as conn:
            checks = {"quick_check": conn.execute("PRAGMA quick_check").fetchone()[0],
                "records": conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0],
                "fts": conn.execute("SELECT COUNT(*) FROM search_index").fetchone()[0],
                "fields": conn.execute("SELECT COUNT(*) FROM record_fields").fetchone()[0]}
        report = {"fixture": "2000 seed records + 48 records / 12 weekly children, no network",
            "elapsed_seconds": elapsed, "snapshot_seconds": sum(s["seconds"] for s in samples),
            "builds": len(samples), "samples": samples, "checks": checks}
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k != "samples"}))


if __name__ == "__main__":
    main()
