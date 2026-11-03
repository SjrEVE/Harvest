"""A4 — sanitizer contract tests.

Every input value here is invented for the test. The point of these tests is not
that the output looks different; it is that the verifier would FAIL if a real
value survived, so a green run is evidence rather than decoration.
"""

from __future__ import annotations

import json
import re

import pytest

from sanitizer import (
    Transform,
    _walk_transform,
    find_leaks,
    sanitize_json_bytes,
    seed_for,
    strategy_for,
)

REAL_SHAPED_INPUT = {
    "code": 200,
    "page": {
        "pageNumber": 0,
        "pageSize": 10,
        "totalElements": 4321,
        "content": [
            {
                "notifyId": "5c7f1a2b3c4d5e6f7a8b9c0d1e2f3a4b",
                "notifyNo": "IB2600123456",
                "notifyNoStand": "IB2600123456-00",
                "notifyVersion": "00",
                "title": "Cung cấp máy vi tính văn phòng cho Sở ABC năm 2026",
                "investorName": "Ban Quản lý Dự án Đầu tư xây dựng tỉnh X",
                "investorCode": "vnzabcdefghijklmnop",
                "investorTaxCode": "0101234567",
                "planNo": "PL2600987654",
                "bidPrice": [12345678901, 9876543210],
                "contractorName": [
                    "Công ty TNHH Xây dựng Hoàn Kiếm",
                    "Doanh nghiệp tư nhân Sao Đỏ",
                ],
                "publishedAt": "2026-10-31T09:00:00",
                "detailUrl": "https://muasamcong.mof.gov.vn/o/egp/notice/IB2600123456",
                "provinceName": "Hà Nội",
                "status": "OPEN",
            }
        ],
    },
}


def sanitize(payload: dict, seed: int = 42) -> dict:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    rendered, _ = sanitize_json_bytes(raw, seed)
    return json.loads(rendered.decode("utf-8"))


def first_record(payload: dict) -> dict:
    return payload["page"]["content"][0]


def test_identity_fields_do_not_survive() -> None:
    out = first_record(sanitize(REAL_SHAPED_INPUT))
    source = first_record(REAL_SHAPED_INPUT)
    for key in (
        "notifyId",
        "notifyNo",
        "investorName",
        "investorCode",
        "planNo",
        "detailUrl",
        "provinceName",
    ):
        assert out[key] != source[key], f"{key} kept its real value"


def test_list_of_names_is_transformed_regression() -> None:
    """A list loses its own field name; the parent key must still drive the strategy."""
    out = first_record(sanitize(REAL_SHAPED_INPUT))
    source = first_record(REAL_SHAPED_INPUT)
    assert out["contractorName"] != source["contractorName"]
    assert all(name != "Công ty TNHH Xây dựng Hoàn Kiếm" for name in out["contractorName"])
    assert all("Doanh nghiệp" not in name for name in out["contractorName"])


def test_bid_price_list_values_replaced() -> None:
    out = first_record(sanitize(REAL_SHAPED_INPUT))
    assert set(out["bidPrice"]).isdisjoint(set(first_record(REAL_SHAPED_INPUT)["bidPrice"]))


def test_verify_detects_leak_when_output_is_a_copy() -> None:
    """Negative control: an untouched copy must be reported as leaking."""
    leaks = find_leaks(REAL_SHAPED_INPUT, json.loads(json.dumps(REAL_SHAPED_INPUT)))
    assert leaks, "verifier accepted an un-sanitized copy"
    paths = {leak["path"] for leak in leaks}
    assert "page.content[0].notifyNo" in paths
    assert "page.content[0].investorTaxCode" in paths


def test_verify_passes_on_sanitized_output() -> None:
    out = sanitize(REAL_SHAPED_INPUT)
    assert find_leaks(REAL_SHAPED_INPUT, out) == []


def test_same_seed_is_deterministic() -> None:
    assert json.dumps(sanitize(REAL_SHAPED_INPUT, 7), sort_keys=True) == json.dumps(
        sanitize(REAL_SHAPED_INPUT, 7), sort_keys=True
    )


def test_different_seed_changes_values() -> None:
    assert sanitize(REAL_SHAPED_INPUT, 1) != sanitize(REAL_SHAPED_INPUT, 2)


def test_structure_and_counts_preserved() -> None:
    out = sanitize(REAL_SHAPED_INPUT)
    assert list(out) == list(REAL_SHAPED_INPUT)
    assert list(out["page"]) == list(REAL_SHAPED_INPUT["page"])
    assert len(out["page"]["content"]) == 1
    assert len(out["page"]["content"][0]["bidPrice"]) == 2
    assert len(out["page"]["content"][0]["contractorName"]) == 2


def test_pagination_and_enums_are_kept() -> None:
    out = first_record(sanitize(REAL_SHAPED_INPUT))
    page = sanitize(REAL_SHAPED_INPUT)["page"]
    assert page["pageNumber"] == 0 and page["pageSize"] == 10
    assert out["notifyVersion"] == "00"
    assert out["status"] == "OPEN"


def test_identifier_shape_survives_so_parsers_still_work() -> None:
    out = first_record(sanitize(REAL_SHAPED_INPUT))
    assert re.fullmatch(r"[A-Z]{2}\d{9,12}", out["notifyNo"]), out["notifyNo"]
    assert re.fullmatch(r"\d{10}", out["investorTaxCode"]), out["investorTaxCode"]
    assert out["investorCode"].startswith("vn")


