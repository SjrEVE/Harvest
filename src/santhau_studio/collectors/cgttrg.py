"""Public CGTTRG contracts verified on three captured notices, not all procurement forms."""

import json
import math
import re

from santhau_studio.collectors.deep import (
    DeepLaneSpec,
    DetailShapeError,
    ParsedDetail,
    ParsedGoodsRow,
    ParsedLot,
    ParsedMember,
    ParsedMoneyAmount,
    _presence_number,
    _text,
)
from santhau_studio.collectors.hsmt_forms import read_form
from santhau_studio.domain.identity import record_object_sha

PARSER_VERSION = 4

PREFIX = "https://muasamcong.mof.gov.vn/o/egp-portal-contractor-selection-v2/services/"
ENDPOINTS = {
    "HSMT": "lcnt_tbmcgtt_hsmt",
    "DETAIL": "online-reoffer/detail",
    "REOFFER": "online-reoffer/detail",
}

# Currency for lot/goods prices. HSMT payloads declare no currency, but the same
# package's REOFFER payload declares bidPriceUnit=VND on 1,679/1,679 live captures
# and its package bidPrice equals the sum of the HSMT lotPrices, so the lot prices
# are VND-denominated by same-package arithmetic, not by assumption. Tax basis
# stays UNKNOWN everywhere: no payload says whether tax is included.
VND = "VND"
TAX_UNKNOWN = "UNKNOWN"


def public_spec(family, lane):
    if family != "TBMT" or lane not in ENDPOINTS:
        return None
    return DeepLaneSpec(
        family,
        lane,
        "public-cgttrg-" + ENDPOINTS[lane],
        ("notifyVersion",),
        requires_session=False,
        parser_kind=lane,
        parser_version=PARSER_VERSION,
    )


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _money_amount(record, field, *, role, owner_kind, owner_ref, source_path, currency_code):
    """One money row for a source price field, or None when the field is absent.

    A parseable value with no attested currency becomes UNPARSABLE with the raw
    text kept — the schema forbids a KNOWN value without a KNOWN currency, and
    a currency must never be guessed. A malformed value is UNPARSABLE with its
    raw text kept. Absence writes no row: no MISSING rows are manufactured.
    """
    presence, value, raw = _presence_number(record, (field,))
    if presence == "MISSING":
        return None
    if presence == "KNOWN" and currency_code:
        return ParsedMoneyAmount(
            owner_kind, owner_ref, role, "KNOWN", value, raw,
            "KNOWN", currency_code, TAX_UNKNOWN, source_path,
        )
    return ParsedMoneyAmount(
        owner_kind, owner_ref, role, "UNPARSABLE", None,
        raw if raw is not None else str(record.get(field)),
        "KNOWN" if currency_code else "MISSING", currency_code, TAX_UNKNOWN, source_path,
    )


def _payload_currency(payload):
    """Currency the REOFFER payload itself declares: bidPriceUnit, else the
    moneyType inside the JSON-encoded bidPriceDetail string."""
    detail = payload.get("bidDetail")
    if isinstance(detail, dict):
        unit = detail.get("bidPriceUnit")
        if isinstance(unit, str) and len(unit.strip()) == 3:
            return unit.strip().upper()
        raw = detail.get("bidPriceDetail")
        if isinstance(raw, str):
            try:
                items = json.loads(raw)
            except ValueError:
                items = None
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        code = item.get("moneyType")
                        if isinstance(code, str) and len(code.strip()) == 3:
                            return code.strip().upper()
    return None


def _distinct_amount(rows, field):
    """Distinct non-null values of a price field across rows (raw JSON values)."""
    return {row.get(field) for row in rows if isinstance(row, dict) and row.get(field) is not None}


