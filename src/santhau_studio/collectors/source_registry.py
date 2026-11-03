"""Single registry for endpoint, family, filter, version and provenance (§1.1 PORT row).

Replaces the source repo's split between `harvester/registry.py` and per-adapter
`id_fields`. One place declares what a family is, so coverage, identity and provenance
cannot disagree about the same endpoint.

Natural keys follow O-3 method A: the version field is declared separately and never
folded into natural_key. The source adapter did `doc.get("notifyVersion") or "00"` on a
path shared by all six families (egp_home.py:158, :341); that coercion is the bug this
registry exists to make impossible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from santhau_studio.collectors.extraction import FamilySpec

SOURCE_EGP_HOME = "egp_home"
SOURCE_EGP_V2 = "egp_v2"
SOURCE_MOH = "moh_quotes"
SOURCE_MOF = "mof_quotes"

EXACT_HOST_ALLOWLIST = ("muasamcong.mof.gov.vn",)

ADAPTER_VERSION = 1


@dataclass(frozen=True)
class SourceSpec:
    key: str
    display_name: str
    endpoint: str
    host: str
    requires_user_session: bool = False
    enabled_by_default: bool = True
    notes: str = ""


@dataclass(frozen=True)
class FamilyBinding:
    """One family on one source: how to fetch it and how to read what came back."""

    source_key: str
    spec: FamilySpec
    page_size: int
    max_pages: int
    max_records: int
    date_filter_field: str | None
    access_state: str = "UNVERIFIED"  # §6 vocabulary; UNVERIFIED until a canary proves it

    def __post_init__(self) -> None:
        if self.page_size <= 0:
            raise ValueError(f"{self.spec.family}: page_size must be positive")
        if self.max_pages <= 0 or self.max_records <= 0:
            raise ValueError(f"{self.spec.family}: fetch caps must be positive")


def _project(title_from: tuple[str, ...], keep: tuple[str, ...]):
    """Project a source document onto the fields the product actually reads.

    The names come from the source adapter's own normalizers, verified at
    `harvester/adapters/egp_home.py` @ aa65f0a, not from the spec's prose. `title` is
    synthesised because the serving FTS (UI-06) and the record list read one title column
    across six families whose source fields differ (`bidName` / `name` / `pname` / `ctName`).
    """

    def normalize(record: dict) -> dict:
        out: dict = {name: record.get(name) for name in keep if name in record}
        out["title"] = next(
            (
                record[candidate].strip()
                for candidate in title_from
                if isinstance(record.get(candidate), str) and record[candidate].strip()
            ),
            None,
        )
        return out

    return normalize


# (es_type, case_khkq, title fields, kept fields) per family — the request filter and the
# readable projection for each of the six families that share one ES envelope.
_FAMILY_SHAPE: dict[str, tuple[str, str | None, tuple[str, ...], tuple[str, ...]]] = {
    "TBMT": (
        "es-notify-contractor",
        "not1",
        ("bidName", "notifyNoStand"),
        ("notifyNo", "notifyNoStand", "notifyVersion", "investorName", "investorCode",
         "procuringEntityName", "publicDate", "bidCloseDate", "status", "bidForm", "bidMode"),
    ),
    "KQLCNT": (
        "es-notify-contractor",
        "1",
        ("bidName", "name", "pname"),
        ("notifyNo", "notifyVersion", "inputResultId", "investorName", "winningContractorName",
         "contractorName", "winningCode", "bidWinningPrice", "bidPrice", "decisionDate",
         "numBidderJoin", "numBidderTech", "publicDateKqlcnt", "planNo", "statusForNotify"),
    ),
    "HOP_DONG": (
        "es-ct-contract",
        None,
        ("bidName", "ctName", "name"),
        ("contractCode", "contractVersion", "contractNo", "notifyNo", "contractSignDate",
         "ctPriceAfter", "winningContractorName", "winningCode", "isDomestic", "investorName"),
    ),
    "KHLCNT": (
        "es-plan-project-p",
        None,
        ("name", "pname", "bidName"),
        ("planNo", "planVersion", "investorName", "bidPrice", "investTotal", "decisionDate",
         "approvalStatus", "haveBidNotNotify", "planType"),
    ),
    "DU_AN": (
        "es-bidp-project-p",
        None,
        ("name", "pname"),
        ("pno", "pversion", "pname", "investorName", "investTotal", "decisionDate", "pgroup", "location"),
    ),
    "YCBG": (
        "es-ycbg",
        None,
        ("bidName", "name", "pname"),
        ("notifyNo", "notifyVersion", "investorName", "decisionDate", "bidOpenDate"),
    ),
}

_VERSION_FIELDS = {
    "TBMT": ("notifyNo", "notifyVersion"),
    "KQLCNT": ("notifyNo", "notifyVersion"),
    "HOP_DONG": ("contractCode", "contractVersion"),
    "KHLCNT": ("planNo", "planVersion"),
    "DU_AN": ("pno", "pversion"),
    "YCBG": ("notifyNo", "notifyVersion"),
}


def _spec(family: str) -> FamilySpec:
    es_type, case_khkq, title_from, keep = _FAMILY_SHAPE[family]
    del es_type, case_khkq  # request-side; see search_shape_for()
    natural, version = _VERSION_FIELDS[family]
    return FamilySpec(
        family=family,
        content_path="/page/content",
        natural_key_field=natural,
        version_field=version,
        normalize=_project(title_from, keep),
    )


@dataclass(frozen=True)
class SearchShape:
    """The request-side filter that selects one family inside the shared ES index."""

    es_type: str
    case_khkq: str | None
    title_fields: tuple[str, ...]

    @property
    def known_fields(self) -> tuple[str, ...]:
        return self.title_fields


def search_shape_for(family: str) -> SearchShape:
    es_type, case_khkq, title_fields, _keep = _FAMILY_SHAPE[family]
    return SearchShape(es_type=es_type, case_khkq=case_khkq, title_fields=title_fields)

SOURCES: dict[str, SourceSpec] = {
    SOURCE_EGP_HOME: SourceSpec(
        key=SOURCE_EGP_HOME,
        display_name="Mua sắm công — trang tìm kiếm công khai",
        endpoint="/o/egp-portal-home/services/smart/search",
        host="muasamcong.mof.gov.vn",
        notes="Read-only public home search. Six families share this envelope.",
    ),
    SOURCE_EGP_V2: SourceSpec(
        key=SOURCE_EGP_V2,
        display_name="Mua sắm công — đường phiên trình duyệt",
        endpoint="/o/egp/v2/search",
        host="muasamcong.mof.gov.vn",
        requires_user_session=True,
        enabled_by_default=False,
        notes="Needs a browser session (reCAPTCHA v3). Never bypassed; blocked until header auth is proven (B7).",
    ),
    SOURCE_MOH: SourceSpec(
        key=SOURCE_MOH,
        display_name="Bộ Y tế — mời chào giá trang thiết bị y tế",
        endpoint="/moh/quotes",
        host="congmua.syt.gov.vn",
        enabled_by_default=False,
        notes="Quarantined: source repo recorded API reset from this machine on 2026-10-31.",
    ),
    SOURCE_MOF: SourceSpec(
        key=SOURCE_MOF,
        display_name="Bộ Tài chính — cung cấp báo giá",
        endpoint="/mof/quotes",
        host="mof.gov.vn",
        enabled_by_default=False,
        notes="Quarantined until a verified canary proves access.",
    ),
}

DEFAULT_PACING_SECONDS = 3.0  # §6: host-scoped minimum for EGP, retries included

FAMILY_ORDER = ("TBMT", "KQLCNT", "HOP_DONG", "KHLCNT", "DU_AN", "YCBG")

BINDINGS: dict[str, FamilyBinding] = {}


def _register(binding: FamilyBinding) -> None:
    if binding.spec.family in BINDINGS:
        raise ValueError(f"duplicate family {binding.spec.family}")
    BINDINGS[binding.spec.family] = binding


for _family in FAMILY_ORDER:
    _register(
        FamilyBinding(
            source_key=SOURCE_EGP_HOME,
            spec=_spec(_family),
            page_size=10,  # portal clamp: pageSize > 10 is refused with HTTP 400
            max_pages=450,
            max_records=4000,
            date_filter_field="publicDate",
        )
    )
del _family


def binding_for(family: str) -> FamilyBinding:
    try:
        return BINDINGS[family]
    except KeyError as exc:
        raise KeyError(f"family {family!r} not in registry; known: {sorted(BINDINGS)}") from exc


def source_for(family: str) -> SourceSpec:
    return SOURCES[binding_for(family).source_key]


def families_on(source_key: str) -> tuple[str, ...]:
    return tuple(f for f in FAMILY_ORDER if BINDINGS[f].source_key == source_key)


def is_allowed_host(host: str) -> bool:
    """Exact host match. §6 forbids proxy rotation and IP ranges, so no suffix tricks."""
    return host.lower() in EXACT_HOST_ALLOWLIST


@dataclass(frozen=True)
class Provenance:
    """Written into every response-derived row so a claim can be traced to an adapter."""

    source_key: str
    family: str
    endpoint: str
    adapter_sha: str
    adapter_version: int = ADAPTER_VERSION
    extra: dict[str, object] = field(default_factory=dict)

    def as_json_dict(self) -> dict[str, object]:
        return {
            "source_key": self.source_key,
            "family": self.family,
            "endpoint": self.endpoint,
            "adapter_sha": self.adapter_sha,
            "adapter_version": self.adapter_version,
            **self.extra,
        }