def test_money_keeps_magnitude_class_but_not_value() -> None:
    out = first_record(sanitize(REAL_SHAPED_INPUT))
    original = first_record(REAL_SHAPED_INPUT)["bidPrice"]
    for before, after in zip(original, out["bidPrice"], strict=True):
        assert len(str(int(after))) == len(str(int(before)))
        assert after != before


def test_dates_shifted_out_of_the_real_era() -> None:
    out = first_record(sanitize(REAL_SHAPED_INPUT))
    assert "2026" not in out["publishedAt"]
    assert "2019" in out["publishedAt"]


def test_source_host_removed_from_urls() -> None:
    out = first_record(sanitize(REAL_SHAPED_INPUT))
    assert "muasamcong.mof.gov.vn" not in out["detailUrl"]


def test_strategy_table_covers_the_fields_that_matter() -> None:
    expected = {
        "notifyNo": "code",
        "notifyId": "id36",
        "investorTaxCode": "tax_id",
        "investorName": "org_name",
        "bidPrice": "money",
        "totalElements": "measure",
        "title": "text",
        "publishedAt": "timestamp",
        "provinceName": "place",
        "detailUrl": "url",
        "notifyVersion": "keep",
        "pageNumber": "keep",
        "pageSize": "keep",
    }
    assert {key: strategy_for(key) for key in expected} == expected


def test_aggregate_counts_are_synthesized_not_kept() -> None:
    """totalElements is a measurement of the real source, so it must not survive either."""
    out = sanitize(REAL_SHAPED_INPUT)
    assert out["page"]["totalElements"] != REAL_SHAPED_INPUT["page"]["totalElements"]
    assert len(str(out["page"]["totalElements"])) == len(
        str(REAL_SHAPED_INPUT["page"]["totalElements"])
    )


def test_transform_counters_report_work_done() -> None:
    tf = Transform(1)
    _walk_transform(REAL_SHAPED_INPUT, tf)
    assert tf.converted > 10
    assert tf.kept > 0


def test_seed_derivation_is_stable_and_overridable() -> None:
    raw = b"abc"
    assert seed_for(raw, None) == seed_for(raw, None)
    assert seed_for(raw, 99) == 99


def test_nested_empty_and_null_values_are_preserved() -> None:
    payload = {"page": {"content": [{"notifyNo": "IB2600123456", "note": None, "tags": []}]}}
    out = sanitize(payload)
    record = out["page"]["content"][0]
    assert record["note"] is None
    assert record["tags"] == []
    assert record["notifyNo"] != "IB2600123456"


@pytest.mark.parametrize("seed", [0, 5, 123, 99991])
def test_no_invented_identity_value_survives_any_seed(seed: int) -> None:
    assert find_leaks(REAL_SHAPED_INPUT, sanitize(REAL_SHAPED_INPUT, seed)) == []


def test_createdby_principal_is_not_mistaken_for_a_timestamp() -> None:
    """Regression: 'createdBy' matched the date rule and was returned untouched."""
    payload = {"page": {"content": [{"createdBy": "vn9876543210-30", "updatedBy": "admin77"}]}}
    out = sanitize(payload)["page"]["content"][0]
    assert out["createdBy"] != "vn9876543210-30"
    assert out["updatedBy"] != "admin77"
    assert out["createdBy"].startswith("vn")
    assert re.search(r"-\d+$", out["createdBy"]), "branch suffix shape lost"
    assert find_leaks(payload, sanitize(payload)) == []


def test_non_date_string_in_date_named_field_is_still_transformed() -> None:
    payload = {
        "page": {
            "content": [
                {"publishedAt": "not-a-date-but-sensitive", "createdAt": "2026-10-31T09:00:00"}
            ]
        }
    }
    out = sanitize(payload)["page"]["content"][0]
    assert out["publishedAt"] != "not-a-date-but-sensitive"
    assert "2026" not in out["createdAt"]


def test_unknown_numeric_field_keeps_its_type() -> None:
    """A count under an unrecognised key must stay a number, or parsers see a new shape."""
    payload = {"page": {"records": 1234567, "oddLabel": "some real text"}}
    out = sanitize(payload)["page"]
    assert isinstance(out["records"], int)
    assert out["records"] != 1234567
    assert isinstance(out["oddLabel"], str)


def test_kept_enum_elsewhere_is_not_reported_as_a_leak() -> None:
    """Path-scoped comparison: 'PARTIAL' living in a kept status field is not exposure."""
    payload = {
        "coverage": {"egp_home": {"coverage": "PARTIAL", "reason": "max_records cap reached"}},
        "runs": [{"status": "PARTIAL"}],
    }
    out = sanitize(payload)
    assert out["runs"][0]["status"] == "PARTIAL"
    assert out["coverage"]["egp_home"]["coverage"] != "PARTIAL"
    assert find_leaks(payload, out) == []


def test_relocated_identifier_is_detected() -> None:
    """If a real id were copied into another field, the cross-path check must catch it."""
    payload = {"page": {"content": [{"notifyNo": "IB2600123456"}]}}
    tampered = {
        "page": {"content": [{"notifyNo": "SY999999999"}]},
        "audit": {"ref": "IB2600123456"},
    }
    leaks = find_leaks(payload, tampered)
    assert [leak["kind"] for leak in leaks] == ["relocated"]
