"""e-GP home search lane — the token-free public channel, ported to the new contract (§6, §3.4).

Ported behaviour, verified live by the source repo and recorded there: one endpoint serves six
families through an ES envelope; `pageSize` above 10 is refused with HTTP 400; the portal ignores
`sortType` (ASC and DESC page 0 return identical content), so completeness never relies on
ordering and every window is walked to exhaustion; `totalElements` saturates at the ES
`max_result_window` of 10 000 and is therefore untrustworthy at that value.

Deliberately **not** ported: `normalize_*_doc` and its `or "00"` version coercion. Field
projection lives in the registry (one place, reviewable) and version handling lives in
`extraction.FamilySpec.version_of`, which maps absent/blank to MISSING instead of inventing
version 00. That coercion was egp_home.py:158 and :341 in the source repo.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from santhau_studio.collectors.http_client import Attempt, FetchLane
from santhau_studio.collectors.source_registry import (
    FAMILY_ORDER,
    SearchShape,
    SourceSpec,
    binding_for,
    search_shape_for,
    source_for,
)

ES_INDEX = "es-contractor-selection"
PAGE_SIZE_LIMIT = 10  # portal clamp
ES_MAX_RESULT_WINDOW = 10_000  # totalElements saturates here and stops meaning anything
SORT_FIELD = "publicDate"


class ResponseShapeError(ValueError):
    """The body is JSON but not the envelope this lane knows how to read."""


@dataclass(frozen=True)
class SearchWindow:
    """Server-side publicDate range. Only sent when the filter is verified for this source.

    §6.3 forbids sending a date filter that was never proven to narrow the result set: an
    unverified filter produces a *plausible* empty day, which coverage would then call COMPLETE.
    """

    date_filter_verified: bool
    from_local: str | None = None
    to_local: str | None = None

    @property
    def active(self) -> bool:
        return self.date_filter_verified and bool(self.from_local) and bool(self.to_local)


@dataclass(frozen=True)
class Page:
    """One fetched page plus the attempt log behind it.

    `attempt_log` matters because §3.3 requires every try to be recorded: a retry is a new
    attempt on the same logical request, and the proof's attempt_error_count has to be derivable
    from rows rather than re-typed by the caller.
    """

    payload: Any
    page_no: int
    rows: int
    has_more: bool
    total_hint: int | None
    total_hint_trusted: bool
    http_status: int | None
    attempt_log: tuple[Attempt, ...] = ()

    @property
    def attempts(self) -> int:
        return len(self.attempt_log)

    @property
    def failed_attempts(self) -> int:
        return sum(1 for attempt in self.attempt_log if attempt.outcome != "OK")


def build_search_body(
    page_no: int,
    *,
    shape: SearchShape,
    page_size: int = PAGE_SIZE_LIMIT,
    window: SearchWindow | None = None,
    sort_asc: bool = False,
) -> list[dict[str, Any]]:
    """The filter contract for one family on the shared index."""
    if page_size > PAGE_SIZE_LIMIT:
        raise ValueError(f"portal refuses pageSize > {PAGE_SIZE_LIMIT}, got {page_size}")
    filters: list[dict[str, Any]] = [{"fieldName": "type", "searchType": "in", "fieldValues": [shape.es_type]}]
    if shape.case_khkq == "not1":
        filters.append({"fieldName": "caseKHKQ", "searchType": "not_in", "fieldValues": ["1"]})
    elif shape.case_khkq == "1":
        filters.append({"fieldName": "caseKHKQ", "searchType": "in", "fieldValues": ["1"]})
    if window and window.active:
        filters.append(
            # BoundaryRule has already chosen the source clock. Only serialize its
            # separator here: the live endpoint rejects a space with HTTP 400.
            # Converting these local calendar values to UTC again shifts the date window.
            {"fieldName": shape.date_field, "searchType": "range",
             "from": window.from_local.replace(" ", "T", 1),
             "to": window.to_local.replace(" ", "T", 1)}
        )
    return [
        {
            "pageSize": page_size,
            "pageNumber": page_no,
            "sortBy": shape.date_field,
            # Sent for parity with the portal's own client. The portal ignores it (verified
            # 2026-11-01), which is why nothing downstream may depend on ordering.
            "sortType": "ASC" if sort_asc else "DESC",
            "query": [
                {
                    "index": ES_INDEX,
                    "keyWord": "",
                    "matchType": "all-1",
                    "matchFields": [f"{shape.date_field}^10"],
                    "filters": filters,
                }
            ],
        }
    ]


def read_rows(payload: Any) -> tuple[list[Any], int | None]:
    """Extract (content, totalElements) or say why the envelope is not readable."""
    if not isinstance(payload, dict):
        raise ResponseShapeError(f"envelope is {type(payload).__name__}, expected an object")
    page = payload.get("page")
    if not isinstance(page, dict):
        raise ResponseShapeError("envelope has no 'page' object")
    content = page.get("content")
    if not isinstance(content, list):
        raise ResponseShapeError("page.content is not an array")
    total = page.get("totalElements")
    if total is not None and (not isinstance(total, int) or isinstance(total, bool) or total < 0):
        raise ResponseShapeError(f"page.totalElements must be a nonnegative integer, got {total!r}")
    return content, total


class EgpHomeLane:
    """Paginates one family against the home search endpoint."""

    def __init__(
        self,
        *,
        family: str,
        lane: FetchLane | None = None,
        source: SourceSpec | None = None,
        window: SearchWindow | None = None,
        max_pages: int | None = None,
        page_size: int | None = None,
        pacing_seconds: float | None = None,
    ) -> None:
        binding = binding_for(family)
        if family not in FAMILY_ORDER:
            raise ValueError(f"{family} is not served by the e-GP home index")
        self.family = family
        self.binding = binding
        self.shape = search_shape_for(family)
        self.source = source or source_for(family)
        self.window = window or SearchWindow(date_filter_verified=False)
        self.max_pages = max_pages if max_pages is not None else binding.max_pages
        self.page_size = page_size or binding.page_size
        self.pacing_seconds = pacing_seconds
        self._own_lane = lane is None
        self.lane = lane or FetchLane(
            url=f"https://{self.source.host}{self.source.endpoint}",
            source_key=self.source.key,
        )

    def close(self) -> None:
        if self._own_lane:
            self.lane.close()

    def fetch_page(self, page_no: int) -> Page:
        body = build_search_body(
            page_no,
            shape=self.shape,
            page_size=self.page_size,
            window=self.window,
            sort_asc=bool(self.window.active and self.window.from_local),
        )
        outcome = self.lane.post(body, min_interval=self.pacing_seconds)
        rows, total = read_rows(outcome.payload)
        family_matches = all(isinstance(row, dict) and self.binding.spec.accepts_record(row) for row in rows)
        family_scoped = self.binding.spec.required_date_field is None or (self.window.active and family_matches)
        return Page(
            payload=outcome.payload,
            page_no=page_no,
            rows=len(rows),
            has_more=len(rows) >= self.page_size,
            total_hint=total,
            total_hint_trusted=total is not None and total < ES_MAX_RESULT_WINDOW and family_scoped,
            http_status=outcome.http_status,
            attempt_log=tuple(outcome.attempts),
        )

    def iter_pages(self, *, start_page: int = 0) -> Iterator[Page]:
        """Walk pages from a resume point. Terminates at the cap even mid-stream (§5.3)."""
        page_no = start_page
        while True:
            if page_no >= self.max_pages:
                # Hitting the cap is not completion: the caller must see has_more=True so the
                # day stays PARTIAL (COV-15), even if the last page looked terminal.
                yield Page(
                    payload=None,
                    page_no=page_no,
                    rows=0,
                    has_more=True,
                    total_hint=None,
                    total_hint_trusted=False,
                    http_status=None,
                )
                return
            page = self.fetch_page(page_no)
            yield page
            if not page.has_more:
                return
            page_no += 1
