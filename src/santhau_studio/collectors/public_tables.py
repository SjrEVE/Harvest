"""Observed CGTTRG web tables; no automatic network access or coverage promotion.

The RC3 capture contains flat lot headers with isParent=False. Relations come from
id/parent, never that display flag. A price submission is an offer, not an award.
Only the observed form contract is supported; other forms fail closed.
"""

from __future__ import annotations

import copy
import json
import re
from uuid import UUID

from santhau_studio.collectors.deep import DeepLaneSpec, DeepResponse, DetailShapeError

HSMT_ENDPOINT = "/o/egp-portal-contractor-selection-v2/services/lcnt_tbmcgtt_hsmt"
REOFFER_ENDPOINT = "/o/egp-portal-contractor-selection-v2/services/online-reoffer/detail"


def _object(value, label):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise DetailShapeError(f"{label}: invalid JSON") from exc
    if not isinstance(value, dict):
        raise DetailShapeError(f"{label}: expected object")
    return value


def _rows(value, label):
    if not isinstance(value, list) or not value or not all(isinstance(r, dict) for r in value):
        raise DetailShapeError(f"{label}: expected non-empty object array")
    return value


def _identity(value, notify_no, version):
    if not notify_no or not version or value.get("notifyNo") != notify_no or value.get("notifyVersion") != version:
        raise DetailShapeError("TABLE_SOURCE_IDENTITY_MISMATCH")


def normalize_hsmt(payload, *, notify_no, version):
    payload = _object(payload, "HSMT")
    forms = _rows(payload.get("bidoInvBiddingDTO"), "HSMT forms")
    lots = []
    lot_numbers = set()
    for form_index, form in enumerate(forms):
        _identity(form, notify_no, version)
        if form.get("formCode") != "BD.DT.02.1960":
            raise DetailShapeError("HSMT_FORM_NOT_VERIFIED")
        table = _rows(_object(form.get("formValue"), "formValue").get("Table"), "goods table")
        parents = {}
        for row_index, row in enumerate(table):
            if row.get("children"):
                raise DetailShapeError("HSMT_NESTED_ROWS_NOT_VERIFIED")
            if row.get("lotNo"):
                parent_id = str(row.get("id"))
                lot_no = str(row["lotNo"])
                if row.get("id") is None or parent_id in parents or lot_no in lot_numbers or row.get("name"):
                    raise DetailShapeError("HSMT_AMBIGUOUS_LOT_HEADER")
                lot = {"lotNo": lot_no, "lotName": row.get("lotName"), "goods": [],
                       "source_form_index": form_index, "source_row_index": row_index}
                parents[parent_id] = lot
                lot_numbers.add(lot_no)
                lots.append(lot)
        goods_ids = set()
        for row_index, row in enumerate(table):
            if row.get("lotNo"):
                continue
            parent = parents.get(str(row.get("parent")))
            if parent is None or not isinstance(row.get("name"), str) or not row["name"].strip():
                raise DetailShapeError("HSMT_ORPHAN_OR_UNNAMED_GOODS")
            if row.get("id") is not None:
                key = str(row["id"])
                if key in goods_ids:
                    raise DetailShapeError("HSMT_DUPLICATE_GOODS_ID")
                goods_ids.add(key)
            item = copy.deepcopy(row)
            item["source_form_index"] = form_index
            item["source_row_index"] = row_index
            parent["goods"].append(item)
        if not parents or any(not lot["goods"] for lot in parents.values()):
            raise DetailShapeError("HSMT_EMPTY_LOT")
    return {"notifyNo": notify_no, "notifyVersion": version, "lots": lots,
            "forms": copy.deepcopy(forms)}


