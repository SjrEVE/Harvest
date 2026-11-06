"""Deep-lane contracts and detail parsing (§10.4, D-3).

A ``FULL_RESPONSE`` capture with a valid hash proves completeness *at capture time* — never
the right to skip forever. It must refresh when the source version moves (FAULT-17), when
``refresh_after_days`` elapses, or when RECONCILE finds drift; ``storage/deep.py`` owns that
decision, this module declares what a capture carries and how it is parsed.

Multiplicity never collapses: several contractors, several prices, multi-currency and a
missing tax basis are preserved verbatim — picking "the first element" is the defect the
unwrap rules here exist to make impossible. Field paths, member field names and HSMT columns
are declared per lane and **not locked** until a canary proves the endpoint (D-3); additive
changes only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from santhau_studio.collectors.extraction import _descend
from santhau_studio.domain.identity import record_object_sha

LANE_DETAIL = "DETAIL"
LANE_HSMT = "HSMT"
LANE_REOFFER = "REOFFER"
LANE_KQLCNT_FULL = "KQLCNT_FULL"
LANE_BOQ = "BOQ"
LANE_OTHER = "OTHER"

PRESENCE_KNOWN = "KNOWN"
PRESENCE_MISSING = "MISSING"
PRESENCE_UNPARSABLE = "UNPARSABLE"

DEFAULT_REFRESH_DAYS = 30


class DetailShapeError(ValueError):
    """The payload is not even the shape the lane declared; no derived row is safe."""


@dataclass(frozen=True)
class DeepResponse:
    """Original response plus its explicit table projection, both retained by the writer."""

    response_payload: Any
    detail: dict[str, Any]


@dataclass(frozen=True)
class DeepLaneSpec:
    """One deep lane on one family: where to look and what may be read.

    Paths and field names are *declared, not discovered*: an unverified path yields no
    derived rows rather than a guess. ``hsmt_columns`` stays empty until the canary locks
    the 14-column contract (D-3).
    """

    family: str
    lane: str
    endpoint_kind: str
    version_fields: tuple[str, ...]
    members_path: str | None = None
    member_role: str = "MEMBER"
    member_name_fields: tuple[str, ...] = ()
    member_tax_fields: tuple[str, ...] = ()
    member_share_fields: tuple[str, ...] = ()
    lots_path: str | None = None
    lot_no_field: str = "lotNo"
    lot_label_fields: tuple[str, ...] = ()
    lot_version_fields: tuple[str, ...] = ()
    goods_path: str | None = None
    goods_name_fields: tuple[str, ...] = ("name",)
    goods_spec_fields: tuple[str, ...] = ()
    goods_unit_fields: tuple[str, ...] = ()
    goods_quantity_fields: tuple[str, ...] = ()
    hsmt_columns: tuple[str, ...] = ()
    requires_session: bool = False
    refresh_after_days: int = DEFAULT_REFRESH_DAYS
    parser_kind: str | None = None
    parser_version: int = 1


@dataclass(frozen=True)
class ParsedGoodsRow:
    row_ordinal: int
    name: str | None
    specification: str | None
    unit: str | None
    quantity_presence: str
    quantity: float | None
    quantity_raw: str | None
    raw: Any = None


@dataclass(frozen=True)
class ParsedLot:
    lot_no_raw: str
    lot_label: str | None
    lot_version: str | None
    goods: tuple[ParsedGoodsRow, ...]
    raw: Any = None


@dataclass(frozen=True)
class ParsedMember:
    member_object_sha: str
    raw: dict[str, Any]
    party_name: str
    party_tax_id_presence: str
    party_tax_id: str | None
    role: str
    membership_share_presence: str
    membership_share: float | None
    ranking: int


@dataclass(frozen=True)
class ParsedDetail:
    source_version_json: str
    hsmt: dict[str, Any]
    lots: tuple[ParsedLot, ...]
    members: tuple[ParsedMember, ...]
    problems: tuple[str, ...] = ()
    unallotted_goods: tuple[ParsedGoodsRow, ...] = ()
    documents: tuple[dict, ...] = ()
    relations: tuple[dict, ...] = ()
    warnings: tuple[str, ...] = ()
    capability: str = "UNKNOWN"
    parser_version: int = 1


def _scalar(value: Any) -> Any:
    """Unwrap the portal's single-element list convention; a true multi-value stays a list."""
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def _text(record: dict[str, Any], fields: tuple[str, ...]) -> str | None:
    for name in fields:
        value = _scalar(record.get(name))
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return None


