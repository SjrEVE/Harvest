"""Captured public table contracts, not permission to crawl additional tenders."""
import copy
import json
from uuid import UUID as UUID_TYPE

import pytest

from santhau_studio.collectors import public_tables
from santhau_studio.collectors.deep import DetailShapeError, parse_detail

NOTICE = "IB2600570777"
VERSION = "01"
UUID = str(UUID_TYPE(int=1))


def hsmt():
    return {"bidoInvBiddingDTO": [{
        "notifyNo": NOTICE, "notifyVersion": VERSION, "formCode": "BD.DT.02.1960",
        "formContent": json.dumps([{"columnName": n} for n in ("name", "qty", "uom")]),
        "formValue": json.dumps({"Table": [
            {"id": 10, "lotNo": "L1", "lotName": "Lot A", "parent": 0, "isParent": False},
            {"id": 11, "name": "Item A", "parent": 10, "qty": 0, "uom": "Cái", "manufacture": "Maker"},
            {"id": 20, "lotNo": "L2", "lotName": "Lot B", "parent": 0, "isParent": False},
            {"id": 21, "name": "Item B", "parent": 20, "qty": 3, "uom": "Bộ"},
        ]}),
    }]}


def test_hsmt_uses_parent_links_even_when_is_parent_is_false():
    source = hsmt()
    original = copy.deepcopy(source)
    detail = public_tables.normalize_hsmt(source, notify_no=NOTICE, version=VERSION)
    parsed = parse_detail(public_tables.table_spec("HSMT"), detail)
    assert [lot.lot_no_raw for lot in parsed.lots] == ["L1", "L2"]
    assert parsed.lots[0].goods[0].quantity == 0
    assert detail["lots"][0]["goods"][0]["manufacture"] == "Maker"
    assert source == original


@pytest.mark.parametrize("damage", ["wrong_notice", "wrong_version", "orphan", "empty", "invalid_json"])
def test_hsmt_rejects_unbound_or_unparseable_tables(damage):
    source = hsmt()
    form = source["bidoInvBiddingDTO"][0]
    if damage == "wrong_notice":
        form["notifyNo"] = "IB-OTHER"
    elif damage == "wrong_version":
        form["notifyVersion"] = "00"
    elif damage == "orphan":
        value = json.loads(form["formValue"])
        value["Table"][1]["parent"] = 999
        form["formValue"] = value
    elif damage == "empty":
        source["bidoInvBiddingDTO"] = None
    else:
        form["formValue"] = "{"
    with pytest.raises(DetailShapeError):
        public_tables.normalize_hsmt(source, notify_no=NOTICE, version=VERSION)


def reoffer():
    return {"notifyNo": NOTICE, "notifyVersion": VERSION, "notifyId": UUID,
            "bidForm": "CGTTRG", "contractorsResult": [
        {"contractorCode": "vn01", "contractorName": "Supplier", "lotNo": lot,
         "formValue": {"Table": [{"name": "Item", "qty": 2, "unitPrice": 10.5, "subTotal": 21}]},
         "reofferPriceFinal": 21} for lot in ("L1", "L2")
    ]}


def test_reoffer_preserves_both_lots_without_counting_one_supplier_twice():
    detail = public_tables.normalize_reoffer(reoffer(), notify_no=NOTICE, version=VERSION, notify_id=UUID)
    parsed = parse_detail(public_tables.table_spec("REOFFER"), detail)
    assert len(parsed.members) == 1
    assert parsed.members[0].role == "OFFEROR"
    assert len(detail["quotes"]) == 2
    assert detail["quotes"][0]["goods"][0]["unitPrice"] == 10.5
    assert parsed.lots == (), "bidder prices must not overwrite requested HSMT quantities"


def test_reoffer_rejects_wrong_identity_and_zero_uuid():
    for uuid in (str(UUID_TYPE(int=0)), "other"):
        with pytest.raises(DetailShapeError):
            public_tables.normalize_reoffer(reoffer(), notify_no=NOTICE, version=VERSION, notify_id=uuid)


def test_reoffer_lot_total_is_preserved_but_not_counted_as_an_item():
    source = reoffer()
    for quote in source["contractorsResult"]:
        quote["formValue"]["Table"].append({"id": 10, "lotNo": quote["lotNo"],
                                           "name": None, "subTotal": 21, "isParent": False})
    result = public_tables.normalize_reoffer(source, notify_no=NOTICE, version=VERSION, notify_id=UUID)
    assert len(result["quotes"][0]["goods"]) == 1
    assert result["quotes"][0]["lot_summaries"][0]["subTotal"] == 21


def test_bounded_fetcher_uses_exact_endpoint_and_preserves_response():
    from types import SimpleNamespace

    from santhau_studio.collectors.budget import RequestBudget

    calls = []

    class Lane:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, body):
            calls.append(body)
            return SimpleNamespace(payload=hsmt())

    fetcher = public_tables.canary_table_fetcher("HSMT", {NOTICE: {"notify_id": UUID, "version": VERSION}},
                                                budget=RequestBudget(1, 30), lane_factory=Lane)
    result = fetcher("TBMT", NOTICE)
    assert calls[0]["url"].endswith(public_tables.HSMT_ENDPOINT)
    assert "?" not in calls[0]["url"]
    assert calls[0]["retry"].max_attempts == 1
    assert calls[1] == {"id": UUID, "processApply": "LDT"}
    assert result.response_payload == hsmt()
    assert len(result.detail["lots"]) == 2
    with pytest.raises(DetailShapeError):
        fetcher("TBMT", "IB-NOT-APPROVED")
    with pytest.raises(DetailShapeError):
        fetcher("KQLCNT", NOTICE)
    assert len(calls) == 2


def test_bounded_fetcher_rejects_zero_id_before_network():
    from santhau_studio.collectors.budget import RequestBudget

    with pytest.raises(DetailShapeError):
        public_tables.canary_table_fetcher("HSMT", {NOTICE: {"notify_id": str(UUID_TYPE(int=0)), "version": VERSION}},
                                           budget=RequestBudget(1, 30))


def test_public_listing_singleton_title_array_is_searchable_without_flattening_multiple_titles():
    from santhau_studio.collectors.extraction import extract_page
    from santhau_studio.collectors.source_registry import binding_for

    row = {"notifyNo": NOTICE, "notifyVersion": VERSION, "bidName": ["Gói mua vật tư"]}
    payload = {"page": {"content": [row]}}
    result = extract_page(payload, binding_for("TBMT").spec)
    assert result.records[0].normalized.fields["title"] == "Gói mua vật tư"
    assert result.records[0].payload["bidName"] == ["Gói mua vật tư"]
    row["bidName"] = ["Title A", "Title B"]
    result = extract_page(payload, binding_for("TBMT").spec)
    assert result.records[0].normalized.fields["title"] is None
    assert result.records[0].payload["bidName"] == ["Title A", "Title B"]
