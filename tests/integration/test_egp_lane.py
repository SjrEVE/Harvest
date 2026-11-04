"""e-GP home lane: request shape, pagination, terminal proof and the ported `or "00"` removal.

The filter bodies here are asserted field by field because a wrong `type` or a missing
result publication date can silently omit awards or mix pending notices into results.
"""

from __future__ import annotations

import json

import httpx
import pytest

from santhau_studio.collectors.egp_home import (
    ES_MAX_RESULT_WINDOW,
    PAGE_SIZE_LIMIT,
    EgpHomeLane,
    ResponseShapeError,
    SearchWindow,
    build_search_body,
    read_rows,
)
from santhau_studio.collectors.extraction import extract_page, verify_extraction
from santhau_studio.collectors.http_client import FetchLane
from santhau_studio.collectors.network_policy import HostRateLimiter, RetryPolicy
from santhau_studio.collectors.source_registry import (
    FAMILY_ORDER,
    binding_for,
    search_shape_for,
)
from santhau_studio.domain.identity import identity_key

URL = "https://muasamcong.mof.gov.vn/o/egp-portal-home/services/smart/search"


def filters_of(body: list[dict]) -> dict[str, dict]:
    return {f["fieldName"]: f for f in body[0]["query"][0]["filters"]}


# ------------------------------------------------------------------ request shape per family

@pytest.mark.parametrize(
    ("family", "es_type", "case_khkq"),
    [
        ("TBMT", "es-notify-contractor", "not1"),
        ("KQLCNT", "es-notify-contractor", None),
        ("HOP_DONG", "es-ct-contract", None),
        ("KHLCNT", "es-plan-project-p", None),
        ("DU_AN", "es-bidp-project-p", None),
        ("YCBG", "es-ycbg", None),
    ],
)
def test_each_family_selects_its_own_slice_of_the_shared_index(family: str, es_type: str, case_khkq: str | None) -> None:
    body = build_search_body(0, shape=search_shape_for(family))
    assert body[0]["query"][0]["index"] == "es-contractor-selection"
    assert filters_of(body)["type"]["fieldValues"] == [es_type]
    if case_khkq == "not1":
        assert filters_of(body)["caseKHKQ"] == {"fieldName": "caseKHKQ", "searchType": "not_in", "fieldValues": ["1"]}
    elif case_khkq == "1":
        assert filters_of(body)["caseKHKQ"]["searchType"] == "in"
    else:
        assert "caseKHKQ" not in filters_of(body), f"{family} must not carry a result-status filter"


def test_tbmt_and_kqlcnt_share_an_index_but_result_query_includes_both_cases() -> None:
    """Award identity is separate; having a prior TBMT must not exclude its result."""
    tbmt = filters_of(build_search_body(0, shape=search_shape_for("TBMT")))
    kqlcnt = filters_of(build_search_body(0, shape=search_shape_for("KQLCNT")))
    assert tbmt["type"] == kqlcnt["type"]
    assert tbmt["caseKHKQ"]["searchType"] == "not_in"
    assert "caseKHKQ" not in kqlcnt
    assert search_shape_for("KQLCNT").date_field == "publicDateKqlcnt"


def test_page_size_above_the_portal_clamp_is_refused_locally() -> None:
    with pytest.raises(ValueError, match="pageSize"):
        build_search_body(0, shape=search_shape_for("TBMT"), page_size=PAGE_SIZE_LIMIT + 1)


def test_date_filter_is_only_sent_when_the_source_filter_is_verified() -> None:
    unverified = filters_of(
        build_search_body(0, shape=search_shape_for("TBMT"), window=SearchWindow(date_filter_verified=False,
                                                                                  from_local="a", to_local="b"))
    )
    assert "publicDate" not in unverified, "an unverified filter turns a gap into a plausible empty day"
    verified = filters_of(
        build_search_body(0, shape=search_shape_for("TBMT"), window=SearchWindow(True, "2026-10-01 00:00:00",
                                                                                 "2026-10-02 00:00:00"))
    )
    assert verified["publicDate"]["searchType"] == "range"
    assert verified["publicDate"]["from"] == "2026-10-01T00:00:00"
    assert verified["publicDate"]["to"] == "2026-10-02T00:00:00"


def test_only_one_half_of_a_window_does_not_activate_the_filter() -> None:
    half = SearchWindow(True, from_local="2026-10-01 00:00:00", to_local=None)
    assert not half.active
    assert "publicDate" not in filters_of(build_search_body(0, shape=search_shape_for("TBMT"), window=half))


# ------------------------------------------------------------------ envelope reading

@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"page": "not-an-object"},
        {"page": {"content": {}}},
        {"page": {"content": [], "totalElements": -1}},
        {"page": {"content": [], "totalElements": "12"}},
        {"page": {"content": [], "totalElements": True}},
    ],
)
def test_unreadable_envelopes_are_shaped_errors_not_empty_pages(payload: object) -> None:
    """A page that cannot be read must never look like a page with zero rows."""
    with pytest.raises(ResponseShapeError):
        read_rows(payload)