def _presence_text(record: dict[str, Any], fields: tuple[str, ...]) -> tuple[str, str | None]:
    for name in fields:
        value = _scalar(record.get(name))
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if isinstance(value, (dict, list, bool)):
            return PRESENCE_UNPARSABLE, None
        return PRESENCE_KNOWN, str(value).strip()
    return PRESENCE_MISSING, None


def _presence_number(record: dict[str, Any], fields: tuple[str, ...]) -> tuple[str, float | None, str | None]:
    for name in fields:
        value = _scalar(record.get(name))
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if isinstance(value, (bool, dict, list)):
            return PRESENCE_UNPARSABLE, None, json.dumps(value, ensure_ascii=False)
        if isinstance(value, (int, float)):
            return PRESENCE_KNOWN, float(value), None
        text = str(value).strip()
        try:
            return PRESENCE_KNOWN, float(text), None
        except ValueError:
            # Ambiguous formats (thousand separators, decimal commas, multi-currency
            # strings) stay unparsed with the raw text kept — never a guessed number.
            return PRESENCE_UNPARSABLE, None, text
    return PRESENCE_MISSING, None, None


def source_version_json(spec: DeepLaneSpec, detail: dict[str, Any]) -> str:
    """Canonical JSON of the declared version fields, multi-values kept verbatim (FAULT-17)."""
    payload = {name: _scalar(detail[name]) for name in spec.version_fields if name in detail}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _members(spec: DeepLaneSpec, detail: dict[str, Any], problems: list[str]) -> tuple[ParsedMember, ...]:
    if spec.members_path is None:
        return ()
    raw_members = _descend(detail, spec.members_path)
    if raw_members is None:
        return ()
    if not isinstance(raw_members, list):
        raise DetailShapeError(f"members at {spec.members_path} are not a list")
    parsed: list[ParsedMember] = []
    for index, member in enumerate(raw_members):
        if not isinstance(member, dict):
            problems.append(f"MEMBER_{index}_NOT_OBJECT")
            continue
        name = _text(member, spec.member_name_fields)
        if name is None:
            problems.append(f"MEMBER_{index}_UNNAMED")
            continue
        tax_presence, tax = _presence_text(member, spec.member_tax_fields)
        share_presence, share, _raw = _presence_number(member, spec.member_share_fields)
        parsed.append(
            ParsedMember(
                member_object_sha=record_object_sha(member),
                raw=member,
                party_name=name,
                party_tax_id_presence=tax_presence,
                party_tax_id=tax,
                role=spec.member_role,
                membership_share_presence=share_presence,
                membership_share=share,
                ranking=index + 1,
            )
        )
    return tuple(parsed)


def _goods(spec: DeepLaneSpec, lot: dict[str, Any], lot_index: int, problems: list[str]) -> tuple[ParsedGoodsRow, ...]:
    if spec.goods_path is None:
        return ()
    rows = _descend(lot, spec.goods_path)
    if rows is None:
        return ()
    if not isinstance(rows, list):
        problems.append(f"LOT_{lot_index}_GOODS_NOT_LIST")
        return ()
    parsed: list[ParsedGoodsRow] = []
    for ordinal, row in enumerate(rows):
        if not isinstance(row, dict):
            problems.append(f"LOT_{lot_index}_ROW_{ordinal}_NOT_OBJECT")
            continue
        presence, quantity, raw = _presence_number(row, spec.goods_quantity_fields)
        parsed.append(
            ParsedGoodsRow(
                row_ordinal=ordinal,
                name=_text(row, spec.goods_name_fields),
                specification=_text(row, spec.goods_spec_fields),
                unit=_text(row, spec.goods_unit_fields),
                quantity_presence=presence,
                quantity=quantity,
                quantity_raw=raw,
            )
        )
    return tuple(parsed)


