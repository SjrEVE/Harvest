"""Explicit bounded acceptance on a NEW isolated root, never the user's active warehouse.

Saved source JSON stays outside Git. --live starts the ordinary service with no injected
fetcher, and queues exactly six bounded requests (three notices x two lanes).
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from santhau_studio.collectors.cgttrg import public_spec
from santhau_studio.collectors.extraction import extract_page
from santhau_studio.collectors.source_registry import binding_for
from santhau_studio.desktop.backend import StudioBackend
from santhau_studio.storage.serving import build_serving_db
from santhau_studio.storage.warehouse import WarehouseWriter, connect_read_only
from santhau_studio.workers.deep_run import run_deep_batch
from santhau_studio.workers.jobs import JobRequest, enqueue_job

PAIRS = [
    ("reoffer-probe-2.json", "hsmt-probe-1.json"),
    ("reoffer-probe-4.json", "hsmt-probe-3.json"),
    ("reoffer-probe-7.json", "hsmt-probe-6.json"),
]


def seed(root, evidence):
    writer = WarehouseWriter.open(root, create_schema=True)
    payloads = {}
    try:
        writer.register_run("evidence-seed", "evidence-seed", "BACKFILL")
        responses = [
            json.loads((evidence / detail).read_text(encoding="utf-8")) for detail, _ in PAIRS
        ]
        for family, rows in [
            ("TBMT", responses),
            ("KHLCNT", [r["bidPlan"] for r in responses if r.get("bidPlan")]),
        ]:
            envelope = {"page": {"content": rows, "totalElements": len(rows)}}
            ref = writer.store_response(envelope)
            attempt = writer.ensure_attempt(
                run_id="evidence-seed",
                family=family,
                endpoint="local-saved-source-replay",
                interval_from="2026-01-01T00:00:00Z",
                interval_to="2026-11-06T00:00:00Z",
                page_no=0,
                attempt_no=1,
                outcome="OK",
                response=ref,
            )
            report = extract_page(envelope, binding_for(family).spec)
            writer.commit_page(
                run_id="evidence-seed", attempt_id=attempt, response_ref=ref, records=report.records
            )
        for detail, hsmt in PAIRS:
            response = json.loads((evidence / detail).read_text(encoding="utf-8"))
            payloads[(response["notifyNo"], "REOFFER")] = response
            payloads[(response["notifyNo"], "HSMT")] = json.loads(
                (evidence / hsmt).read_text(encoding="utf-8")
            )
        records = [
            dict(r)
            for r in writer.conn.execute(
                "SELECT * FROM source_records WHERE family='TBMT' ORDER BY natural_key"
            )
        ]
        assert len(records) == 3
        return records, payloads
    finally:
        writer.close()


def report(root):
    conn = connect_read_only(root / "warehouse.sqlite")
    result = {
        "counts": {
            t: conn.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
            for t in (
                "deep_captures",
                "deep_capture_history",
                "lots",
                "goods_rows",
                "documents",
                "contractor_memberships",
                "relation_edges",
            )
        },
        "tasks": [dict(r) for r in conn.execute("SELECT * FROM deep_tasks")],
        "integrity": conn.execute("PRAGMA quick_check").fetchone()[0],
        "foreign_keys": [list(r) for r in conn.execute("PRAGMA foreign_key_check")],
    }
    ids = [r[0] for r in conn.execute("SELECT id FROM source_records WHERE family='TBMT'")]
    conn.close()
    backend = StudioBackend(root)
    result["app"] = {}
    for rid in ids:
        detail = backend.record_detail(rid)
        assert "error" not in detail, detail.get("error")
        result["app"][detail["record"]["natural_key"]] = {
            "lots": len(detail["lots"]),
            "goods": len(detail["goods_rows"]),
            "documents": len(detail["documents"]),
            "members": len(detail["contractor_memberships"]),
            "states": detail.get("deep_states"),
        }
    result["queue"] = backend.deep_queue()
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    root = args.root.resolve()
    if root.exists():
        raise ValueError("Use a new isolated root; refusing existing data")
    records, payloads = seed(root, args.evidence)
    if args.live:
        for index, record in enumerate(records):
            for lane in ("HSMT", "REOFFER"):
                enqueue_job(
                    root,
                    JobRequest(
                        job_id=f"public-acceptance-{index}-{lane}",
                        job_kind=lane,
                        requested_by="bounded-public-acceptance",
                        sources=(
                            {
                                "source_key": "egp_home",
                                "family": "TBMT",
                                "adapter_sha": "public-deep-v1",
                            },
                        ),
                        scope={"record_ids": [record["id"]]},
                        request_limits={
                            "max_requests": 1,
                            "max_attempts": 1,
                            "max_seconds": 90,
                            "pacing_seconds": 3,
                        },
                        created_at="2026-11-05T00:00:00Z",
                    ),
                )
        env = {k: v for k, v in os.environ.items() if k != "SANTHAU_OFFLINE_ACCEPTANCE_ROOT"}
        env["PYTHONPATH"] = str(Path("src").resolve())
        with (root / "worker-process.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "santhau_studio.workers.service",
                    "--data-root",
                    str(root),
                    "--idle-exit-seconds",
                    "2",
                ],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                code = process.wait(timeout=600)
            except subprocess.TimeoutExpired:
                # Do not kill a writer. Request normal shutdown over its scoped pipe.
                from santhau_studio.workers.ipc import PipeClient, worker_pipe_name

                client = PipeClient.connect(worker_pipe_name(data_root=root))
                try:
                    client.request({"op": "command", "command": "FORCE_TERMINATE"})
                finally:
                    client.close()
                process.wait(timeout=120)
                raise
        assert code == 0, (root / "worker-process.log").read_text(encoding="utf-8")
    else:
        writer = WarehouseWriter.open(root, create_schema=True)
        try:
            for record in records:
                for lane in ("HSMT", "REOFFER"):
                    result = run_deep_batch(
                        writer,
                        run_id="saved-evidence",
                        family="TBMT",
                        lane=lane,
                        scope={"record_ids": [record["id"]]},
                        fetcher=lambda _family, key, lane=lane: payloads[(key, lane)],
                        spec_provider=public_spec,
                    )
                    assert result.status == "COMPLETED", result
            before = writer.conn.execute("SELECT COUNT(*) FROM deep_capture_history").fetchone()[0]
            for record in records:
                for lane in ("HSMT", "REOFFER"):
                    result = run_deep_batch(
                        writer,
                        run_id="saved-rerun",
                        family="TBMT",
                        lane=lane,
                        scope={"record_ids": [record["id"]]},
                        fetcher=lambda _family, _key: (_ for _ in ()).throw(
                            AssertionError("Checkpoint should skip fetch")
                        ),
                        spec_provider=public_spec,
                    )
                    assert result.detail["skipped"] == 1
            assert (
                writer.conn.execute("SELECT COUNT(*) FROM deep_capture_history").fetchone()[0]
                == before
            )
        finally:
            writer.close()
        assert build_serving_db(root / "warehouse.sqlite", root / "serving.sqlite").published
    result = report(root)
    (root / "acceptance-report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "root": str(root),
                "counts": result["counts"],
                "app": {
                    k: {n: v for n, v in x.items() if n != "states"}
                    for k, x in result["app"].items()
                },
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
