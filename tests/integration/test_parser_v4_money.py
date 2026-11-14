"""Parser v4: prices into money_amounts, medicine-row fallback, replay targets."""

import dataclasses
import json
from datetime import UTC, datetime

from santhau_studio.collectors.cgttrg import PARSER_VERSION, public_spec
from santhau_studio.collectors.deep import parse_detail
from santhau_studio.storage.warehouse import WarehouseWriter
from santhau_studio.workers.deep_replay import replay as replay_deep


def hsmt(rows, notify_no="IB-0"):
    return {
        "bidoInvBiddingDTO": [
            {
                "notifyNo": notify_no,
                "notifyId": "notice-id",
                "notifyVersion": "00",
                "bidForm": "CGTTRG",
                "formCode": "BD.DT.02.1960",
                "formValue": json.dumps({"Table": rows}),
            }
        ]
    }


def reoffer(notify_no="IB-0"):
    return {
        "bidForm": "CGTTRG",
        "notifyNo": notify_no,
        "notifyVersion": "00",
        "bidDetail": {
            "bidPrice": 5000000,
            "bidPriceUnit": "VND",
            "bidPriceDetail": json.dumps([{"invest": 5000000, "moneyType": "VND"}]),
        },
        "bidoListContractorReofferPassedDTOList": [
            {
                "contractorCode": "org-1",
                "lotNo": "LOT-1",
                "bidPrice": 5000000,
                "bidFinalPrice": None,
                "priceInit": 1000000,
                "priceStep": 10000,
            }
        ],
        "contractorsResult": [
            {"contractorCode": "org-1", "contractorName": "A", "reofferPrice": 4000000,
             "reofferPriceFinal": 4000000},
            {"contractorCode": "org-2", "contractorName": "B", "reofferPrice": 4500000,
             "reofferPriceFinal": 4500000},
        ],
        "priceInit": 5000000,
        "priceStep": 50000,
    }


def money_by_role(parsed):
    return {m.role: m for m in parsed.money}


# ---------------------------------------------------------------- parser: REOFFER

def test_reoffer_prices_per_contractor_and_package_totals():
    parsed = parse_detail(public_spec("TBMT", "REOFFER"), reoffer())
    by_role = money_by_role(parsed)
    offer_rows = [m for m in parsed.money if m.owner_kind == "OFFER"]
    # Two contractors x two roles = 4 offer rows, one row per contractor per role.
    assert len(offer_rows) == 4
    assert len({m.owner_ref for m in offer_rows}) == 2
    by_member = {
        (m.owner_ref, m.role): m for m in offer_rows
    }
    org1, org2 = parsed.members[0].member_object_sha, parsed.members[1].member_object_sha
    assert by_member[(org1, "REOFFER_PRICE")].value == 4000000
    assert by_member[(org1, "REOFFER_PRICE_FINAL")].value == 4000000
    assert by_member[(org2, "REOFFER_PRICE")].value == 4500000
    assert by_member[(org2, "REOFFER_PRICE_FINAL")].value == 4500000
    assert all(
        m.currency_code == "VND" and m.tax_basis == "UNKNOWN" for m in offer_rows
    )
    assert by_role["PACKAGE_PRICE"].value == 5000000
    assert by_role["BID_PRICE"].value == 5000000
    assert by_role["PACKAGE_PRICE_INIT"].value == 5000000
    assert by_role["PACKAGE_PRICE_STEP"].value == 50000
    assert "BID_FINAL_PRICE" not in by_role  # always null in live captures: no row
    assert not parsed.problems