def _lots(spec: DeepLaneSpec, detail: dict[str, Any], problems: list[str]) -> tuple[ParsedLot, ...]:
    if spec.lots_path is None:
        return ()
    raw_lots = _descend(detail, spec.lots_path)
    if raw_lots is None:
        return ()
    if not isinstance(raw_lots, list):
        raise DetailShapeError(f"lots at {spec.lots_path} are not a list")
    parsed: list[ParsedLot] = []
    for index, lot in enumerate(raw_lots):
        if not isinstance(lot, dict):
            problems.append(f"LOT_{index}_NOT_OBJECT")
            continue
        lot_no = _text(lot, (spec.lot_no_field,))
        if lot_no is None:
            problems.append(f"LOT_{index}_NO_NUMBER")
            continue
        parsed.append(
            ParsedLot(
                lot_no_raw=lot_no,
                lot_label=_text(lot, spec.lot_label_fields),
                lot_version=_text(lot, spec.lot_version_fields),
                goods=_goods(spec, lot, index, problems),
            )
        )
    return tuple(parsed)


def parse_detail(spec: DeepLaneSpec, detail: Any) -> ParsedDetail:
    """Parse one detail payload against the lane spec. Member/lot damage becomes a problem,
    never a silent drop; envelope damage raises."""
    if spec.parser_kind:
        from santhau_studio.collectors.cgttrg import parse_public
        return parse_public(spec, detail)
    if not isinstance(detail, dict):
        raise DetailShapeError(f"detail payload is {type(detail).__name__}, not an object")
    problems: list[str] = []
    return ParsedDetail(
        source_version_json=source_version_json(spec, detail),
        hsmt={column: detail[column] for column in spec.hsmt_columns if column in detail},
        lots=_lots(spec, detail, problems),
        members=_members(spec, detail, problems),
        problems=tuple(problems),
    )


# The version fields are grounded in the listing contract (source_registry): the detail of a
# family carries the same version axis its listing rows do, plus the decision/result version
# the deep render exposes. Member/lot paths stay undeclared until the canary (D-3).
_DETAIL_VERSION_FIELDS: dict[str, tuple[str, ...]] = {
    "TBMT": ("notifyVersion", "decisionVersion"),
    "KQLCNT": ("notifyVersion", "resultVersion"),
    "HOP_DONG": ("contractVersion",),
    "KHLCNT": ("planVersion",),
    "DU_AN": ("pversion",),
    "YCBG": ("notifyVersion",),
}


def detail_spec(family: str) -> DeepLaneSpec:
    """The working DETAIL lane for a family. Session-gated and path-locked until canaried."""
    return DeepLaneSpec(
        family=family,
        lane=LANE_DETAIL,
        endpoint_kind="detail-v2",
        version_fields=_DETAIL_VERSION_FIELDS[family],
        requires_session=True,
    )


def hsmt_spec(family: str) -> DeepLaneSpec:
    return DeepLaneSpec(
        family=family,
        lane=LANE_HSMT,
        endpoint_kind="hsmt-v2",
        version_fields=_DETAIL_VERSION_FIELDS[family],
        requires_session=True,
    )


def reoffer_spec(family: str) -> DeepLaneSpec:
    """Reoffer history is a reported snapshot only — never merged into identity (§10.4)."""
    return DeepLaneSpec(
        family=family,
        lane=LANE_REOFFER,
        endpoint_kind="reoffer-v2",
        version_fields=_DETAIL_VERSION_FIELDS[family],
        requires_session=True,
    )