def test_max_result_window_makes_total_elements_untrusted() -> None:
    assert read_rows({"page": {"content": [], "totalElements": ES_MAX_RESULT_WINDOW}}) == ([], ES_MAX_RESULT_WINDOW)
    body = {"page": {"content": [{"notifyNo": "A", "notifyVersion": "00"}], "totalElements": 9_999}}
    rows, total = read_rows(body)
    assert len(rows) == 1 and total == 9_999


# ------------------------------------------------------------------ pagination

class FakePortal:
    def __init__(self, pages: list[list[dict]], *, total: int | None = None) -> None:
        self.pages = pages
        self.total = total
        self.bodies: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        index = body[0]["pageNumber"]
        content = self.pages[index] if index < len(self.pages) else []
        total = self.total if self.total is not None else sum(len(p) for p in self.pages)
        return httpx.Response(200, json={"page": {"content": content, "totalElements": total}})

    def lane(self, *, max_pages: int | None = None, family: str = "TBMT") -> EgpHomeLane:
        client = httpx.Client(transport=httpx.MockTransport(self.handler))
        fetch = FetchLane(
            url=URL,
            source_key="egp_home",
            client=client,
            retry=RetryPolicy(),
            pacing_sleeper=lambda _s: None,
        )
        binding = binding_for(family)
        return EgpHomeLane(
            family=family,
            lane=fetch,
            max_pages=max_pages if max_pages is not None else binding.max_pages,
        )


def row(no: str, version: str | None = "00", title: str = "Gói thầu") -> dict:
    doc = {"notifyNo": no, "bidName": f"{title} {no}", "investorName": "Ban QLDA 1", "publicDate": "2026-10-31 09:00"}
    if version is not None:
        doc["notifyVersion"] = version
    return doc


def test_pagination_stops_at_the_first_short_page() -> None:
    portal = FakePortal([[row(f"A{i}") for i in range(10)], [row("B1")]])
    lane = portal.lane()
    pages = list(lane.iter_pages())
    assert [p.rows for p in pages] == [10, 1]
    assert [p.has_more for p in pages] == [True, False]
    assert [p.page_no for p in pages] == [0, 1]
    assert [b[0]["pageNumber"] for b in portal.bodies] == [0, 1]


def test_empty_first_page_is_terminal_and_trusted() -> None:
    portal = FakePortal([[]], total=0)
    pages = list(portal.lane().iter_pages())
    assert len(pages) == 1 and not pages[0].has_more
    assert pages[0].total_hint == 0 and pages[0].total_hint_trusted


def test_hitting_the_page_cap_reports_more_not_complete() -> None:
    """COV-15: touching max_pages is PARTIAL even when the last page looked terminal."""
    portal = FakePortal([[row(f"A{i}") for i in range(10)] for _ in range(3)] + [[]])
    pages = list(portal.lane(max_pages=2).iter_pages())
    assert [p.page_no for p in pages[:2]] == [0, 1]
    assert len(portal.bodies) == 2, "no request was made past the cap"
    terminal = pages[-1]
    assert terminal.has_more is True, "the cap must not be reported as exhaustion"
    assert terminal.payload is None and terminal.rows == 0 and terminal.attempts == 0
    assert terminal.page_no == 2, "the cap batch carries the resume cursor, not a phantom page"


def test_resume_starts_from_the_stored_page() -> None:
    portal = FakePortal([[], [row("C1")]])
    pages = list(portal.lane().iter_pages(start_page=1))
    assert pages[0].page_no == 1
    assert portal.bodies[0][0]["pageNumber"] == 1


def test_every_page_carries_the_family_filter() -> None:
    portal = FakePortal([[row("A1")], []])
    list(portal.lane(family="KQLCNT").iter_pages())
    for body in portal.bodies:
        filters = {f["fieldName"]: f for f in body[0]["query"][0]["filters"]}
        assert filters["type"]["fieldValues"] == ["es-notify-contractor"]
        assert "caseKHKQ" not in filters
        assert body[0]["sortBy"] == "publicDateKqlcnt"


def test_total_elements_at_the_es_cap_is_flagged_untrusted() -> None:
    portal = FakePortal([[row(f"A{i}") for i in range(10)]], total=ES_MAX_RESULT_WINDOW)
    page = portal.lane().fetch_page(0)
    assert page.total_hint == ES_MAX_RESULT_WINDOW
    assert not page.total_hint_trusted, "10000 means the query is truncated, not that there are exactly 10000"


# ------------------------------------------------------------------ version handling (the ported bug)

