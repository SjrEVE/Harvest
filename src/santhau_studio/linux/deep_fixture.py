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
            {
                "contractorCode": f"SYNTH-ORG-{i}",
                "contractorName": f"Synthetic offeror {i}",
                "reofferPrice": 1000000 + i * 1000,
                "reofferPriceFinal": 1000000 + i * 1000,
            }
            for i in range(5)
        ]
        if index == 2
        else [],
        "contractorsResultAll": [{"contractorCode": f"SYNTH-ORG-{i % 5}"} for i in range(10)]
        if index == 2
        else [],
        # Parser v4 money: package totals repeat on every passed-list lot row, so
        # the package owns one row each; the bidDetail string carries moneyType.
        "bidDetail": {
            "bidPrice": 5000000,
            "bidPriceUnit": "VND",
            "bidPriceDetail": json.dumps([{"invest": 5000000, "moneyType": "VND"}]),
        }
        if index == 2
        else None,
        "bidoListContractorReofferPassedDTOList": [
            {
                "contractorCode": "SYNTH-ORG-0",
                "contractorName": "Synthetic offeror 0",
                "lotNo": "SYNTH-LOT-1",
                "lotName": "Lot 1",
                "bidPrice": 5000000,
                "bidFinalPrice": None,
                "priceInit": 1000000,
                "priceStep": 10000,
            }
        ]
        if index == 2
        else [],
        "priceInit": 5000000 if index == 2 else None,
        "priceStep": 50000 if index == 2 else None,
    }


def hsmt(index):
    source = notice(index)
    rows = []
    if index == 0:
        for lot, count in enumerate((8, 13, 7), 1):
            header = {
                "id": f"lot-{lot}",
                "lotNo": f"SYNTH-LOT-{lot}",
                "lotName": f"Lot {lot}",
                "lotPrice": 1000000 * lot,
                "lotEstimatePrice": 900000 * lot,
            }
            if lot == 1:
                header["pricePlan"] = 950000
            rows.append(header)
            for i in range(count):
                if lot == 1 and i == 0:
                    # Medicine row (form 1960): tenThuoc/quantity instead of
                    # name/qty, plus a planned unit price — parser v4 fallback.
                    rows.append(
                        {
                            "id": "item-1-0",
                            "parent": "lot-1",
                            "tenThuoc": "Paracetamol 500mg",
                            "quantity": 100,
                            "uom": "viên",
                            "pricePlan": 500,
                            "medicineCode": "MED-001",
                            "nongDo": "500mg",
                            "duongDung": "Uống",
                            "dangBaoChe": "Viên nén",
                            "specification": "Synthetic medicine row",
                        }
                    )
                    continue
                rows.append(
                    {
                        "id": f"item-{lot}-{i}",
                        "parent": f"lot-{lot}",
                        "name": f"Item {i}",
                        "qty": i + 1,
                        "uom": "box",
                        "extra": "preserved",
                    }
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
                    "money_amounts",
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
                "money_amounts",
            ),
            # money: HSMT 3 LOT_PRICE + 3 LOT_ESTIMATE_PRICE + 1 LOT_PLAN_PRICE
            # + 1 UNIT_PRICE (medicine row) = 8; REOFFER 5 contractors x
            # (REOFFER_PRICE + REOFFER_PRICE_FINAL) = 10 + PACKAGE_PRICE +
            # BID_PRICE + PACKAGE_PRICE_INIT + PACKAGE_PRICE_STEP = 14; DETAIL 0.
            (9, 9, 3, 67, 3, 5, 3, 22),
            strict=True,
        )
    )
    # Three records x three logical lanes (HSMT, DETAIL, REOFFER) still produce
    # nine captured lane tasks. DETAIL and REOFFER share one endpoint/body, so
    # only six physical responses are needed and the rerun adds none.
    expected_requests = 6
    if (
        first != expected
        or second != first
        or requests != expected_requests
        or OfflineDeepFetcher.calls != expected_requests
        or forbidden
        or list(root.glob("serving*"))
    ):
        raise RuntimeError(
            f"Deep fixture mismatch: {first}, {second}, "
            f"{requests} first / {OfflineDeepFetcher.calls} total requests, {forbidden}"
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
