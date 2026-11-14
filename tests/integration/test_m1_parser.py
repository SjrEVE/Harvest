"""M1 lane: per-item winning prices in KQLCNT (parser v6, migration 008).

Fixtures are SYNTHETIC, modeled on the live captures: the two 2026-11-13
canary records (IB2600073112 goodsList/Table encoding and IB2600363545
lotResultItems encoding) plus the flat goodsList encoding from the 2026-11-12
deploy canary (7 records, e.g. IB2600597038, IB2600599802: entries carry the
item inline with formValue null and priceUnit as the unit price).
No live payload is copied into the repo.
"""

import json
from pathlib import Path

import pytest

from santhau_studio.collectors.cgttrg import PARSER_VERSION, public_spec
from santhau_studio.collectors.deep import DetailShapeError, parse_detail
from santhau_studio.collectors.public_deep import identity_problem

RESULT_ID = "6465862b-3fac-4a69-99ab-7851613bc1c8"


def goodslist_payload(notify_no="IB-0", *, currency="VND"):
    return {
        "bideContractorInputResultDTO": {
            "id": RESULT_ID,
            "notifyNo": notify_no,
            "notifyVersion": "00",
            "resultVersion": "00",
            "bidPriceUnit": currency,
            "lotResultDTO": [
                {
                    "id": "lot-uuid-1",
                    "lotNo": "BP2600067419",
                    "lotName": "Holter recorder",
                    "lotPrice": 371800000,
                    "lotEstimatePrice": 380000000,
                    "winningCode": "vn0313698992",
                    "goodsList": json.dumps(
                        [
                            {
                                "contractorCode": "vn0313698992",
                                "formCode": "BD.MT.02.1259",
                                "formValue": {
                                    "lotContent": {
                                        "Table": [
                                            {
                                                "name": "Holter recorder",
                                                "uom": "Bộ",
                                                "qty": "4",
                                                "origin": "Nhật Bản",
                                                "bidPrice": "92500000",
                                                "amount": "370000000",
                                                "codeGood": "Cardy 305 Pico",
                                                "labelGood": "Kenzmedico",
                                                "yearManufacture": "2025",
                                                "manufacturer": "Kenzmedico",
                                                "feature": "3-channel recorder",
                                                "parent": 0,
                                            }
                                        ]
                                    }
                                },
                            }
                        ]
                    ),
                    "contractorList": [
                        {"taxCode": "0313698992", "bidWiningPrice": 370000000, "bidResult": 1}
                    ],
                }
            ],
            "lotResultItems": [],
        },
        "bidpPlanDetailDTO": None,
    }


def lot_items_payload(notify_no="IB-0"):
    def item(name, qty, unit_price, *, winning=None, parent="2600239700"):
        row = {
            "name": name,
            "uom": "cái",
            "qty": qty,
            "origin": "Malaysia",
            "bidPrice": unit_price,
            "amount": qty * unit_price,
            "codeGood": "ACUSON Maple Select",
            "labelGood": "Siemens",
            "yearManufacture": "2026",
            "manufacturer": "Siemens",
            "feature": "Ultrasound",
            "parent": parent,
        }
        if winning is not None:
            row["lotWiningPrice"] = winning
        return row

    return {
        "bideContractorInputResultDTO": {
            "id": RESULT_ID,
            "notifyNo": notify_no,
            "notifyVersion": "01",
            "resultVersion": "01",
            "bidPriceUnit": "VND",
            "lotResultDTO": [
                {
                    "id": "lot-uuid-a",
                    "lotNo": "PP2600239700",
                    "lotName": "Lot A",
                    "lotPrice": 1445000000,
                    "lotEstimatePrice": None,
                    "winningCode": "vn0101174429",
                    "goodsList": None,
                    "contractorList": [
                        {"taxCode": "0101174429", "bidWiningPrice": 1450000000, "bidResult": 1}
                    ],
                },
                {
                    "id": "lot-uuid-b",
                    "lotNo": "PP2600239701",
                    "lotName": "Lot B",
                    "lotPrice": 775000000,
                    "lotEstimatePrice": None,
                    "winningCode": "vn0106757689",
                    "goodsList": None,
                    "contractorList": [
                        {"taxCode": "0106757689", "bidWiningPrice": 773000000, "bidResult": 1}
                    ],
                },
            ],
            "lotResultItems": [
                {
                    "lotResultId": "lot-uuid-a",
                    "contractorCode": "vn0101174429",
                    "formCode": "HH_12.1",
                    "formValue": json.dumps(
                        [
                            item("Scope A1", 1, 715000000, winning=715000000),
                            item("Scope A2", 1, 735000000, winning=735000000),
                        ]
                    ),
                },
                {
                    "lotResultId": "lot-uuid-b",
                    "contractorCode": "vn0106757689",
                    "formCode": "HH_12.1",
                    # bidPrice differs from lotWiningPrice: both roles are written.
                    "formValue": json.dumps(
                        [item("Scope B1", 1, 780000000, winning=773000000)]
                    ),
                },
            ],
        },
        "bidpPlanDetailDTO": None,
    }


