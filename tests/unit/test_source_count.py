from datetime import UTC, datetime, timedelta

from santhau_studio.collectors.source_count import count_windows, new_report
from santhau_studio.domain.coverage import Interval


def test_split_counts_do_not_add_capped_parent_and_can_resume():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    span = Interval(start, start + timedelta(days=4))
    report = new_report(span, ["TBMT", "KQLCNT"])
    def fetch(_family, window):
        return {"total": 10000 if window.duration.days > 1 else 4000, "trusted": window.duration.days == 1,
                "measured_at": "2026-11-04T00:00:00Z", "response_sha256": "offline"}
    count_windows(report, fetch, max_windows=3)
    assert all(r["total"] is None for r in report["families"])
    assert all(r["lower_bound"] == 10000 for r in report["families"])
    first = len([n for n in report["windows"] if n["state"] != "PENDING"])
    count_windows(report, fetch, max_windows=30)
    assert [r["total"] for r in report["families"]] == [16000, 16000]
    assert all(r["status"] == "SOURCE_REPORTED" for r in report["families"])
    assert len([n for n in report["windows"] if n["state"] != "PENDING"]) > first
    def unexpected(*_args):
        raise AssertionError("Completed windows must not be requested again")
    count_windows(report, unexpected, max_windows=5)


def test_untrusted_total_is_never_promoted_and_cap_uses_verified_minimum():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    report = new_report(Interval(start, start + timedelta(days=1)), ["TBMT"])
    count_windows(report, lambda *_: {"total": 10000, "trusted": False}, max_windows=5,
                  minimum=timedelta(days=1))
    assert report["families"][0]["total"] is None
    assert report["windows"][0]["state"] == "BLOCKED_PRECISION"
    assert report["families"][0]["lower_bound"] == 10000
