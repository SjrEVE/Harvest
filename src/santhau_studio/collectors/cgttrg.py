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
    _presence_number,
    _text,
)
from santhau_studio.domain.identity import record_object_sha

PREFIX = "https://muasamcong.mof.gov.vn/o/egp-portal-contractor-selection-v2/services/"
ENDPOINTS = {
    "HSMT": "lcnt_tbmcgtt_hsmt",
    "DETAIL": "online-reoffer/detail",
    "REOFFER": "online-reoffer/detail",
}


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
    )


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hsmt(payload):
    forms = payload.get("bidoInvBiddingDTO")
    if "bidoInvBiddingDTO" not in payload or (forms is not None and not isinstance(forms, list)):
        raise DetailShapeError("HSMT_FORMS_MISSING_OR_INVALID")
    lots, unallotted, problems, versions, schemas = [], [], [], [], []
    ordinal = 0
    for fi, form in enumerate(forms or []):
        if not isinstance(form, dict) or form.get("bidForm") != "CGTTRG":
            raise DetailShapeError("UNVERIFIED_HSMT_FORM")
        versions.append(
            {
                k: form.get(k)
                for k in ("notifyId", "notifyNo", "notifyVersion", "formCode", "lastModifiedDate")
            }
        )
        schemas.append({k: v for k, v in form.items() if k != "formValue"})
        try:
            data = (
                json.loads(form["formValue"])
                if isinstance(form.get("formValue"), str)
                else form.get("formValue")
            )
        except ValueError as exc:
            raise DetailShapeError(f"FORM_{fi}_INVALID_JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("Table"), list):
            problems.append(f"FORM_{fi}_TABLE_MISSING_OR_INVALID")
            continue
        headers = {}
        for i, row in enumerate(data["Table"]):
            if (
                isinstance(row, dict)
                and row.get("lotNo")
                and row.get("lotName")
                and not row.get("name")
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
                problems.append(f"FORM_{fi}_ROW_{i}_NOT_OBJECT")
                row = {}
            presence, qty, qty_raw = _presence_number(row, ("qty",))
            if isinstance(row.get("qty"), str) and re.fullmatch(
                r"[+-]?\d{1,3}(?:[.,]\d{3})+", row["qty"].strip()
            ):
                presence, qty, qty_raw = "UNPARSABLE", None, row["qty"]
            if qty is not None and not math.isfinite(qty):
                presence, qty, qty_raw = "UNPARSABLE", None, str(row.get("qty"))
            if presence != "KNOWN" or not row.get("name"):
                problems.append(f"FORM_{fi}_ROW_{i}_INVALID_FIELDS")
            good = ParsedGoodsRow(
                ordinal,
                _text(row, ("name",)),
                _text(row, ("specification",)),
                _text(row, ("uom",)),
                presence,
                qty,
                qty_raw,
                raw,
            )
            ordinal += 1
            parent = row.get("parent")
            if str(parent) in headers:
                headers[str(parent)][1].append(good)
            else:
                if parent not in (None, 0, "0", ""):
                    problems.append(f"FORM_{fi}_ROW_{i}_UNKNOWN_PARENT_{parent}")
                unallotted.append(good)
        for row, goods, _ in headers.values():
            lots.append(
                ParsedLot(
                    str(row["lotNo"]), row["lotName"], form.get("notifyVersion"), tuple(goods), row
                )
            )
    return ParsedDetail(
        _json({"forms": versions}),
        {"forms": schemas},
        tuple(lots),
        (),
        tuple(problems),
        tuple(unallotted),
    )


def _reoffer(payload):
    if payload.get("bidForm") != "CGTTRG" or not payload.get("notifyNo"):
        raise DetailShapeError("UNVERIFIED_REOFFER_SHAPE")
    problems, grouped = [], {}
    rows = payload.get("contractorsResult") or []
    if not isinstance(rows, list):
        raise DetailShapeError("OFFERORS_NOT_LIST")
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            problems.append(f"OFFEROR_{i}_INVALID")
            continue
        key = row.get("contractorCode") or "unnamed-row-" + str(i)
        grouped.setdefault(key, []).append(row)
    members = []
    for code, originals in grouped.items():
        raw = {"contractorCode": code, "source_rows": originals}
        name = _text(originals[0], ("contractorName",))
        if not name:
            problems.append("OFFEROR_NAME_MISSING")
        members.append(
            ParsedMember(
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
        )
    plan = payload.get("bidPlan") or {}
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
    )


def parse_public(spec, payload):
    if not isinstance(payload, dict):
        raise DetailShapeError("DEEP_RESPONSE_NOT_OBJECT")
    return _hsmt(payload) if spec.parser_kind == "HSMT" else _reoffer(payload)