def money_by_role(parsed):
    return {(m.role, m.owner_ref): m for m in parsed.money}


def flat_goodslist_payload(notify_no="IB-0"):
    """Flat goodsList encoding (parser v6): the entry itself is the item.

    Modeled on the 7 live records from the 2026-11-12 deploy canary
    (e.g. IB2600597038, IB2600599802): entries carry name/quantity/unit/
    description/origin/priceUnit with formValue null and no lotContent;
    lotResultItems is null. priceUnit is the winning unit price.
    """
    return {
        "bideContractorInputResultDTO": {
            "id": RESULT_ID,
            "notifyNo": notify_no,
            "notifyVersion": "00",
            "resultVersion": "00",
            "bidPriceUnit": "VND",
            "lotResultDTO": [
                {
                    "id": "lot-uuid-flat",
                    "lotNo": "BP2600000001",
                    "lotName": "Flat lot",
                    "lotPrice": 317876794,
                    "lotEstimatePrice": None,
                    "winningCode": "vn0000000001",
                    "goodsList": json.dumps(
                        [
                            {
                                "id": "flat-item-1",
                                "name": "Flat widget",
                                "code": "Flat widget",
                                "lableGood": "Acme",  # server's own misspelling, kept verbatim
                                "yearGood": 2026,
                                "manufacturer": "Acme",
                                "quantity": 2,
                                "unit": "Cái",
                                "description": "A flat widget",
                                "origin": "Việt Nam",
                                "priceUnit": 45000000,
                                "cperiodText": "30 ngày",
                                "formValue": None,
                            },
                            {
                                "id": "flat-item-2",
                                "name": "Flat gas",
                                "code": "N/A",
                                "lableGood": "Petrolimex",
                                "yearGood": "2026",
                                "manufacturer": "Petrolimex",
                                "maHS": "",
                                "quantity": 5034.06,  # float quantity, as in the live LPG record
                                "unit": "kg",
                                "description": "Flat gas",
                                "origin": "Việt Nam",
                                "priceUnit": 45267,
                                "note": "",
                                "cperiodText": "10 tháng",
                                "formValue": None,
                            },
                        ]
                    ),
                    "contractorList": [
                        {"taxCode": "0000000001", "bidWiningPrice": 317876794, "bidResult": 1}
                    ],
                }
            ],
            "lotResultItems": None,
        },
        "bidpPlanDetailDTO": None,
    }


def seed_kqlcnt_record(root, *, with_input_result_id=False):
    """One KQLCNT record, seeded via the TBMT helper then re-familied.

    seed_records() only seeds TBMT: its boundary rule carries no KQLCNT query
    contract, so a KQLCNT Runner backfill stays PARTIAL. The deep lane under
    test reads only family / natural_key / version_raw / normalized_json, so
    re-familing one seeded record is exact for this test.
    """
    import json as _json

    from test_deep_run import seed_records

    writer = seed_records(root, family="TBMT")
    writer.conn.execute(
        "UPDATE source_records SET family='KQLCNT' WHERE natural_key='IB-0'"
    )
    if with_input_result_id:
        row = writer.conn.execute(
            "SELECT v.id, v.normalized_json FROM source_record_versions v"
            " JOIN source_records r ON r.id=v.source_record_id"
            " WHERE r.natural_key='IB-0' AND v.content_version_seq=r.current_content_version"
        ).fetchone()
        normalized = _json.loads(row[1])
        normalized["inputResultId"] = RESULT_ID
        writer.conn.execute(
            "UPDATE source_record_versions SET normalized_json=? WHERE id=?",
            (_json.dumps(normalized, ensure_ascii=False), row[0]),
        )
    writer.conn.commit()
    return writer


# ---------------------------------------------------------------- parser: goodsList encoding

