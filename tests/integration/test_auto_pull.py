"""One-button server pull: skip proved windows, walk backwards from today."""
from datetime import date

from santhau_studio.linux.auto_pull import enqueue_missing, plan_missing


def _w(day: str, family: str, state: str, queue=None) -> dict:
    return {
        "date": day, "source_key": "egp_home", "family": family, "state": state,
        "queue": queue or [], "proven_fraction": 1.0 if state == "proved" else 0.0,
        "reasons": [], "gaps": [],
    }


def _report(*windows) -> dict:
    return {"windows": list(windows), "summary": {}, "day_family_windows": len(windows)}


def test_proved_windows_are_skipped():
    rep = _report(
        _w("2022-09-01", "TBMT", "never_attempted"),
        _w("2022-09-02", "TBMT", "proved"),
        _w("2022-09-03", "TBMT", "partial_uncovered"),
    )
    batches = plan_missing(rep, today=date(2026, 11, 7))
    days = {d for b in batches for d in b["days"]}
    assert days == {"2022-09-01", "2022-09-03"}, days


def test_newest_day_comes_first():
    rep = _report(
        _w("2022-09-01", "TBMT", "never_attempted"),
        _w("2026-11-05", "TBMT", "never_attempted"),
    )
    batches = plan_missing(rep, today=date(2026, 11, 7))
    assert batches[0]["days"][0] == "2026-11-05"


def test_consecutive_days_same_family_are_one_batch():
    rep = _report(*(_w(f"2022-09-0{d}", "TBMT", "never_attempted") for d in (1, 2, 3)))
    batches = plan_missing(rep, today=date(2026, 11, 7))
    assert len(batches) == 1
    assert batches[0]["days"] == ["2022-09-03", "2022-09-02", "2022-09-01"]
    assert batches[0]["from"] == "2022-09-01" and batches[0]["to"] == "2022-09-03"


def test_already_queued_window_is_skipped():
    rep = _report(
        _w("2022-09-01", "TBMT", "never_attempted", queue=["queued"]),
        _w("2022-09-02", "TBMT", "never_attempted", queue=["active"]),
        _w("2022-09-03", "TBMT", "never_attempted"),
    )
    batches = plan_missing(rep, today=date(2026, 11, 7))
    assert {d for b in batches for d in b["days"]} == {"2022-09-03"}


def test_future_days_are_skipped():
    rep = _report(
        _w("2026-11-08", "TBMT", "never_attempted"),
        _w("2026-11-07", "TBMT", "never_attempted"),
    )
    batches = plan_missing(rep, today=date(2026, 11, 7))
    assert {d for b in batches for d in b["days"]} == {"2026-11-07"}


def test_dry_run_never_writes_and_reports_scope(tmp_path):
    """A fresh synthetic scope: nothing proved, so the whole span is planned."""
    result = enqueue_missing(tmp_path, execute=False, today=date(2026, 11, 7))
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["jobs"] == []
    assert not list((tmp_path / "jobs/queued").glob("*.json"))


def test_scope_is_extended_to_today_without_touching_settings():
    from santhau_studio.linux.auto_pull import extend_scope_to_today

    scope = {"date_from": "2022-01-01", "date_to": "2026-11-06", "sources": ()}
    extended = extend_scope_to_today(scope, date(2026, 11, 7))
    assert extended["date_to"] == "2026-11-07"
    assert scope["date_to"] == "2026-11-06"  # original untouched
    assert extend_scope_to_today(scope, date(2026, 11, 6))["date_to"] == "2026-11-06"