def test_reoffer_price_varying_by_lot_is_flagged_not_collapsed():
    value = reoffer()
    value["contractorsResult"].append(
        {"contractorCode": "org-1", "contractorName": "A", "lotNo": "LOT-2",
         "reofferPrice": 3000000, "reofferPriceFinal": 3000000}
    )
    parsed = parse_detail(public_spec("TBMT", "REOFFER"), value)
    offer_rows = [m for m in parsed.money if m.owner_kind == "OFFER"]
    # org-2 still has one unambiguous price per role; org-1 has none (flagged).
    assert len(offer_rows) == 2
    assert all(m.owner_ref != parsed.members[0].member_object_sha for m in offer_rows)
    assert any("OFFEROR_org-1_REOFFER_PRICE_VARIES_BY_LOT" in w for w in parsed.warnings)
    assert any("OFFEROR_org-1_REOFFER_PRICE_FINAL_VARIES_BY_LOT" in w for w in parsed.warnings)
    assert not parsed.problems  # a warning, not a capture failure


def test_detail_lane_parses_the_same_payload_but_writes_no_money():
    parsed = parse_detail(public_spec("TBMT", "DETAIL"), reoffer())
    assert parsed.money == ()
    assert len(parsed.members) == 2


def test_bid_detail_currency_falls_back_to_money_type_string():
    value = reoffer()
    value["bidDetail"] = {
        "bidPrice": 7000000,
        "bidPriceDetail": json.dumps([{"invest": 7000000, "moneyType": "VND"}]),
    }
    parsed = parse_detail(public_spec("TBMT", "REOFFER"), value)
    assert money_by_role(parsed)["PACKAGE_PRICE"].currency_code == "VND"


def test_bid_detail_price_without_any_currency_is_unparsable_not_guessed():
    value = reoffer()
    value["bidDetail"] = {"bidPrice": 7000000}
    parsed = parse_detail(public_spec("TBMT", "REOFFER"), value)
    row = money_by_role(parsed)["PACKAGE_PRICE"]
    assert row.presence_state == "UNPARSABLE" and row.value is None
    assert row.value_raw == "7000000" and row.currency == "MISSING"


# ---------------------------------------------------------------- parser: HSMT

def test_hsmt_lot_prices_and_medicine_row_fallback():
    value = hsmt(
        [
            {"id": 1, "lotNo": "LOT-1", "lotName": "Lot 1", "lotPrice": 1000000,
             "lotEstimatePrice": 900000, "pricePlan": 950000},
            {"id": 2, "parent": 1, "tenThuoc": "Paracetamol 500mg", "quantity": 100,
             "uom": "viên", "pricePlan": 500, "medicineCode": "MED-001",
             "nongDo": "500mg", "duongDung": "Uống", "dangBaoChe": "Viên nén"},
            {"id": 3, "parent": 1, "name": "Cable", "qty": 2, "uom": "m"},
        ]
    )
    parsed = parse_detail(public_spec("TBMT", "HSMT"), value)
    assert not parsed.problems
    assert parsed.lots[0].goods[0].name == "Paracetamol 500mg"
    assert parsed.lots[0].goods[0].quantity == 100
    assert parsed.lots[0].goods[0].quantity_presence == "KNOWN"
    assert parsed.lots[0].goods[0].raw["medicineCode"] == "MED-001"
    by_role = money_by_role(parsed)
    assert by_role["LOT_PRICE"].value == 1000000
    assert by_role["LOT_PRICE"].owner_kind == "LOT" and by_role["LOT_PRICE"].owner_ref == "LOT-1"
    assert by_role["LOT_ESTIMATE_PRICE"].value == 900000
    assert by_role["LOT_PLAN_PRICE"].value == 950000
    assert by_role["UNIT_PRICE"].value == 500
    assert by_role["UNIT_PRICE"].owner_kind == "GOODS_ROW"
    assert all(m.currency_code == "VND" and m.tax_basis == "UNKNOWN" for m in parsed.money)


def test_thousand_separator_quantity_stays_unparsable():
    # Locks the standing rule: "1.000" is ambiguous (1000 in vi-VN, 1.0 in en-US)
    # and is never guessed. This is why the 4 live PARSE_ERROR packages (all
    # thousand-separator qty strings, not medicine rows) stay PARSE_ERROR under
    # v4; repairing them needs a locale-aware qty decision, not the fallback.
    parsed = parse_detail(
        public_spec("TBMT", "HSMT"),
        hsmt([{"name": "Cable", "qty": "1.000", "uom": "m"}]),
    )
    assert parsed.unallotted_goods[0].quantity is None
    assert parsed.unallotted_goods[0].quantity_raw == "1.000"
    assert parsed.problems


