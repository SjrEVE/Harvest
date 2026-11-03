"""§4.2 boundary conversion, pinned per precision and per boundary kind.

A wrong closing term loses or repeats the rows that sit on the edge of a window, and the source
still answers 200 with plausible content. These are the strings the source will actually receive.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

import pytest

from santhau_studio.collectors.boundary import (
    BoundaryError,
    BoundaryRule,
    source_window_for,
    split_for_granularity,
)
from santhau_studio.domain.coverage import Interval

VN_DAY = Interval(datetime(2026, 10, 31, 17, 0, tzinfo=UTC), datetime(2026, 11, 1, 17, 0, tzinfo=UTC))


def rule(kind: str = "CLOSED_LOCAL", precision: str = "DAY") -> BoundaryRule:
    return BoundaryRule(
        rule_id=1, boundary_kind=kind, precision=precision,
        source_timezone="Asia/Ho_Chi_Minh", offset_minutes=420, canary_verified=True,
    )


def test_closed_local_day_closes_at_the_last_second_of_the_vn_day() -> None:
    assert source_window_for(rule(), VN_DAY) == ("2026-11-01 00:00:00", "2026-11-01 23:59:59")


def test_closed_local_hour() -> None:
    hour = Interval(VN_DAY.from_utc, VN_DAY.from_utc + timedelta(hours=1))
    assert source_window_for(rule(precision="HOUR"), hour) == ("2026-11-01 00:00:00", "2026-11-01 00:59:59")


def test_closed_local_minute() -> None:
    span = Interval(VN_DAY.from_utc, VN_DAY.from_utc + timedelta(minutes=5))
    assert source_window_for(rule(precision="MINUTE"), span) == ("2026-11-01 00:00:00", "2026-11-01 00:04:59")


def test_millisecond_precision_keeps_three_fraction_digits() -> None:
    span = Interval(VN_DAY.from_utc, VN_DAY.from_utc + timedelta(milliseconds=500))
    _from, to = source_window_for(rule(precision="MILLISECOND"), span)
    assert to.endswith(".499"), to


def test_half_open_utc_is_not_adjusted() -> None:
    span = Interval(datetime(2026, 11, 1, 0, 0, tzinfo=UTC), datetime(2026, 11, 2, 0, 0, tzinfo=UTC))
    assert source_window_for(rule("HALF_OPEN_UTC", "DAY"), span) == (
        "2026-11-01 00:00:00",
        "2026-11-02 00:00:00",
    ), "subtracting at a half-open source would drop the last instant"


def test_closed_utc_uses_utc_not_the_local_zone() -> None:
    span = Interval(datetime(2026, 11, 1, 0, 0, tzinfo=UTC), datetime(2026, 11, 1, 1, 0, tzinfo=UTC))
    assert source_window_for(rule("CLOSED_UTC", "HOUR"), span) == ("2026-11-01 00:00:00", "2026-11-01 00:59:59")


def test_open_utc_is_refused() -> None:
    with pytest.raises(BoundaryError, match="cannot be proven terminal"):
        source_window_for(rule("OPEN_UTC"), VN_DAY)


def test_unknown_precision_is_refused_rather_than_guessed() -> None:
    with pytest.raises(BoundaryError, match="unsupported precision"):
        source_window_for(rule(precision="WEEK"), VN_DAY)


def test_empty_interval_cannot_exist_at_all() -> None:
    """The guard lives in the domain type, so no caller can reach the conversion with one."""
    with pytest.raises(ValueError, match="empty interval"):
        Interval(VN_DAY.from_utc, VN_DAY.from_utc)


def test_interval_below_the_source_precision_is_refused() -> None:
    """Asking a day-precision source for 12 hours cannot be answered exactly; say so."""
    tiny = Interval(VN_DAY.from_utc, VN_DAY.from_utc + timedelta(hours=12))
    with pytest.raises(BoundaryError, match="shorter than the source"):
        source_window_for(rule(precision="DAY"), tiny)


def test_unknown_timezone_falls_back_to_the_declared_offset() -> None:
    fallback = BoundaryRule(
        rule_id=2, boundary_kind="CLOSED_LOCAL", precision="DAY",
        source_timezone="Not/AZone", offset_minutes=420, canary_verified=True,
    )
    assert source_window_for(fallback, VN_DAY) == ("2026-11-01 00:00:00", "2026-11-01 23:59:59")


@pytest.mark.parametrize("granularity", ["DAY", "HOUR", "MINUTE"])
def test_split_covers_the_whole_interval_without_gaps(granularity: str) -> None:
    span = Interval(datetime(2026, 11, 1, 0, 0, tzinfo=UTC), datetime(2026, 11, 1, 6, 0, tzinfo=UTC))
    parts = split_for_granularity(span, granularity)
    assert parts[0].from_utc == span.from_utc
    assert parts[-1].to_utc == span.to_utc
    for earlier, later in itertools.pairwise(parts):
        assert earlier.to_utc == later.from_utc, "a split with a hole silently loses coverage"
    assert sum((part.duration for part in parts), timedelta(0)) == span.duration


def test_split_of_a_small_interval_is_the_interval_itself() -> None:
    small = Interval(VN_DAY.from_utc, VN_DAY.from_utc + timedelta(minutes=30))
    assert split_for_granularity(small, "DAY") == (small,)