def normalize_reoffer(payload, *, notify_no, version, notify_id):
    payload = _object(payload, "reoffer")
    _identity(payload, notify_no, version)
    try:
        valid_id = UUID(notify_id).int != 0
    except (ValueError, TypeError, AttributeError):
        valid_id = False
    if not valid_id or payload.get("notifyId") != notify_id or payload.get("bidForm") != "CGTTRG":
        raise DetailShapeError("REOFFER_SOURCE_IDENTITY_MISMATCH")
    source_quotes = _rows(payload.get("contractorsResult"), "reoffer results")
    quotes, members, seen = [], {}, set()
    for index, quote in enumerate(source_quotes):
        code, name, lot_no = (quote.get(k) for k in ("contractorCode", "contractorName", "lotNo"))
        if not all(isinstance(v, str) and v.strip() for v in (code, name, lot_no)):
            raise DetailShapeError("REOFFER_MISSING_PARTY_OR_LOT")
        party = (quote.get("ventureCode"), code)
        key = (*party, lot_no)
        if key in seen:
            raise DetailShapeError("REOFFER_DUPLICATE_CURRENT_QUOTE")
        seen.add(key)
        table = _rows(_object(quote.get("formValue"), "quote formValue").get("Table"), "quote goods")
        goods, summaries = [], []
        for row in table:
            if isinstance(row.get("name"), str) and row["name"].strip():
                goods.append(row)
            elif row.get("lotNo") == lot_no:
                summaries.append(row)
            else:
                raise DetailShapeError("REOFFER_UNNAMED_GOODS")
        if not goods or len(summaries) > 1:
            raise DetailShapeError("REOFFER_AMBIGUOUS_LOT_TOTAL")
        normalized = copy.deepcopy(quote)
        normalized["goods"] = copy.deepcopy(goods)
        normalized["lot_summaries"] = copy.deepcopy(summaries)
        normalized["source_quote_index"] = index
        quotes.append(normalized)
        if party not in members:
            tax = code[2:] if re.fullmatch(r"vn(?:\d{10}|\d{13})", code) else None
            members[party] = {"contractorCode": code, "contractorName": name, "taxCode": tax,
                              "ventureCode": quote.get("ventureCode"), "quotes": []}
        elif members[party]["contractorName"] != name:
            raise DetailShapeError("REOFFER_PARTY_NAME_CONFLICT")
        members[party]["quotes"].append(copy.deepcopy(normalized))
    return {"notifyNo": notify_no, "notifyVersion": version, "notifyId": notify_id,
            "members": list(members.values()), "quotes": quotes}


def table_spec(lane):
    common = {"family": "TBMT", "lane": lane, "version_fields": ("notifyVersion",)}
    if lane == "HSMT":
        return DeepLaneSpec(**common, endpoint_kind="egp-public-cgtt-hsmt-v1",
                            lots_path="/lots", lot_label_fields=("lotName",), goods_path="/goods",
                            goods_name_fields=("name",), goods_spec_fields=("specification",),
                            goods_unit_fields=("uom",), goods_quantity_fields=("qty",),
                            hsmt_columns=("forms",))
    if lane == "REOFFER":
        return DeepLaneSpec(**common, endpoint_kind="egp-public-cgtt-reoffer-v1",
                            members_path="/members", member_role="OFFEROR",
                            member_name_fields=("contractorName",), member_tax_fields=("taxCode",),
                            hsmt_columns=("quotes",))
    raise DetailShapeError("PUBLIC_TABLE_LANE_NOT_VERIFIED")


def canary_table_fetcher(lane, targets, *, budget, lane_factory=None):
    """Explicit targets for an owner-approved canary; never installed as a global lane.

    The caller owns approval and the shared batch budget. Validation happens before
    opening a socket. Each capture performs at most one attempt, without a session.
    """
    from santhau_studio.collectors.http_client import FetchLane
    from santhau_studio.collectors.network_policy import RetryPolicy

    table_spec(lane)
    if not isinstance(targets, dict) or not 1 <= len(targets) <= 3:
        raise DetailShapeError("PUBLIC_TABLE_EXPLICIT_TARGETS_REQUIRED")
    approved = copy.deepcopy(targets)
    for notice, target in approved.items():
        try:
            valid = bool(notice and target["version"] and UUID(target["notify_id"]).int)
        except (KeyError, ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise DetailShapeError("PUBLIC_TABLE_TARGET_IDENTITY_INVALID")
    endpoint = HSMT_ENDPOINT if lane == "HSMT" else REOFFER_ENDPOINT

    def fetch(family, natural_key):
        if family != "TBMT" or natural_key not in approved:
            raise DetailShapeError("PUBLIC_TABLE_TARGET_NOT_APPROVED")
        target = approved[natural_key]
        body = {"id": target["notify_id"]}
        if lane == "HSMT":
            body["processApply"] = "LDT"
        with (lane_factory or FetchLane)(url="https://muasamcong.mof.gov.vn" + endpoint,
                                        source_key="egp_home", budget=budget,
                                        retry=RetryPolicy(max_attempts=1)) as client:
            raw = client.post(body).payload
        if lane == "HSMT":
            detail = normalize_hsmt(raw, notify_no=natural_key, version=target["version"])
        else:
            detail = normalize_reoffer(raw, notify_no=natural_key, version=target["version"],
                                       notify_id=target["notify_id"])
        return DeepResponse(raw, detail)

    return fetch
