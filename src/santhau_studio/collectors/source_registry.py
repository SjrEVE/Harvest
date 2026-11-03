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


def _keep(*names: str):
    def normalize(record: dict) -> dict:
        return {name: record.get(name) for name in names if name in record}

    return normalize


_TBMT = FamilySpec(
    family="TBMT",
    content_path="/page/content",
    natural_key_field="notifyNo",
    version_field="notifyVersion",
    normalize=_keep("notifyNo", "notifyNoStand", "title", "investorName", "planNo", "publishDate"),
)
_KQLCNT = FamilySpec(
    family="KQLCNT",
    content_path="/page/content",
    natural_key_field="notifyNo",
    version_field="notifyVersion",
    normalize=_keep("notifyNo", "inputResultId", "title", "investorName", "contractorName", "bidWinningPrice"),
)
_HOP_DONG = FamilySpec(
    family="HOP_DONG",
    content_path="/page/content",
    natural_key_field="contractCode",
    version_field="contractVersion",
    normalize=_keep("contractCode", "notifyNo", "investorName", "winningContractorName", "ctPriceAfter"),
)
_KHLCNT = FamilySpec(
    family="KHLCNT",
    content_path="/page/content",
    natural_key_field="planNo",
    version_field="planVersion",
    normalize=_keep("planNo", "planNoStand", "investorName", "bidPrice", "publishDate"),
)
_DU_AN = FamilySpec(
    family="DU_AN",
    content_path="/page/content",
    natural_key_field="pno",
    version_field="pversion",
    normalize=_keep("pno", "pname", "investorName", "ptitle"),
)
_YCBG = FamilySpec(
    family="YCBG",
    content_path="/page/content",
    natural_key_field="notifyNo",
    version_field="notifyVersion",
    normalize=_keep("notifyNo", "title", "investorName", "planNo"),
)

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


for _family, _spec in (
    ("TBMT", _TBMT),
    ("KQLCNT", _KQLCNT),
    ("HOP_DONG", _HOP_DONG),
    ("KHLCNT", _KHLCNT),
    ("DU_AN", _DU_AN),
    ("YCBG", _YCBG),
):
    _register(
        FamilyBinding(
            source_key=SOURCE_EGP_HOME,
            spec=_spec,
            page_size=10,
            max_pages=450,
            max_records=4000,
            date_filter_field="publishDate",
        )
    )
del _family, _spec


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