# ---------------------------------------------------------------- writer + migration

def test_money_amounts_schema_accepts_all_v4_roles(tmp_path):
    writer = WarehouseWriter.open(tmp_path, create_schema=True, role="money-roles")
    try:
        assert writer.conn.execute(
            "SELECT MAX(version) FROM software_migrations"
        ).fetchone()[0] == 9
        roles = [
            "PACKAGE_VALUE", "BID_PRICE", "WIN_PRICE", "UNIT_PRICE", "CONTRACT_VALUE",
            "BUDGET", "ADJUSTMENT", "REOFFER_PRICE", "REOFFER_PRICE_FINAL",
            "BID_FINAL_PRICE", "PACKAGE_PRICE", "PACKAGE_PRICE_INIT",
            "PACKAGE_PRICE_STEP", "LOT_PRICE", "LOT_ESTIMATE_PRICE", "LOT_PLAN_PRICE",
            "ITEM_WINNING_PRICE", "ITEM_BID_PRICE",
        ]
        for role in roles:
            writer.conn.execute(
                "INSERT INTO money_amounts (owner_kind, owner_id, role, presence_state,"
                " value, currency, currency_code, tax_basis, provenance_json, observed_at_utc)"
                " VALUES ('PACKAGE', 1, ?, 'KNOWN', 1000, 'KNOWN', 'VND', 'UNKNOWN', '{}',"
                " '2026-11-08T00:00:00Z')",
                (role,),
            )
        assert writer.conn.execute("SELECT COUNT(*) FROM money_amounts").fetchone()[0] == len(roles)
    finally:
        writer.close()


def test_capture_writes_money_and_recapture_is_idempotent(tmp_path):
    from test_deep_run import seed_records

    from santhau_studio.storage.deep import record_deep_capture

    writer = seed_records(tmp_path)
    try:
        rid = writer.conn.execute("SELECT id FROM source_records ORDER BY id LIMIT 1").fetchone()[0]
        hsmt_payload = hsmt(
            [
                {"id": 1, "lotNo": "LOT-1", "lotName": "Lot 1", "lotPrice": 1000000,
                 "lotEstimatePrice": 900000, "pricePlan": 950000},
                {"id": 2, "parent": 1, "tenThuoc": "Paracetamol", "quantity": 100,
                 "uom": "viên", "pricePlan": 500},
                {"id": 3, "parent": 1, "name": "Cable", "qty": 2, "uom": "m"},
            ]
        )

        def capture(value, lane):
            return record_deep_capture(
                writer, run_id="run-deep", source_record_id=rid,
                spec=public_spec("TBMT", lane), completeness="FULL",
                response_payload=value, detail=value, now=datetime.now(UTC),
            )

        capture(hsmt_payload, "HSMT")
        capture(reoffer(), "REOFFER")
        rows = writer.conn.execute(
            "SELECT owner_kind, role, value, currency_code, tax_basis,"
            " json_extract(provenance_json,'$.lane') lane FROM money_amounts ORDER BY role"
        ).fetchall()
        assert len(rows) == 12  # HSMT 4 + REOFFER 8
        by_role = {}
        for r in rows:
            by_role.setdefault(r["role"], []).append(r)
        assert {r["value"] for r in by_role["REOFFER_PRICE"]} == {4000000, 4500000}
        assert {r["value"] for r in by_role["REOFFER_PRICE_FINAL"]} == {4000000, 4500000}
        assert by_role["LOT_PRICE"][0]["value"] == 1000000
        assert by_role["UNIT_PRICE"][0]["value"] == 500
        assert by_role["PACKAGE_PRICE"][0]["value"] == 5000000
        assert all(r["currency_code"] == "VND" and r["tax_basis"] == "UNKNOWN" for r in rows)
        assert by_role["LOT_PRICE"][0]["lane"] == "HSMT"
        assert by_role["REOFFER_PRICE"][0]["lane"] == "REOFFER"
        # The medicine fields ride in the goods provenance, not a new column.
        prov = writer.conn.execute(
            "SELECT provenance_json FROM goods_rows WHERE name='Paracetamol'"
        ).fetchone()[0]
        assert json.loads(prov)["raw_row"]["tenThuoc"] == "Paracetamol"
        # Re-capturing the same payloads re-cuts the same rows, never duplicates.
        capture(hsmt_payload, "HSMT")
        capture(reoffer(), "REOFFER")
        assert writer.conn.execute("SELECT COUNT(*) FROM money_amounts").fetchone()[0] == 12
    finally:
        writer.close()


