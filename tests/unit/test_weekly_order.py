from datetime import date

import pytest

from santhau_studio.domain.coverage import Interval, vn_calendar_day
from santhau_studio.workers.weekly import weeks


@pytest.mark.parametrize(("first", "last", "expected"), [
    ("2026-10-01", "2026-10-01", [("2026-10-01", "2026-10-01")]),
    ("2026-10-01", "2026-10-07", [("2026-10-01", "2026-10-07")]),
    ("2026-10-01", "2026-10-08", [("2026-10-08", "2026-10-08"), ("2026-10-01", "2026-10-07")]),
    ("2025-12-25", "2026-01-08", [("2026-01-08", "2026-01-08"), ("2026-01-01", "2026-01-07"), ("2025-12-25", "2025-12-31")]),
])
def test_weeks_newest_first_preserve_existing_boundaries_without_gaps(first, last, expected):
    def span(a, b):
        return Interval(vn_calendar_day(date.fromisoformat(a)).from_utc,
                        vn_calendar_day(date.fromisoformat(b)).to_utc)

    actual = list(weeks(span(first, last)))
    assert actual == [span(a, b) for a, b in expected]
    assert sum(period.duration.days for period in actual) == span(first, last).duration.days
