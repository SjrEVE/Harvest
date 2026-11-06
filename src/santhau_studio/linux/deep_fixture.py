"""Explicit synthetic deep acceptance, isolated from every live source."""

import json
import sys
from contextlib import closing
from typing import ClassVar

from santhau_studio.collectors.extraction import extract_page
from santhau_studio.collectors.source_registry import binding_for
from santhau_studio.linux import fixture
from santhau_studio.storage.warehouse import WarehouseWriter, connect_read_only
from santhau_studio.workers.auto_deep import configure


def notice(index):
    return {
        "notifyNo": f"SYNTH-DEEP-{index}",
        "notifyId": f"synthetic-notify-{index}",
        "notifyVersion": "00",
        "bidForm": "CGTTRG",
        "processApply": "LDT",
        "bidName": "Synthetic deep acceptance",
        "isMultiLot": int(index == 0),
        "bidPlan": {
            "planNo": f"SYNTH-PLAN-{index}",
            "planVersion": "00",
            "decisionFileId": f"synthetic-file-{index}",
            "decisionFileName": "synthetic-decision.pdf",
        },
        "contractorsResult": [
            {"contractorCode": f"SYNTH-ORG-{i}", "contractorName": f"Synthetic offeror {i}"}
            for i in range(5)
        ]
        if index == 2
        else [],
        "contractorsResultAll": [{"contractorCode": f"SYNTH-ORG-{i % 5}"} for i in range(10)]
        if index == 2
        else [],
    }


def hsmt(index):
    source = notice(index)
    rows = []
    if index == 0:
        for lot, count in enumerate((8, 13, 7), 1):
            rows.append({"id": f"lot-{lot}", "lotNo": f"SYNTH-LOT-{lot}", "lotName": f"Lot {lot}"})
            rows.extend(
                {
                    "id": f"item-{lot}-{i}",
                    "parent": f"lot-{lot}",
                    "name": f"Item {i}",
                    "qty": i + 1,
                    "uom": "box",
                    "extra": "preserved",
                }
                for i in range(count)
            )
    else:
        rows = [
            {
                "id": i,
                "name": f"Item {i}",
                "qty": 1,
                "uom": "unit",
                "specification": "Synthetic specification",
            }
            for i in range(1 if index == 1 else 38)
        ]
    return {
        "bidoInvBiddingDTO": [
            {
                **source,
                "formCode": "BD.DT.02.1960" if index == 0 else "BD.DT.02.1957",
                "formContent": '[{"columnName":"extra"}]',
                "formValue": json.dumps({"Table": rows}),
            }
        ]
    }


class OfflineDeepFetcher:
    calls: ClassVar[int] = 0

    def __init__(self, writer, request, stop_requested):
        if not fixture.enabled(writer.store.root):
            raise ValueError("Deep fixture requires a marked isolated offline root")
        self.request, self.stop_requested = request, stop_requested
        self.receipt = {"evidence_class": fixture.PROFILE, "endpoint": "synthetic-offline"}

    def fetch_record(self, record):
        if self.stop_requested():
            raise ValueError("STOP_REQUESTED")
        if not record["natural_key"].startswith("SYNTH-DEEP-"):
            raise ValueError("Unknown synthetic deep target")
        type(self).calls += 1
        index = int(record["natural_key"].rsplit("-", 1)[1])
        return hsmt(index) if self.request.job_kind == "HSMT" else notice(index)


def initialize(root):
    fixture.initialize_fixture(root)
    with closing(WarehouseWriter.open(root, create_schema=True, role="deep-self-test")) as writer:
        writer.register_run("deep-seed", "deep-seed", "BACKFILL")
        notices = [notice(i) for i in range(3)]
        for family, rows in (("TBMT", notices), ("KHLCNT", [n["bidPlan"] for n in notices])):
            payload = {"page": {"content": rows, "totalElements": len(rows)}}
            ref = writer.store_response(payload)
            attempt = writer.ensure_attempt(
                run_id="deep-seed",
                family=family,
                endpoint="synthetic-offline",
                interval_from="2026-01-01T00:00:00Z",
                interval_to="2026-01-02T00:00:00Z",
                page_no=0,
                attempt_no=1,
                outcome="OK",
                response=ref,
            )
            parsed = extract_page(payload, binding_for(family).spec)
            writer.commit_page(
                run_id="deep-seed", attempt_id=attempt, response_ref=ref, records=parsed.records
            )
        writer.finish_run("deep-seed", "COMPLETED")
        configure(writer.conn, {"enabled": True, "batch_requests": 9})


def self_test(root):
    from santhau_studio.linux.cli import make_loop

    initialize(root)
    OfflineDeepFetcher.calls = 0

    def collect():
        loop = make_loop(root, offline=True, idle_exit=0.15)
        loop.run()
        if loop.exit_code:
            raise RuntimeError(loop.last_error)
        with closing(connect_read_only(root / "warehouse.sqlite")) as conn:
            counts = {
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
            }
            if (
                conn.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                or conn.execute("PRAGMA foreign_key_check").fetchall()
            ):
                raise RuntimeError("Deep fixture integrity failed")
            if (
                conn.execute("SELECT COUNT(*) FROM deep_tasks WHERE state='FETCHED'").fetchone()[0]
                != 9
            ):
                raise RuntimeError("Deep fixture did not fetch all nine lanes")
        return counts

    first = collect()
    requests = OfflineDeepFetcher.calls
    second = collect()
    forbidden = [
        name
        for name in sys.modules
        if name.startswith(
            (
                "PySide6",
                "santhau_studio.desktop",
                "santhau_studio.storage.serving",
                "santhau_studio.storage.read_model",
            )
        )
    ]
    expected = dict(
        zip(
            (
                "deep_captures",
                "deep_capture_history",
                "lots",
                "goods_rows",
                "documents",
                "contractor_memberships",
                "relation_edges",
            ),
            (9, 9, 3, 67, 3, 5, 3),
            strict=True,
        )
    )
    if (
        first != expected
        or second != first
        or requests != 9
        or OfflineDeepFetcher.calls != 9
        or forbidden
        or list(root.glob("serving*"))
    ):
        raise RuntimeError(
            f"Deep fixture mismatch: {first}, {second}, {OfflineDeepFetcher.calls}, {forbidden}"
        )
    return {
        "ok": True,
        "evidence_class": "SYNTHETIC_OFFLINE",
        "counts": first,
        "first_requests": requests,
        "rerun_requests": 0,
        "network_requests": 0,
        "serving_created": False,
        "forbidden_imports": forbidden,
    }