def _hsmt(payload):
    forms = payload.get("bidoInvBiddingDTO")
    if "bidoInvBiddingDTO" not in payload or (forms is not None and not isinstance(forms, list)):
        raise DetailShapeError("HSMT_FORMS_MISSING_OR_INVALID")
    lots, unallotted, problems, versions, schemas = [], [], [], [], []
    warnings, documents, kinds = [], [], []
    money = []
    ordinal = 0
    for form_index, form in enumerate(forms or []):
        if not isinstance(form, dict) or form.get("bidForm") != "CGTTRG":
            raise DetailShapeError("UNVERIFIED_HSMT_FORM")
        versions.append(
            {
                k: form.get(k)
                for k in ("notifyId", "notifyNo", "notifyVersion", "formCode", "lastModifiedDate")
            }
        )
        schemas.append({k: v for k, v in form.items() if k != "formValue"})
        content = read_form(form)
        data = content.data
        kinds.append(content.kind)
        warnings.extend(content.warnings)
        problems.extend(content.problems)
        documents.extend(content.documents)
        if content.kind not in ("STRUCTURED", "UNVERIFIED_STRUCTURED") or not isinstance(data.get("Table"), list):
            continue
        headers = {}
        for i, row in enumerate(data["Table"]):
            if (
                isinstance(row, dict)
                and row.get("lotNo")
                and row.get("lotName")
                and not row.get("name")
                and not row.get("tenThuoc")
            ):
                key = str(row.get("id"))
                if row.get("id") is None or key in headers:
                    raise DetailShapeError("AMBIGUOUS_LOT_PARENT")
                headers[key] = (row, [], i)
        for i, row in enumerate(data["Table"]):
            if any(i == v[2] for v in headers.values()):
                continue
            raw = row
            if not isinstance(row, dict):
                problems.append(f"FORM_{content.code}_ROW_{i}_NOT_OBJECT")
                row = {}
            # Medicine rows (form 1960) use tenThuoc/quantity instead of name/qty;
            # the fallback runs after the standard fields, never before them.
            qty_value = next(
                (row.get(field) for field in ("qty", "quantity") if row.get(field) is not None),
                None,
            )
            presence, qty, qty_raw = _presence_number(row, ("qty", "quantity"))
            if isinstance(qty_value, str) and re.fullmatch(
                r"[+-]?\d{1,3}(?:[.,]\d{3})+", qty_value.strip()
            ):
                presence, qty, qty_raw = "UNPARSABLE", None, qty_value
            if qty is not None and not math.isfinite(qty):
                presence, qty, qty_raw = "UNPARSABLE", None, str(qty_value)
            name = _text(row, ("name", "tenThuoc"))
            if presence != "KNOWN" or not name:
                problems.append(f"FORM_{content.code}_ROW_{i}_INVALID_FIELDS")
            good = ParsedGoodsRow(
                ordinal,
                name,
                _text(row, ("specification",)),
                _text(row, ("uom",)),
                presence,
                qty,
                qty_raw,
                raw,
            )
            # A goods row's own planned price (medicine pricePlan) is a unit price.
            # Verified on live captures: pricePlan is never populated in Table rows
            # today, so this is defensive — it writes nothing until the source does.
            amount = _money_amount(
                row, "pricePlan", role="UNIT_PRICE", owner_kind="GOODS_ROW",
                owner_ref=str(ordinal),
                source_path=f"bidoInvBiddingDTO[{form_index}].formValue.Table[{i}].pricePlan",
                currency_code=VND,
            )
            if amount is not None:
                money.append(amount)
            ordinal += 1
            parent = row.get("parent")
            if str(parent) in headers:
                headers[str(parent)][1].append(good)
            else:
                if parent not in (None, 0, "0", ""):
                    problems.append(f"FORM_{content.code}_ROW_{i}_UNKNOWN_PARENT_{parent}")
                unallotted.append(good)
        for row, goods, i in headers.values():
            lot_no = str(row["lotNo"])
            for field, role in (
                ("lotPrice", "LOT_PRICE"),
                ("lotEstimatePrice", "LOT_ESTIMATE_PRICE"),
                ("pricePlan", "LOT_PLAN_PRICE"),
            ):
                amount = _money_amount(
                    row, field, role=role, owner_kind="LOT", owner_ref=lot_no,
                    source_path=f"bidoInvBiddingDTO[{form_index}].formValue.Table[{i}].{field}",
                    currency_code=VND,
                )
                if amount is not None:
                    money.append(amount)
            lots.append(
                ParsedLot(
                    lot_no, row["lotName"], form.get("notifyVersion"), tuple(goods), row
                )
            )
    capability = "STRUCTURED_ITEMS"
    if "UNVERIFIED_STRUCTURED" in kinds or "UNVERIFIED" in kinds or "INVALID" in kinds:
        capability = "UNVERIFIED_FORM"
    elif "DOCUMENT" in kinds:
        capability = "DOCUMENT_ONLY" if "STRUCTURED" not in kinds and documents else "STRUCTURED_ITEMS_UNAVAILABLE"
    elif kinds and "STRUCTURED" not in kinds:
        capability = "STRUCTURED_ITEMS_UNAVAILABLE"
    return ParsedDetail(
        _json({"forms": versions}),
        {"forms": schemas},
        tuple(lots),
        (),
        tuple(problems),
        tuple(unallotted),
        documents=tuple(documents), warnings=tuple(sorted(set(warnings))), capability=capability, parser_version=PARSER_VERSION,
        money=tuple(money),
    )