def test_m1_goodslist_encoding_writes_lot_and_item_winning_price():
    parsed = parse_detail(public_spec("KQLCNT", "M1"), goodslist_payload())
    assert parsed.parser_version == 6
    assert parsed.capability == "STRUCTURED_ITEMS"
    assert parsed.problems == ()
    assert len(parsed.lots) == 1
    lot = parsed.lots[0]
    assert lot.lot_no_raw == "BP2600067419"
    assert len(lot.goods) == 1
    good = lot.goods[0]
    assert good.name == "Holter recorder"
    assert good.unit == "Bộ"
    assert good.quantity == 4
    assert good.specification == "3-channel recorder"
    by_role = money_by_role(parsed)
    assert by_role[("LOT_PRICE", "BP2600067419")].value == 371800000
    assert by_role[("LOT_ESTIMATE_PRICE", "BP2600067419")].value == 380000000
    item_price = by_role[("ITEM_WINNING_PRICE", "0")]
    assert item_price.value == 92500000
    assert item_price.currency_code == "VND"
    assert ("ITEM_BID_PRICE", "0") not in by_role  # no lotWiningPrice: bid IS the winning price


# ---------------------------------------------------------------- parser: lotResultItems encoding

def test_m1_lot_result_items_link_by_lot_result_id_and_sum_to_lot():
    parsed = parse_detail(public_spec("KQLCNT", "M1"), lot_items_payload())
    assert parsed.problems == ()
    assert len(parsed.lots) == 2
    assert [len(lot.goods) for lot in parsed.lots] == [2, 1]
    by_role = money_by_role(parsed)
    assert by_role[("ITEM_WINNING_PRICE", "0")].value == 715000000
    assert by_role[("ITEM_WINNING_PRICE", "1")].value == 735000000
    assert by_role[("ITEM_WINNING_PRICE", "2")].value == 773000000
    # The one row whose bid differs from its winning price keeps both roles.
    assert by_role[("ITEM_BID_PRICE", "2")].value == 780000000
    assert sum(
        m.value for m in parsed.money
        if m.role == "ITEM_WINNING_PRICE" and m.owner_ref in ("0", "1")
    ) == 1450000000  # == the winner's bidWiningPrice for lot A


def test_m1_unlinked_lot_result_item_is_unallotted_not_dropped():
    payload = lot_items_payload()
    payload["bideContractorInputResultDTO"]["lotResultItems"][0]["lotResultId"] = "no-such-lot"
    parsed = parse_detail(public_spec("KQLCNT", "M1"), payload)
    assert len(parsed.lots) == 2
    assert [len(lot.goods) for lot in parsed.lots] == [0, 1]
    assert len(parsed.unallotted_goods) == 2
    assert any("UNLINKED_LOT" in p for p in parsed.problems)


def test_m1_both_encodings_prefers_lot_result_items_and_warns():
    payload = lot_items_payload()
    lot = payload["bideContractorInputResultDTO"]["lotResultDTO"][0]
    lot["goodsList"] = json.dumps(
        [
            {
                "contractorCode": "vn0101174429",
                "formCode": "BD.MT.02.1259",
                "formValue": {
                    "lotContent": {
                        "Table": [
                            {"name": "Duplicate of A1", "uom": "cái", "qty": 1,
                             "bidPrice": 715000000, "amount": 715000000}
                        ]
                    }
                },
            }
        ]
    )
    parsed = parse_detail(public_spec("KQLCNT", "M1"), payload)
    assert [len(lot.goods) for lot in parsed.lots] == [2, 1]  # no double-count
    assert any("BOTH_ENCODINGS" in w for w in parsed.warnings)


# ---------------------------------------------------------------- parser: flat goodsList encoding (parser v6)

def test_m1_flat_goodslist_entries_parse_with_price_unit():
    parsed = parse_detail(public_spec("KQLCNT", "M1"), flat_goodslist_payload())
    assert parsed.parser_version == 6
    assert parsed.capability == "STRUCTURED_ITEMS"
    assert parsed.problems == ()
    assert len(parsed.lots) == 1
    lot = parsed.lots[0]
    assert len(lot.goods) == 2
    widget, gas = lot.goods
    assert widget.name == "Flat widget"
    assert widget.unit == "Cái"
    assert widget.quantity == 2
    assert widget.specification == "A flat widget"
    assert gas.quantity == pytest.approx(5034.06)
    by_role = money_by_role(parsed)
    assert by_role[("ITEM_WINNING_PRICE", "0")].value == 45000000
    assert by_role[("ITEM_WINNING_PRICE", "1")].value == 45267
    assert by_role[("ITEM_WINNING_PRICE", "1")].source_path.endswith(".priceUnit")
    assert sum(
        g.quantity * by_role[("ITEM_WINNING_PRICE", str(g.row_ordinal))].value
        for g in lot.goods
    ) == pytest.approx(317876794.02, abs=1.0)  # == the winner's bidWiningPrice


