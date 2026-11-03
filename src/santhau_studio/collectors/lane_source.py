"""Production glue: runner window → EgpHomeLane → page iterator (§6, §4.2).

The runner injects a page source; this module is the one that builds it from the real lane, so a
test can swap only the HTTP transport (an `httpx.MockTransport` fake portal) and still exercise
allowlist, pacing, retry, classification, pagination and extraction exactly as production does.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from santhau_studio.collectors.egp_home import EgpHomeLane, Page, SearchWindow
from santhau_studio.collectors.http_client import FetchLane
from santhau_studio.collectors.source_registry import binding_for
from santhau_studio.domain.coverage import Interval
from santhau_studio.workers.runner import SearchWindow as RunnerWindow


def egp_page_source(
    *,
    family: str,
    lane_factory: Callable[[], FetchLane] | None = None,
    page_size: int | None = None,
    pacing_seconds: float | None = None,
) -> Callable[[Interval, str, RunnerWindow], Iterator[Page]]:
    """A page source for one family that walks the real lane page by page.

    The runner refuses to call this without a verified boundary window (`window` is None in that
    case), so `date_filter_verified=True` here never means "we hoped it works".
    """

    def page_source(interval: Interval, granularity: str, window: RunnerWindow) -> Iterator[Page]:
        del interval, granularity  # the source-side filter is the window, built from the rule
        search_window = SearchWindow(
            date_filter_verified=True, from_local=window.from_value, to_local=window.to_value
        )
        lane = lane_factory() if lane_factory else None
        home = EgpHomeLane(
            family=family,
            lane=lane,
            window=search_window,
            page_size=page_size or binding_for(family).page_size,
            pacing_seconds=pacing_seconds,
        )
        try:
            yield from home.iter_pages()
        finally:
            home.close()

    return page_source


def bounded_source(source, *, max_pages, max_records):
    """Share a job cap across intervals, including adaptive splits."""
    from santhau_studio.collectors.http_client import FetchFailed
    pages = rows = 0
    def walk(*args):
        nonlocal pages, rows
        iterator = source(*args)
        try:
            while True:
                if pages >= max_pages or rows >= max_records:
                    raise FetchFailed("JOB_PAGE_OR_RECORD_BUDGET_EXHAUSTED")
                try:
                    page = next(iterator)
                except StopIteration:
                    return
                pages += 1
                rows += page.rows
                yield page
        finally:
            close = getattr(iterator, "close", None)
            if close:
                close()
    return walk