def _reoffer(payload, lane):
    if payload.get("bidForm") != "CGTTRG" or not payload.get("notifyNo"):
        raise DetailShapeError("UNVERIFIED_REOFFER_SHAPE")
    problems, grouped = [], {}
    warnings = []
    rows = payload.get("contractorsResult")
    rows = [] if rows is None else rows
    if not isinstance(rows, list):
        raise DetailShapeError("OFFERORS_NOT_LIST")
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            problems.append(f"OFFEROR_{i}_INVALID")
            continue
        key = row.get("contractorCode") or "unnamed-row-" + str(i)
        grouped.setdefault(key, []).append(row)
    members = []
    members_by_code = {}
    for code, originals in grouped.items():
        raw = {"contractorCode": code, "source_rows": originals}
        name = _text(originals[0], ("contractorName",))
        if not name:
            problems.append("OFFEROR_NAME_MISSING")
        member = ParsedMember(
            record_object_sha(raw),
            raw,
            name or str(code),
            "MISSING",
            None,
            "OFFEROR",
            "MISSING",
            None,
            len(members) + 1,
        )
        members.append(member)
        members_by_code[code] = (member, originals)
    money = []
    if lane == "REOFFER":
        # DETAIL parses the same endpoint/body; only REOFFER writes money, so the
        # shared package price is written once instead of once per lane.
        currency = _payload_currency(payload)
        for code, (member, originals) in members_by_code.items():
            for field, role in (
                ("reofferPrice", "REOFFER_PRICE"),
                ("reofferPriceFinal", "REOFFER_PRICE_FINAL"),
            ):
                values = _distinct_amount(originals, field)
                if len(values) == 1:
                    # One contractor, one price: the row is per (contractor, lot) in
                    # the source, and several lots share one contractor. When the
                    # lots disagree the price cannot be stored under the single
                    # (OFFER, role) slot the schema allows, so it is flagged
                    # instead of silently collapsed to one lot's price.
                    amount = _money_amount(
                        {"v": next(iter(values))}, "v", role=role,
                        owner_kind="OFFER", owner_ref=member.member_object_sha,
                        source_path=f"contractorsResult[].{field}",
                        currency_code=currency,
                    )
                    if amount is not None:
                        money.append(amount)
                elif len(values) > 1:
                    # A warning, not a problem: the capture itself is fine, only
                    # the money grain cannot hold a per-lot price. Flagging it as
                    # a problem would flip otherwise good captures to PARSE_ERROR.
                    warnings.append(f"OFFEROR_{code}_{role}_VARIES_BY_LOT")
        passed = [
            row for row in (payload.get("bidoListContractorReofferPassedDTOList") or [])
            if isinstance(row, dict)
        ]
        for field, role in (("bidPrice", "BID_PRICE"), ("bidFinalPrice", "BID_FINAL_PRICE")):
            values = _distinct_amount(passed, field)
            if len(values) == 1:
                # The passed list repeats the package total on every lot row
                # (verified: identical across all rows of a capture), so the
                # package owns one row. Per-lot priceInit/priceStep are the
                # lots' own prices and already land as HSMT LOT_PRICE.
                amount = _money_amount(
                    {"v": next(iter(values))}, "v", role=role,
                    owner_kind="PACKAGE", owner_ref="",
                    source_path=f"bidoListContractorReofferPassedDTOList[].{field}",
                    currency_code=currency,
                )
                if amount is not None:
                    money.append(amount)
            elif len(values) > 1:
                warnings.append(f"PASSED_{role}_VARIES")
        for field, role in (
            ("priceInit", "PACKAGE_PRICE_INIT"),
            ("priceStep", "PACKAGE_PRICE_STEP"),
        ):
            amount = _money_amount(
                payload, field, role=role, owner_kind="PACKAGE", owner_ref="",
                source_path=field, currency_code=currency,
            )
            if amount is not None:
                money.append(amount)
        detail = payload.get("bidDetail")
        if isinstance(detail, dict):
            amount = _money_amount(
                detail, "bidPrice", role="PACKAGE_PRICE", owner_kind="PACKAGE",
                owner_ref="", source_path="bidDetail.bidPrice", currency_code=currency,
            )
            if amount is not None:
                money.append(amount)
    plan = payload.get("bidPlan")
    plan = {} if plan is None else plan
    if not isinstance(plan, dict):
        raise DetailShapeError("BID_PLAN_NOT_OBJECT")
    documents, relations = [], []
    plan_no = plan.get("planNo") or payload.get("planNo")
    if plan_no:
        relations.append(
            {
                "family": "KHLCNT",
                "key": plan_no,
                "version": plan.get("planVersion"),
                "relation": "PLAN_HAS_NOTICE",
                "path": "bidPlan.planNo / planNo",
            }
        )
    if plan.get("decisionFileId"):
        documents.append(
            {
                "document_kind": "PLAN_DECISION",
                "source_file_id": plan["decisionFileId"],
                "name": plan.get("decisionFileName") or plan["decisionFileId"],
                "owner_kind": "PLAN",
                "owner_key": plan_no,
                "owner_version": plan.get("planVersion"),
                "source_path": "bidPlan.decisionFileId",
                "download_status": "REFERENCE_ONLY",
                "download_reason": "DOWNLOAD_ENDPOINT_UNVERIFIED",
            }
        )
    version = {
        k: payload.get(k)
        for k in ("notifyId", "notifyNo", "notifyVersion", "reofferVersion", "lastModifiedDate")
    }
    return ParsedDetail(
        _json(version),
        {},
        (),
        tuple(members),
        tuple(problems),
        (),
        tuple(documents),
        tuple(relations),
        warnings=tuple(sorted(set(warnings))),
        capability="NOTICE_METADATA", parser_version=PARSER_VERSION,
        money=tuple(money),
    )


def parse_public(spec, payload):
    if not isinstance(payload, dict):
        raise DetailShapeError("DEEP_RESPONSE_NOT_OBJECT")
    return _hsmt(payload) if spec.parser_kind == "HSMT" else _reoffer(payload, spec.parser_kind)
