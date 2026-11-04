from datetime import UTC, datetime, timedelta

from santhau_studio.desktop.collection_totals import exact_total
from santhau_studio.domain.coverage import Interval


def test_totals_choose_nonoverlapping_full_path_and_never_prorate():
    a = datetime(2026, 10, 1, tzinfo=UTC)
    b, c, d = (a + timedelta(days=i) for i in (1, 2, 3))
    totals = [(a, b, 10, "old"), (b, d, 20, "new"), (a, c, 99, "new")]
    assert exact_total(totals, Interval(a, d)) == (30, "new", "old")
    assert exact_total([(a, d, 30, "t")], Interval(b, c))[0] is None
    assert exact_total(totals, Interval(a, d + timedelta(days=1)))[0] is None