# ---------------------------------------------------------------- replay

def _v3_spec(family, lane):
    return dataclasses.replace(public_spec(family, lane), parser_version=3)


def test_replay_covers_all_old_parser_captures_and_fills_money(tmp_path):
    from test_deep_run import seed_records

    from santhau_studio.workers.deep_run import run_deep_batch

    writer = seed_records(tmp_path)
    try:
        rid = writer.conn.execute("SELECT id FROM source_records ORDER BY id LIMIT 1").fetchone()[0]
        hsmt_payload = hsmt(
            [
                {"id": 1, "lotNo": "LOT-1", "lotName": "Lot 1", "lotPrice": 1000000,
                 "lotEstimatePrice": 900000, "pricePlan": 950000},
                {"id": 2, "parent": 1, "name": "Cable", "qty": 2, "uom": "m"},
            ]
        )

        def collect(lane, payload):
            return run_deep_batch(
                writer, run_id="deep-v3", family="TBMT", lane=lane,
                scope={"record_ids": [rid]}, fetcher=lambda _f, _k: payload,
                spec_provider=_v3_spec,
            )

        assert collect("HSMT", hsmt_payload).status == "COMPLETED"
        assert collect("REOFFER", reoffer()).status == "COMPLETED"
        assert collect("DETAIL", reoffer()).status == "COMPLETED"
        # The v3 spec labels the captures parser_version=3, but the parse code
        # that runs is always the current one, so v5 already wrote the money.
        # Reproduce v3's observable store state (no money rows) by removing
        # them: that is exactly what a store captured by the real v3 holds.
        writer.conn.execute("DELETE FROM money_amounts")
        writer.conn.commit()
        assert writer.conn.execute("SELECT COUNT(*) FROM money_amounts").fetchone()[0] == 0
        assert writer.conn.execute(
            "SELECT COUNT(*) FROM deep_capture_parse_metadata WHERE parser_version=3"
        ).fetchone()[0] == 3
    finally:
        writer.close()

    preview = replay_deep(tmp_path, execute=False, limit=100)
    assert preview["eligible"] == 3 and preview["network_requests"] == 0
    assert sum(r["money"] for r in preview["records"]) == 11  # HSMT 3 + REOFFER 8 + DETAIL 0

    result = replay_deep(tmp_path, execute=True, limit=100)
    assert result["eligible"] == 3 and result["applied"] == 3 and result["repaired"] == 3
    from santhau_studio.storage.warehouse import connect_read_only

    with connect_read_only(tmp_path / "warehouse.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM money_amounts").fetchone()[0] == 11
        assert conn.execute(
            "SELECT COUNT(*) FROM deep_capture_parse_metadata WHERE parser_version=7"
        ).fetchone()[0] == 3
        assert conn.execute(
            "SELECT COUNT(DISTINCT state) FROM deep_tasks WHERE state='FETCHED'"
        ).fetchone()[0] == 1
    assert replay_deep(tmp_path, execute=False, limit=100)["eligible"] == 0


def test_parser_version_is_7():
    assert PARSER_VERSION == 7