def test_m1_flat_entry_without_price_is_warning_not_error():
    payload = flat_goodslist_payload()
    entries = json.loads(payload["bideContractorInputResultDTO"]["lotResultDTO"][0]["goodsList"])
    del entries[1]["priceUnit"]
    payload["bideContractorInputResultDTO"]["lotResultDTO"][0]["goodsList"] = json.dumps(entries)
    parsed = parse_detail(public_spec("KQLCNT", "M1"), payload)
    assert parsed.problems == ()
    assert len(parsed.lots[0].goods) == 2  # the priceless row is still captured
    by_role = money_by_role(parsed)
    assert ("ITEM_WINNING_PRICE", "0") in by_role
    assert ("ITEM_WINNING_PRICE", "1") not in by_role
    assert any("FLAT_NO_ITEM_PRICE" in w for w in parsed.warnings)


def test_m1_goodslist_entry_without_name_or_table_stays_a_problem():
    payload = flat_goodslist_payload()
    entries = json.loads(payload["bideContractorInputResultDTO"]["lotResultDTO"][0]["goodsList"])
    entries[1] = {"id": "nameless", "quantity": 1, "formValue": None}
    payload["bideContractorInputResultDTO"]["lotResultDTO"][0]["goodsList"] = json.dumps(entries)
    parsed = parse_detail(public_spec("KQLCNT", "M1"), payload)
    assert any("NO_TABLE" in p for p in parsed.problems)
    assert len(parsed.lots[0].goods) == 1  # the named flat row still parses


def test_m1_table_and_flat_lots_coexist():
    payload = goodslist_payload()
    flat_lot = flat_goodslist_payload()["bideContractorInputResultDTO"]["lotResultDTO"][0]
    payload["bideContractorInputResultDTO"]["lotResultDTO"].append(flat_lot)
    parsed = parse_detail(public_spec("KQLCNT", "M1"), payload)
    assert parsed.problems == ()
    assert [len(lot.goods) for lot in parsed.lots] == [1, 2]
    by_role = money_by_role(parsed)
    assert by_role[("ITEM_WINNING_PRICE", "0")].value == 92500000  # Table lot
    assert by_role[("ITEM_WINNING_PRICE", "1")].value == 45000000  # flat lot
    assert by_role[("ITEM_WINNING_PRICE", "2")].value == 45267


# ---------------------------------------------------------------- parser: empty / damaged shapes

def test_m1_no_items_is_notice_metadata_not_an_error():
    payload = {
        "bideContractorInputResultDTO": {
            "id": RESULT_ID,
            "notifyNo": "IB-0",
            "notifyVersion": "00",
            "resultVersion": "00",
            "bidPriceUnit": "VND",
            "lotResultDTO": [],
            "lotResultItems": [],
        }
    }
    parsed = parse_detail(public_spec("KQLCNT", "M1"), payload)
    assert parsed.capability == "NOTICE_METADATA"
    assert parsed.problems == ()
    assert parsed.money == ()


def test_m1_invalid_json_string_is_a_problem():
    payload = lot_items_payload()
    payload["bideContractorInputResultDTO"]["lotResultItems"][0]["formValue"] = "{not json"
    parsed = parse_detail(public_spec("KQLCNT", "M1"), payload)
    assert parsed.problems, "damaged JSON must surface, never silently drop rows"
    assert len(parsed.lots[0].goods) == 0


def test_m1_missing_dto_raises():
    with pytest.raises(DetailShapeError):
        parse_detail(public_spec("KQLCNT", "M1"), {"bidpPlanDetailDTO": None})


def test_m1_missing_currency_is_unparsable_not_guessed():
    parsed = parse_detail(public_spec("KQLCNT", "M1"), goodslist_payload(currency=None))
    by_role = money_by_role(parsed)
    item_price = by_role[("ITEM_WINNING_PRICE", "0")]
    assert item_price.presence_state == "UNPARSABLE"
    assert item_price.value is None
    assert item_price.currency_code is None


# ---------------------------------------------------------------- spec + identity

def test_m1_public_spec_is_kqlcnt_only():
    assert public_spec("KQLCNT", "M1") is not None
    assert public_spec("KQLCNT", "M1").parser_version == PARSER_VERSION == 6
    assert public_spec("TBMT", "M1") is None
    assert public_spec("KQLCNT", "HSMT") is None


