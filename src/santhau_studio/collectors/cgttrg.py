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

PARSER_VERSION = 7

PREFIX = "https://muasamcong.mof.gov.vn/o/egp-portal-contractor-selection-v2/services/"
ENDPOINTS = {
    "HSMT": "lcnt_tbmcgtt_hsmt",
    "DETAIL": "online-reoffer/detail",
    "REOFFER": "online-reoffer/detail",
    "M1": "expose/contractor-input-result/get",
}

# Currency for lot/goods prices. HSMT payloads declare no currency, but the same
# package's REOFFER payload declares bidPriceUnit=VND on 1,679/1,679 live captures
# and its package bidPrice equals the sum of the HSMT lotPrices, so the lot prices
# are VND-denominated by same-package arithmetic, not by assumption. Tax basis
# stays UNKNOWN everywhere: no payload says whether tax is included.
VND = "VND"
TAX_UNKNOWN = "UNKNOWN"


def public_spec(family, lane):
    if lane not in ENDPOINTS:
        return None
    if lane == "M1":
        if family != "KQLCNT":
            return None
    elif family != "TBMT":
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


def _m1(payload):
    """Per-item winning prices for one KQLCNT result (parser v7).

    Response envelope (canaried 2026-11-13 on two live records, IB2600073112
    and IB2600363545): ``{bideContractorInputResultDTO, bidpPlanDetailDTO}``.
    The DTO carries ``lotResultDTO[]`` (one entry per lot: lotNo, lotName,
    lotPrice, winningCode, contractorList with the winner's bidWiningPrice)
    and the per-item rows in SIX alternative encodings, mutually exclusive
    per lot:

    * ``lotResultDTO[].goodsList`` — JSON-encoded string, one entry per lot,
      ``formValue.lotContent.Table[]``; each item's ``bidPrice`` is the
      winning unit price. Entries repeat per bidding contractor (each entry
      carries its own ``contractorCode``; 1499707 shows the same Table under
      3 codes) — only the winner's entries are parsed, and identical Tables
      are parsed once, so a repeated table is never triple-counted.
    * ``dto.lotResultItems[]`` — one entry per (winning contractor, lot),
      linked by ``lotResultId`` to a ``lotResultDTO[].id`` (9/9 verified);
      ``formValue`` is a JSON-encoded string of items carrying the explicit
      ``lotWiningPrice`` (== ``bidPrice`` == the item's ``lotPrice`` field in
      every observed row) plus ``codeGood`` (model), ``labelGood`` (brand),
      ``origin``, ``manufacturer``, ``yearManufacture``.
    * ``lotResultDTO[].goodsList`` flat rows (live 2026-11-12, LCNT_DB/CDTRG
      lots): the entry itself is the item — name/quantity/unit/description/
      origin/``priceUnit`` with ``formValue`` null and no ``lotContent``.
      ``priceUnit`` is the winning unit price.
    * ``lotResultDTO[].goodsList`` as a JSON OBJECT of service lists (live
      2026-11-13, parser v7): ``{"listTG": [...], "listKQDR": [],
      "listTLPT": [], "listDG": [], "listTTG": []}`` — one of listTG/listDG
      is populated per record, the rest empty. Rows carry ``serviceCategory``
      (name) or ``description``, ``qty``, ``uom``, ``bidPrice``,
      ``intoMoney`` and ``orgCode`` (== the lot's winningCode in 753/753
      rows checked). Rows form a two-level tree: ``parent: 0`` rows are group
      headers whose ``intoMoney`` is the sum of their children
      (``pos`` "1.1", "1.2", ...); only the leaf rows are items — headers
      are skipped, never priced. An all-empty object carries no items.
    * ``lotResultDTO[].goodsList`` as ``{"dsDvKqTg": [...]}`` (live
      2026-11-13, parser v7): one unit per (contractor, lot) with
      ``dsHH[]`` service rows; each unit's ``contractorCode`` must equal its
      lot's winningCode (24/24 in the live record) or the unit is skipped —
      a loser's table is never emitted as winning prices. Rows attach to the
      lot their unit's ``lotNo`` names.
    * ``lotResultDTO[].goodsList`` medicine rows (live 2026-11-13, parser
      v7; DC* drug packages): the entry itself is the drug — ``tenThuoc``
      (name, fallback ``lotName``), ``quantity``, ``uom``, ``donGia`` (unit
      price), ``winContractors[0].bidWiningPrice``; qty x donGia == amount
      == the winner's per-item total in 8/8 live rows. Entries route to the
      lot their own ``lotNo`` names (1496970: 5 drug rows for 5 lots all sit
      in lot0's goodsList).

    Item names use a shared vocabulary across encodings: ``name``,
    ``serviceCategory``, ``description``, ``tenThuoc``, ``lotName`` (first
    non-empty wins); the specification is the first of ``feature``,
    ``serviceDescription``, ``technique``, ``quyCach``, ``nongDo``,
    ``dangBaoChe``, ``duongDung``, ``description`` that differs from the
    chosen name. Unit prices come from ``bidPrice``, ``priceUnit`` or
    ``donGia`` (first known wins).

    What "winning unit price" means: the per-item ``bidPrice``/``priceUnit``/
    ``donGia`` is the WINNER's OFFERED unit price (the bid-letter schedule;
    the row's ``orgCode``/entry ``contractorCode`` equals the lot's
    winningCode wherever the source attests it). qty x unit price sums to
    the winner's OFFERED lot total (``lotOpenPrice``/``adjustPrice``) when
    the table covers the whole lot — exactly in 19/22 verifiable live
    records, within source rounding (<=0.0002%) in 2 more. It does NOT
    always equal ``bidWiningPrice``: the winning total is the post-evaluation
    figure and may be lower (1499902: offered 50,849,700,000 vs winning
    47,310,000,000; 1499704: offered 3,686,323,941 vs winning 3,317,691,000),
    and some tables cover only part of the lot (1499740: listDG 1,858,566,867
    of a 4,087,152,000 lot; 1499867: Table 1,948,070,000 of a 10,152,387,319
    lot). The per-item prices are still the winner's real offered prices;
    no total is ever invented to force a match. ``ITEM_BID_PRICE`` is
    emitted only when ``bidPrice`` and ``lotWiningPrice`` are both present
    and differ — otherwise the two would be duplicate rows for the same
    price. Currency is the DTO's own ``bidPriceUnit`` (``VND`` in both
    captures); without a 3-letter code the rows become UNPARSABLE, never
    guessed. No items at all yields capability ``NOTICE_METADATA`` (the run
    marks the task SOURCE_EMPTY, not an error). Members are NOT emitted:
    contractorList role semantics are unverified.
    """
    dto = payload.get("bideContractorInputResultDTO")
    if not isinstance(dto, dict):
        raise DetailShapeError("M1_DTO_MISSING_OR_INVALID")
    currency = dto.get("bidPriceUnit")
    currency = currency.strip().upper() if isinstance(currency, str) and len(currency.strip()) == 3 else None
    lots_raw = dto.get("lotResultDTO")
    if lots_raw is None:
        lots_raw = []
    if not isinstance(lots_raw, list):
        raise DetailShapeError("M1_LOT_RESULT_NOT_LIST")
    items_raw = dto.get("lotResultItems")
    if items_raw is None:
        items_raw = []
    if not isinstance(items_raw, list):
        raise DetailShapeError("M1_LOT_RESULT_ITEMS_NOT_LIST")

    problems, warnings, money = [], [], []
    ordinal = 0

    def item_rows(row, source_path):
        """One item dict -> (ParsedGoodsRow, money rows).

        Field names differ per encoding: the Table/lotResultItems rows use
        qty/uom/feature/bidPrice, the flat goodsList rows (parser v6) use
        quantity/unit/description/priceUnit, the service-list rows (parser
        v7) use serviceCategory/serviceDescription/bidPrice, and the
        medicine rows (parser v7) use tenThuoc/quantity/uom/donGia. The
        name is the first non-empty of name/serviceCategory/description/
        tenThuoc/lotName; the specification is the first of feature/
        serviceDescription/technique/quyCach/nongDo/dangBaoChe/duongDung/
        description that differs from the chosen name (so a description
        used as the name is never duplicated into the spec, while a flat
        row's description still lands as its spec). ``priceUnit`` is the
        winning unit price in the flat encoding (verified: qty x priceUnit
        sums to the winner's bidWiningPrice); it is only consulted when
        ``bidPrice`` is absent, and the money row's source_path names the
        field actually read. ``donGia`` is the medicine rows' unit price
        (qty x donGia == amount == winContractors[0].bidWiningPrice, 8/8).
        """
        nonlocal ordinal
        name = _text(row, ("name", "serviceCategory", "description", "tenThuoc", "lotName"))
        spec = None
        for field in (
            "feature", "serviceDescription", "technique", "quyCach",
            "nongDo", "dangBaoChe", "duongDung", "description",
        ):
            candidate = _text(row, (field,))
            if candidate is not None and candidate != name:
                spec = candidate
                break
        presence, qty, qty_raw = _presence_number(row, ("qty", "quantity"))
        if presence != "KNOWN" or name is None:
            problems.append(f"M1_ITEM_{ordinal}_INVALID_FIELDS")
        good = ParsedGoodsRow(
            ordinal,
            name,
            spec,
            _text(row, ("uom", "unit")),
            presence,
            qty,
            qty_raw,
            row,
        )
        win_presence, win_value, _ = _presence_number(row, ("lotWiningPrice",))
        bid_field, bid_presence, bid_value = None, "MISSING", None
        for candidate in ("bidPrice", "priceUnit", "donGia"):
            candidate_presence, candidate_value, _ = _presence_number(row, (candidate,))
            if candidate_presence == "KNOWN":
                bid_field, bid_presence, bid_value = candidate, candidate_presence, candidate_value
                break
        if win_presence == "KNOWN":
            field, role = "lotWiningPrice", "ITEM_WINNING_PRICE"
            amount = _money_amount(
                row, field, role=role, owner_kind="GOODS_ROW",
                owner_ref=str(ordinal),
                source_path=f"{source_path}.{field}",
                currency_code=currency,
            )
            if amount is not None:
                money.append(amount)
            if bid_presence == "KNOWN" and bid_value != win_value:
                amount = _money_amount(
                    row, bid_field, role="ITEM_BID_PRICE", owner_kind="GOODS_ROW",
                    owner_ref=str(ordinal),
                    source_path=f"{source_path}.{bid_field}",
                    currency_code=currency,
                )
                if amount is not None:
                    money.append(amount)
        elif bid_presence == "KNOWN":
            amount = _money_amount(
                row, bid_field, role="ITEM_WINNING_PRICE", owner_kind="GOODS_ROW",
                owner_ref=str(ordinal),
                source_path=f"{source_path}.{bid_field}",
                currency_code=currency,
            )
            if amount is not None:
                money.append(amount)
        ordinal += 1
        return good

    def json_list(text, problem):
        try:
            value = json.loads(text)
        except ValueError:
            problems.append(problem)
            return None
        if not isinstance(value, list):
            problems.append(problem)
            return None
        return value

    # Lots first: goods attach to them by lotResultId (lotResultItems) or by
    # position (goodsList, which is already per-lot).
    lot_goods = {}
    lot_by_id = {}
    lots = []
    for lot_index, lot in enumerate(lots_raw):
        if not isinstance(lot, dict):
            problems.append(f"M1_LOT_{lot_index}_NOT_OBJECT")
            continue
        lot_no = _text(lot, ("lotNo",))
        if lot_no is None:
            problems.append(f"M1_LOT_{lot_index}_NO_NUMBER")
            continue
        if isinstance(lot.get("id"), str):
            lot_by_id[lot["id"]] = lot_no
        lot_goods[lot_no] = []
        for field, role in (
            ("lotPrice", "LOT_PRICE"),
            ("lotEstimatePrice", "LOT_ESTIMATE_PRICE"),
        ):
            amount = _money_amount(
                lot, field, role=role, owner_kind="LOT", owner_ref=lot_no,
                source_path=f"bideContractorInputResultDTO.lotResultDTO[{lot_index}].{field}",
                currency_code=currency,
            )
            if amount is not None:
                money.append(amount)
        lots.append((lot, lot_no))
    # lotResultItems encoding: explicit lotWiningPrice per item.
    for entry_index, entry in enumerate(items_raw):
        if not isinstance(entry, dict):
            problems.append(f"M1_LOT_RESULT_ITEM_{entry_index}_NOT_OBJECT")
            continue
        form_value = entry.get("formValue")
        if form_value is None:
            continue
        if not isinstance(form_value, str):
            problems.append(f"M1_LOT_RESULT_ITEM_{entry_index}_FORM_VALUE_NOT_STRING")
            continue
        items = json_list(form_value, f"M1_LOT_RESULT_ITEM_{entry_index}_INVALID_JSON")
        if items is None:
            continue
        lot_no = lot_by_id.get(entry.get("lotResultId"))
        target = lot_goods.get(lot_no) if lot_no else None
        if target is None:
            problems.append(f"M1_LOT_RESULT_ITEM_{entry_index}_UNLINKED_LOT")
        for item_index, row in enumerate(items):
            if not isinstance(row, dict):
                problems.append(f"M1_LOT_RESULT_ITEM_{entry_index}_ROW_{item_index}_NOT_OBJECT")
                continue
            good = item_rows(
                row,
                f"bideContractorInputResultDTO.lotResultItems[{entry_index}].formValue[{item_index}]",
            )
            if target is None:
                lot_goods.setdefault(None, []).append(good)
            else:
                target.append(good)
    # goodsList encoding: JSON string per lot, winner's goods table.
    # The string decodes to a LIST of entries (Table / flat / medicine
    # rows) or a DICT of service lists (parser v7). Entries carry their own
    # lotNo: an entry belongs to the lot its lotNo names, which may differ
    # from the enclosing lot (medicine rows: 1496970 keeps 5 drug rows for
    # 5 lots in lot0's goodsList). Table entries repeat per bidding
    # contractor, so only the winner's entries are parsed and identical
    # Tables are parsed once.
    lot_by_no = {lot_no: lot for lot, lot_no in lots}

    def winner_codes(target_lot):
        """The lot's winning contractor codes (winningCode may be doubled)."""
        raw = target_lot.get("winningCode") if isinstance(target_lot, dict) else None
        if not isinstance(raw, str):
            return set()
        return {code.strip() for code in raw.split(",") if code.strip()}

    def route(entry, enclosing_no):
        """Entry lotNo -> target lot_no, or None when it names no lot here."""
        number = entry.get("lotNo")
        if isinstance(number, str) and number:
            return number if number in lot_by_no else None
        return enclosing_no

    def is_medicine(entry):
        return (
            isinstance(entry.get("winContractors"), list)
            or "donGia" in entry
            or "tenThuoc" in entry
        )

    # Lots the lotResultItems encoding already populated: goodsList must
    # never double-count them. Snapshotted before the goodsList loop — the
    # check is "populated by lotResultItems", not "non-empty right now",
    # so a second goodsList entry for the same lot is not mistaken for an
    # overlap with itself.
    pre_populated = {no for no, goods in lot_goods.items() if goods}

    def goodslist_dict(decoded, lot, lot_no, lot_index):
        """goodsList decoded to a dict: service-list encoding (parser v7)."""
        winners = winner_codes(lot)
        # dsDvKqTg: one unit per (contractor, lot) with dsHH service rows.
        units = decoded.get("dsDvKqTg")
        if isinstance(units, list):
            skipped = 0
            for unit_index, unit in enumerate(units):
                if not isinstance(unit, dict):
                    problems.append(
                        f"M1_LOT_{lot_index}_GOODSLIST_DSDVKQTG_{unit_index}_NOT_OBJECT"
                    )
                    continue
                target_no = unit.get("lotNo")
                target_lot = lot_by_no.get(target_no) if isinstance(target_no, str) else None
                if target_lot is None:
                    warnings.append(f"M1_LOT_{lot_no}_GOODSLIST_DSDVKQTG_UNLINKED_LOT")
                target_winners = winner_codes(target_lot) if target_lot is not None else set()
                contractor = unit.get("contractorCode")
                if (
                    target_winners
                    and isinstance(contractor, str)
                    and contractor not in target_winners
                ):
                    # A loser's table must never be emitted as winning prices.
                    skipped += 1
                    continue
                if target_no in pre_populated:
                    warnings.append(f"M1_LOT_{target_no}_BOTH_ENCODINGS")
                    continue
                target_goods = (
                    lot_goods.setdefault(target_no, [])
                    if target_lot is not None
                    else lot_goods.setdefault(None, [])
                )
                rows = unit.get("dsHH")
                if not isinstance(rows, list):
                    continue
                for item_index, row in enumerate(rows):
                    if not isinstance(row, dict):
                        problems.append(
                            f"M1_LOT_{lot_index}_GOODSLIST_DSDVKQTG_{unit_index}_ROW_{item_index}_NOT_OBJECT"
                        )
                        continue
                    target_goods.append(item_rows(
                        row,
                        f"bideContractorInputResultDTO.lotResultDTO[{lot_index}].goodsList.dsDvKqTg[{unit_index}].dsHH[{item_index}]",
                    ))
            if skipped:
                warnings.append(
                    f"M1_LOT_{lot_no}_GOODSLIST_DSDVKQTG_NOT_WINNER_SKIPPED:{skipped}"
                )
        elif units is not None:
            problems.append(f"M1_LOT_{lot_index}_GOODSLIST_DSDVKQTG_NOT_LIST")
        # Service lists: exactly one of listTG/listDG is populated per
        # record in every live capture; the rest are empty.
        service_keys = ("listTG", "listKQDR", "listTLPT", "listDG", "listTTG")
        nonempty = [
            key for key in service_keys
            if isinstance(decoded.get(key), list) and decoded[key]
        ]
        if len(nonempty) > 1:
            warnings.append(f"M1_LOT_{lot_no}_GOODSLIST_MULTI_LISTS")
        if lot_no in pre_populated:
            # Both encodings populated for one lot: never double-count the
            # same items. lotResultItems carries the explicit lotWiningPrice,
            # so it wins; the overlap is flagged, not silently merged.
            warnings.append(f"M1_LOT_{lot_no}_BOTH_ENCODINGS")
            return
        for key in service_keys:
            rows = decoded.get(key)
            if rows is None:
                continue
            if not isinstance(rows, list):
                problems.append(f"M1_LOT_{lot_index}_GOODSLIST_{key}_NOT_LIST")
                continue
            child_parents = {
                row.get("parent") for row in rows if isinstance(row, dict)
            }
            headers = 0
            skipped = 0
            for item_index, row in enumerate(rows):
                if not isinstance(row, dict):
                    problems.append(
                        f"M1_LOT_{lot_index}_GOODSLIST_{key}_ROW_{item_index}_NOT_OBJECT"
                    )
                    continue
                row_id = row.get("id")
                if row_id is not None and row_id in child_parents:
                    # Group header: its intoMoney is the sum of its
                    # children, so pricing it would double-count.
                    headers += 1
                    continue
                org = row.get("orgCode")
                if winners and isinstance(org, str) and org not in winners:
                    skipped += 1
                    continue
                lot_goods[lot_no].append(item_rows(
                    row,
                    f"bideContractorInputResultDTO.lotResultDTO[{lot_index}].goodsList.{key}[{item_index}]",
                ))
            if headers:
                warnings.append(
                    f"M1_LOT_{lot_no}_GOODSLIST_{key}_GROUP_HEADERS_SKIPPED:{headers}"
                )
            if skipped:
                warnings.append(
                    f"M1_LOT_{lot_no}_GOODSLIST_{key}_NOT_WINNER_SKIPPED:{skipped}"
                )
        for key in decoded:
            if key not in service_keys and key != "dsDvKqTg":
                warnings.append(f"M1_LOT_{lot_no}_GOODSLIST_UNKNOWN_KEY_{key}")

    for lot_index, (lot, lot_no) in enumerate(lots):
        raw = lot.get("goodsList")
        if raw is None:
            continue
        if not isinstance(raw, str):
            problems.append(f"M1_LOT_{lot_index}_GOODSLIST_NOT_STRING")
            continue
        try:
            decoded = json.loads(raw)
        except ValueError:
            problems.append(f"M1_LOT_{lot_index}_GOODSLIST_INVALID_JSON")
            continue
        if isinstance(decoded, dict):
            goodslist_dict(decoded, lot, lot_no, lot_index)
            continue
        if not isinstance(decoded, list):
            problems.append(f"M1_LOT_{lot_index}_GOODSLIST_INVALID_JSON")
            continue
        entries = decoded
        table_entries = []
        for entry_index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                problems.append(f"M1_LOT_{lot_index}_GOODSLIST_ENTRY_{entry_index}_NOT_OBJECT")
                continue
            target_no = route(entry, lot_no)
            form_value = entry.get("formValue")
            table = form_value.get("lotContent", {}).get("Table") if isinstance(form_value, dict) else None
            if isinstance(table, list):
                table_entries.append((entry_index, entry, table, target_no))
                continue
            # Medicine encoding (live 2026-11-13, DC* drug packages): the
            # entry itself is the drug — tenThuoc/quantity/uom/donGia with
            # winContractors[0].bidWiningPrice; qty x donGia == amount ==
            # the winner's per-item total (8/8 live rows). Entries route to
            # the lot their own lotNo names.
            if is_medicine(entry):
                if target_no is None:
                    warnings.append(f"M1_LOT_{lot_no}_GOODSLIST_MEDICINE_UNLINKED_LOT")
                    target_goods = lot_goods.setdefault(None, [])
                else:
                    if target_no in pre_populated:
                        warnings.append(f"M1_LOT_{target_no}_BOTH_ENCODINGS")
                        continue
                    target_goods = lot_goods.setdefault(target_no, [])
                money_before = len(money)
                target_goods.append(item_rows(
                    entry,
                    f"bideContractorInputResultDTO.lotResultDTO[{lot_index}].goodsList[{entry_index}]",
                ))
                if len(money) == money_before:
                    warnings.append(f"M1_LOT_{target_no or lot_no}_GOODSLIST_MEDICINE_NO_ITEM_PRICE")
                continue
            # Flat encoding (live 2026-11-12, LCNT_DB/CDTRG lots): the entry
            # itself is the item row — name/quantity/unit/description/origin/
            # priceUnit with formValue null and no lotContent.Table. priceUnit
            # is the winning unit price (verified against bidWiningPrice);
            # lotResultItems is null for these records, so no fallback exists.
            # An entry with a name but no usable price is still a goods row:
            # the missing price is a warning, never a record failure.
            if _text(entry, ("name", "serviceCategory", "description", "tenThuoc", "lotName")) is not None:
                if target_no is None:
                    warnings.append(f"M1_LOT_{lot_no}_GOODSLIST_FLAT_UNLINKED_LOT")
                    target_goods = lot_goods.setdefault(None, [])
                else:
                    if target_no in pre_populated:
                        warnings.append(f"M1_LOT_{target_no}_BOTH_ENCODINGS")
                        continue
                    target_goods = lot_goods.setdefault(target_no, [])
                money_before = len(money)
                target_goods.append(item_rows(
                    entry,
                    f"bideContractorInputResultDTO.lotResultDTO[{lot_index}].goodsList[{entry_index}]",
                ))
                if len(money) == money_before:
                    warnings.append(f"M1_LOT_{target_no or lot_no}_GOODSLIST_FLAT_NO_ITEM_PRICE")
                continue
            problems.append(f"M1_LOT_{lot_index}_GOODSLIST_ENTRY_{entry_index}_NO_TABLE")
        # Table entries, grouped by target lot: only the winner's entries
        # are parsed (entries repeat per bidding contractor), and identical
        # Tables are parsed once — a repeated table is never counted twice.
        by_target = {}
        for entry_index, entry, table, target_no in table_entries:
            by_target.setdefault(target_no, []).append((entry_index, entry, table))
        for target_no, group in by_target.items():
            if target_no is None:
                warnings.append(f"M1_LOT_{lot_no}_GOODSLIST_TABLE_UNLINKED_LOT")
                target_goods = lot_goods.setdefault(None, [])
            else:
                if target_no in pre_populated:
                    warnings.append(f"M1_LOT_{target_no}_BOTH_ENCODINGS")
                    continue
                target_goods = lot_goods.setdefault(target_no, [])
            target_lot = lot_by_no.get(target_no) if target_no is not None else None
            winners = winner_codes(target_lot) if target_lot is not None else set()
            pool = group
            if winners:
                winner_group = [
                    item for item in group
                    if isinstance(item[1].get("contractorCode"), str)
                    and item[1]["contractorCode"] in winners
                ]
                if winner_group:
                    pool = winner_group
                else:
                    warnings.append(f"M1_LOT_{target_no}_GOODSLIST_NO_WINNER_ENTRY")
            seen_tables = set()
            duplicates = 0
            for entry_index, _entry, table in pool:
                key = json.dumps(table, sort_keys=True, ensure_ascii=False)
                if key in seen_tables:
                    duplicates += 1
                    continue
                seen_tables.add(key)
                for item_index, row in enumerate(table):
                    if not isinstance(row, dict):
                        problems.append(f"M1_LOT_{lot_index}_GOODSLIST_ROW_{item_index}_NOT_OBJECT")
                        continue
                    target_goods.append(item_rows(
                        row,
                        f"bideContractorInputResultDTO.lotResultDTO[{lot_index}].goodsList[{entry_index}].formValue.lotContent.Table[{item_index}]",
                    ))
            if duplicates:
                warnings.append(
                    f"M1_LOT_{target_no or lot_no}_GOODSLIST_DUPLICATE_TABLE_SKIPPED:{duplicates}"
                )
    parsed_lots = tuple(
        ParsedLot(lot_no, _text(lot, ("lotName",)), _text(dto, ("resultVersion",)),
                  tuple(lot_goods.get(lot_no, ())), lot)
        for lot, lot_no in lots
    )
    unallotted = tuple(lot_goods.get(None, ()))
    version = {
        k: dto.get(k)
        for k in ("notifyId", "notifyNo", "notifyVersion", "resultVersion", "lastModifiedDate")
    }
    total_goods = sum(len(lot.goods) for lot in parsed_lots) + len(unallotted)
    return ParsedDetail(
        _json(version),
        {},
        parsed_lots,
        (),
        tuple(problems),
        unallotted,
        warnings=tuple(sorted(set(warnings))),
        capability="STRUCTURED_ITEMS" if total_goods else "NOTICE_METADATA",
        parser_version=PARSER_VERSION,
        money=tuple(money),
    )


def parse_public(spec, payload):
    if not isinstance(payload, dict):
        raise DetailShapeError("DEEP_RESPONSE_NOT_OBJECT")
    if spec.parser_kind == "HSMT":
        return _hsmt(payload)
    if spec.parser_kind == "M1":
        return _m1(payload)
    return _reoffer(payload, spec.parser_kind)