@pytest.mark.parametrize("family", FAMILY_ORDER)
def test_absent_version_is_missing_never_zero_zero(family: str) -> None:
    """egp_home.py:158 and :341 did `doc.get(version_field) or "00"`; that coercion is gone."""
    spec = binding_for(family).spec
    natural, version = spec.natural_key_field, spec.version_field
    missing = {natural: "X1"}
    explicit = {natural: "X1", version: "00"}
    blank = {natural: "X1", version: "   "}
    assert spec.version_of(missing) == ("MISSING", None)
    assert spec.version_of(blank) == ("MISSING", None)
    assert spec.version_of(explicit) == ("KNOWN", "00")
    assert spec.version_of({natural: "X1", version: "3"}) == ("KNOWN", "3")


def test_missing_and_zero_zero_do_not_collide_after_extraction() -> None:
    """The two records must stay separate identities, because one of them is a claim about absence."""
    spec = binding_for("TBMT").spec
    response = {"page": {"content": [row("IB9001", version=None), row("IB9001", version="00")]}}
    report = extract_page(response, spec)
    states = [(r.normalized.version_state, r.normalized.version_raw) for r in report.records]
    assert states == [("MISSING", None), ("KNOWN", "00")]
    computed = [
        identity_key("TBMT", r.normalized.natural_key, r.normalized.version_state, r.normalized.version_raw)
        for r in report.records
    ]
    assert len(set(computed)) == 2, "MISSING and '00' must not share an identity"


def test_non_numeric_version_is_unparsable_not_dropped() -> None:
    spec = binding_for("TBMT").spec
    response = {"page": {"content": [row("IB9002", version="v3")]}}
    record = extract_page(response, spec).records[0]
    assert record.normalized.version_state == "UNPARSABLE"
    assert record.normalized.version_raw == "v3"


def test_records_without_a_natural_key_are_unresolved_not_silently_skipped() -> None:
    spec = binding_for("TBMT").spec
    response = {"page": {"content": [{"bidName": "không có số thông báo"}]}}
    report = extract_page(response, spec)
    assert report.unresolved and report.records[0].extractable is False
    assert report.records[0].reason_unresolved == "MISSING_NATURAL_KEY"


# ------------------------------------------------------------------ registry projection

def test_title_is_projected_from_the_real_source_fields() -> None:
    """The portal has no `title` field; UI-06 and the FTS index read one, so the registry maps it."""
    spec = binding_for("TBMT").spec
    fields = spec.normalize({"notifyNo": "IB1", "bidName": "Gói xây lắp", "investorName": "Ban QLDA"})
    assert fields["title"] == "Gói xây lắp"
    assert fields["investorName"] == "Ban QLDA"
    fallback = spec.normalize({"notifyNo": "IB1", "notifyNoStand": "IB1-stand"})
    assert fallback["title"] == "IB1-stand"
    assert spec.normalize({"notifyNo": "IB1"})["title"] is None


@pytest.mark.parametrize(
    ("family", "title_field"),
    [("TBMT", "bidName"), ("KQLCNT", "bidName"), ("HOP_DONG", "ctName"), ("KHLCNT", "name"), ("DU_AN", "pname"),
     ("YCBG", "bidName")],
)
def test_every_family_yields_a_title_from_its_own_fields(family: str, title_field: str) -> None:
    spec = binding_for(family).spec
    natural = spec.natural_key_field
    fields = spec.normalize({natural: "X", title_field: f"value in {title_field}"})
    assert fields["title"] == f"value in {title_field}"


# ------------------------------------------------------------------ one lane through the writer

def test_lane_output_feeds_the_production_writer(tmp_path) -> None:
    """End to end without a socket: portal response → lane → extraction → writer rows."""
    from santhau_studio.storage.warehouse import WarehouseWriter

    data_root = tmp_path / "data"
    writer = WarehouseWriter.open(data_root, create_schema=True, role="backfill")
    try:
        portal = FakePortal([[row("IB7777", version=None), row("IB7778", version="00")]], total=2)
        lane = portal.lane()
        page = lane.fetch_page(0)
        report = extract_page(page.payload, binding_for("TBMT").spec)
        verify_extraction(page.payload, report)
        response_ref = writer.store_response(page.payload)
        attempt = writer.ensure_attempt(
            run_id="run-1", family="TBMT", endpoint="/o/egp-portal-home/services/smart/search",
            interval_from="2026-10-31T00:00:00Z", interval_to="2026-11-01T00:00:00Z",
            page_no=0, attempt_no=1, outcome="OK", response=response_ref,
            http_status=page.http_status,
        )
        outcome = writer.commit_page(
            run_id="run-1", attempt_id=attempt, response_ref=response_ref, records=report.records
        )
        assert outcome.records_new == 2
        rows = writer.conn.execute(
            "SELECT natural_key, version_state, version_raw FROM source_records ORDER BY natural_key"
        ).fetchall()
        assert [(r["natural_key"], r["version_state"], r["version_raw"]) for r in rows] == [
            ("IB7777", "MISSING", None),
            ("IB7778", "KNOWN", "00"),
        ]
    finally:
        writer.close()
        HostRateLimiter.reset_registry()