def test_m1_identity_checks_dto_id_notify_and_version():
    payload = lot_items_payload()
    record = {"natural_key": "IB-0", "version_raw": "01"}
    assert identity_problem(payload, record, "M1", RESULT_ID) is None
    # "01" vs "1" is the same version after the lstrip("0") rule.
    assert identity_problem(payload, {"natural_key": "IB-0", "version_raw": "1"}, "M1", RESULT_ID) is None
    assert identity_problem(payload, {"natural_key": "IB-OTHER", "version_raw": "01"}, "M1", RESULT_ID) == "NOTICE_IDENTITY_MISMATCH"
    assert identity_problem(payload, record, "M1", "different-id") == "NOTICE_UUID_MISMATCH"
    assert identity_problem(payload, {"natural_key": "IB-0", "version_raw": "02"}, "M1", RESULT_ID) == "NOTICE_VERSION_MISMATCH"
    assert identity_problem({}, record, "M1", RESULT_ID) == "IDENTITY_SHAPE_INVALID"


# ---------------------------------------------------------------- end to end: dispatch + projection

def test_m1_dispatch_projects_lots_goods_and_money(tmp_path: Path):
    from santhau_studio.collectors.deep import DeepResponse
    from santhau_studio.workers.deep_run import run_deep_batch

    writer = seed_kqlcnt_record(tmp_path / "data")
    try:
        record_id = writer.conn.execute(
            "SELECT id FROM source_records WHERE natural_key='IB-0'"
        ).fetchone()[0]
        payload = goodslist_payload()
        result = run_deep_batch(
            writer, run_id="run-m1", family="KQLCNT", lane="M1",
            scope={"record_ids": [record_id]},
            fetcher=lambda _f, _k: DeepResponse(payload, payload),
            spec_provider=public_spec,
        )
        assert result.status == "COMPLETED", result.detail
        assert result.detail["captured"] == 1
        assert writer.conn.execute("SELECT COUNT(*) FROM lots").fetchone()[0] == 1
        assert writer.conn.execute("SELECT COUNT(*) FROM goods_rows").fetchone()[0] == 1
        roles = {
            r[0] for r in writer.conn.execute("SELECT role FROM money_amounts")
        }
        assert {"LOT_PRICE", "LOT_ESTIMATE_PRICE", "ITEM_WINNING_PRICE"} <= roles
        state = writer.conn.execute(
            "SELECT state FROM deep_tasks WHERE source_record_id=? AND lane='M1'",
            (record_id,),
        ).fetchone()[0]
        assert state == "FETCHED"
    finally:
        writer.close()


def test_m1_flat_dispatch_projects_goods_and_money(tmp_path: Path):
    from santhau_studio.collectors.deep import DeepResponse
    from santhau_studio.workers.deep_run import run_deep_batch

    writer = seed_kqlcnt_record(tmp_path / "data")
    try:
        record_id = writer.conn.execute(
            "SELECT id FROM source_records WHERE natural_key='IB-0'"
        ).fetchone()[0]
        payload = flat_goodslist_payload()
        result = run_deep_batch(
            writer, run_id="run-m1-flat", family="KQLCNT", lane="M1",
            scope={"record_ids": [record_id]},
            fetcher=lambda _f, _k: DeepResponse(payload, payload),
            spec_provider=public_spec,
        )
        assert result.status == "COMPLETED", result.detail
        assert result.detail["captured"] == 1
        assert writer.conn.execute("SELECT COUNT(*) FROM goods_rows").fetchone()[0] == 2
        roles = {
            r[0] for r in writer.conn.execute("SELECT role FROM money_amounts")
        }
        assert "ITEM_WINNING_PRICE" in roles
        state = writer.conn.execute(
            "SELECT state FROM deep_tasks WHERE source_record_id=? AND lane='M1'",
            (record_id,),
        ).fetchone()[0]
        assert state == "FETCHED"
    finally:
        writer.close()


def test_m1_candidates_and_sweep_cover_kqlcnt_records(tmp_path: Path):
    from santhau_studio.linux.deep import sweep_deep
    from santhau_studio.workers.auto_deep import candidates, configure

    writer = seed_kqlcnt_record(tmp_path / "data", with_input_result_id=True)
    try:
        configure(writer.conn, {"enabled": True, "batch_requests": 30})
        found = [c for c in candidates(writer.conn, 30) if c["lane"] == "M1"]
        assert len(found) == 1
        assert found[0]["family"] == "KQLCNT"
        preview = sweep_deep(tmp_path / "data", lane="M1", limit=100)
        assert preview["executed"] is False
        assert preview["records_matched"] == 1
        assert preview["jobs"] == 1
        assert not list((tmp_path / "data" / "jobs" / "queued").glob("*"))
    finally:
        writer.close()
