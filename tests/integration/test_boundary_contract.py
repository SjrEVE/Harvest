"""Boundary contract, terminal evidence, caps/retry bookkeeping and cross-family identity (RC2-A).

These pin the quiet edge behaviour the whole coverage claim rests on: the exact filter strings a
closed-local source receives, what counts as terminal evidence, and that a truncated or capped
walk never becomes COMPLETE.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from santhau_studio.collectors.boundary import BoundaryError, BoundaryRule, source_window_for
from santhau_studio.collectors.egp_home import Page
from santhau_studio.collectors.http_client import Attempt
from santhau_studio.collectors.source_registry import SOURCE_EGP_HOME, SOURCES
from santhau_studio.domain.coverage import Interval, vn_calendar_day
from santhau_studio.storage.warehouse import WarehouseWriter
from santhau_studio.workers.runner import RunLimits, Runner

ENDPOINT = SOURCES[SOURCE_EGP_HOME].endpoint
ADAPTER_SHA = hashlib.sha256(b"boundary-contract").hexdigest()
DAY = vn_calendar_day(date(2026, 11, 1))


def rule(precision: str, kind: str = "CLOSED_LOCAL") -> BoundaryRule:
    return BoundaryRule(rule_id=1, boundary_kind=kind, precision=precision,
                        source_timezone="Asia/Ho_Chi_Minh", offset_minutes=420, canary_verified=True)


def test_closed_local_day_window_is_last_expressible_instant() -> None:
    start, end = source_window_for(rule("DAY"), DAY)
    assert start == "2026-11-01 00:00:00"
    assert end == "2026-11-01 23:59:59"


def test_closed_local_hour_window_steps_down_one_unit() -> None:
    hour = Interval(datetime(2026, 11, 1, 3, tzinfo=UTC), datetime(2026, 11, 1, 4, tzinfo=UTC))
    start, end = source_window_for(rule("HOUR"), hour)
    assert start == "2026-11-01 10:00:00"  # VN local (+7)
    assert end == "2026-11-01 10:59:59"


def test_half_open_source_is_not_adjusted() -> None:
    start, end = source_window_for(rule("DAY", kind="HALF_OPEN_UTC"), DAY)
    assert (start, end) == ("2026-10-31 17:00:00", "2026-11-01 17:00:00")


def test_open_utc_cannot_prove_termination() -> None:
    with pytest.raises(BoundaryError):
        source_window_for(rule("DAY", kind="OPEN_UTC"), DAY)


def test_interval_shorter_than_precision_is_refused() -> None:
    tiny = Interval(datetime(2026, 11, 1, 0, tzinfo=UTC), datetime(2026, 11, 1, 0, 0, 0, 500000, tzinfo=UTC))
    with pytest.raises(BoundaryError):
        source_window_for(rule("DAY"), tiny)


def records(page_no: int, count: int, total: int | None = None) -> dict:
    content = [
        {"notifyNo": f"P{page_no}-{i}", "notifyVersion": "00", "bidName": f"Gói {i}"}
        for i in range(count)
    ]
    return {"page": {"content": content, "totalElements": total if total is not None else count}}


def page(page_no: int, count: int, *, total: int | None = None, has_more: bool = False,
         attempts: tuple[Attempt, ...] = ()) -> Page:
    payload = records(page_no, count, total)
    hint = total if total is not None else count
    return Page(payload=payload, page_no=page_no, rows=count, has_more=has_more,
                total_hint=hint, total_hint_trusted=hint < 10_000,  # ES window saturates here
                http_status=200, attempt_log=attempts)


@pytest.fixture()
def writer(tmp_path: Path):
    data_root = tmp_path / "data"
    writer = WarehouseWriter.open(data_root, create_schema=True, role="boundary")
    writer.conn.execute(
        "INSERT INTO filter_boundary_rules (rule_key, rule_version, source_key, family, endpoint,"
        " adapter_sha, boundary_kind, precision, source_timezone, offset_minutes, conversion_params,"
        " canary_verified, verified_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("boundary", 1, SOURCE_EGP_HOME, "TBMT", ENDPOINT, ADAPTER_SHA,
         "CLOSED_LOCAL", "DAY", "Asia/Ho_Chi_Minh", 420, '{"to_minus":"1s"}', 1,
         "2026-10-31T00:00:00Z"),
    )
    writer.conn.commit()
    writer.register_run("run-b", "job-b", "BACKFILL")
    yield writer
    writer.close()


def runner(writer, pages: list[Page], *, limits: RunLimits | None = None) -> Runner:
    def source(_interval: Interval, _granularity: str, _window) -> object:
        yield from pages

    return Runner(writer=writer, source_key=SOURCE_EGP_HOME, family="TBMT", endpoint=ENDPOINT,
                  adapter_sha=ADAPTER_SHA, page_source=source,
                  limits=limits or RunLimits(max_pages=450, max_records=4000, page_size=10))


def test_short_page_is_terminal_evidence(writer) -> None:
    outcome = runner(writer, [page(0, 10, total=13, has_more=True), page(1, 3, total=13)]).run_interval(DAY, "DAY", "run-b")
    assert outcome.status == "COMPLETE", outcome.blockers
    proof = writer.conn.execute("SELECT terminal_evidence_json FROM coverage_proofs").fetchone()[0]
    assert '"kind": "SHORT_PAGE"' in proof


def test_empty_page_is_terminal_evidence(writer) -> None:
    outcome = runner(writer, [page(0, 0, total=0)]).run_interval(DAY, "DAY", "run-b")
    assert outcome.status == "COMPLETE", outcome.blockers
    proof = writer.conn.execute("SELECT terminal_evidence_json FROM coverage_proofs").fetchone()[0]
    assert '"kind": "EMPTY_PAGE"' in proof


def test_total_at_the_es_window_is_untrusted_and_never_complete(writer) -> None:
    outcome = runner(writer, [page(0, 10, total=10_000, has_more=True)]).run_interval(DAY, "DAY", "run-b")
    # `totalElements == max_result_window` is not a count; the runner must not trust it, and the
    # resulting split (HOUR children vs a DAY rule) cannot be proven either.
    assert outcome.status != "COMPLETE"
    assert outcome.total_trusted is False
    assert writer.conn.execute("SELECT COUNT(*) FROM coverage_proofs").fetchone()[0] == 0


def test_max_pages_cap_never_completes(writer) -> None:
    pages = [page(no, 10, total=100, has_more=True) for no in range(5)]
    outcome = runner(writer, pages, limits=RunLimits(max_pages=2, max_records=4000, page_size=10)).run_interval(
        DAY, "DAY", "run-b"
    )
    assert outcome.status == "PARTIAL"
    assert any("MAX_PAGES_HIT" in blocker for blocker in outcome.blockers)
    assert writer.conn.execute("SELECT COUNT(*) FROM coverage_proofs").fetchone()[0] == 0


def test_retry_is_counted_as_attempts_on_one_request(writer) -> None:
    retry = Attempt(attempt_no=1, started_at_utc="2026-11-01T00:00:00Z", http_status=429,
                    outcome="RETRY", error_kind="RATE_LIMIT_429", elapsed_seconds=0.1, byte_len=0)
    ok = Attempt(attempt_no=2, started_at_utc="2026-11-01T00:00:01Z", http_status=200,
                 outcome="OK", elapsed_seconds=0.1, byte_len=100)
    outcome = runner(writer, [page(0, 4, total=4, attempts=(retry, ok))]).run_interval(DAY, "DAY", "run-b")
    assert outcome.status == "COMPLETE", outcome.blockers
    proof = writer.conn.execute(
        "SELECT request_count, attempt_count, attempt_error_count, unrecovered_request_count"
        " FROM coverage_proofs"
    ).fetchone()
    assert proof["request_count"] == 1
    assert proof["attempt_count"] == 2
    assert proof["attempt_error_count"] == 1
    assert proof["unrecovered_request_count"] == 0


def test_same_natural_key_in_two_families_stays_two_records(writer) -> None:
    page_payload = {"page": {"content": [
        {"notifyNo": "IB-SHARED", "notifyVersion": "00", "bidName": "Gói chung", "investorName": "Chủ A"},
        {"notifyNo": "IB-ONLY-TBMT", "notifyVersion": "00", "bidName": "Gói riêng", "investorName": "Chủ B"},
    ], "totalElements": 2}}
    # Same natural key, different family: identity must not collapse across families.
    ref = writer.store_response(page_payload)
    attempt = writer.ensure_attempt(
        run_id="run-b", family="TBMT", endpoint=ENDPOINT,
        interval_from="2026-11-01T00:00:00Z", interval_to="2026-11-02T00:00:00Z",
        page_no=0, attempt_no=1, outcome="OK", response=ref,
    )
    from santhau_studio.collectors.extraction import extract_page
    from santhau_studio.collectors.source_registry import binding_for

    report = extract_page(page_payload, binding_for("TBMT").spec)
    writer.commit_page(run_id="run-b", attempt_id=attempt, response_ref=ref, records=report.records)

    kq_payload = {"page": {"content": [
        {"notifyNo": "IB-SHARED", "notifyVersion": "01", "bidName": "Kết quả gói chung",
         "winningContractorName": "Nhà thầu X"},
    ], "totalElements": 1}}
    kq_ref = writer.store_response(kq_payload)
    kq_attempt = writer.ensure_attempt(
        run_id="run-b", family="KQLCNT", endpoint=ENDPOINT,
        interval_from="2026-11-01T00:00:00Z", interval_to="2026-11-02T00:00:00Z",
        page_no=0, attempt_no=1, outcome="OK", response=kq_ref,
    )
    kq_report = extract_page(kq_payload, binding_for("KQLCNT").spec)
    writer.commit_page(run_id="run-b", attempt_id=kq_attempt, response_ref=kq_ref, records=kq_report.records)

    rows = writer.conn.execute(
        "SELECT family, natural_key, identity_key FROM source_records ORDER BY family, natural_key"
    ).fetchall()
    assert len(rows) == 3, [tuple(r) for r in rows]
    tbmt_shared = next(r for r in rows if r["family"] == "TBMT" and r["natural_key"] == "IB-SHARED")
    kq_shared = next(r for r in rows if r["family"] == "KQLCNT" and r["natural_key"] == "IB-SHARED")
    assert tbmt_shared["identity_key"] != kq_shared["identity_key"]
    assert tbmt_shared["identity_key"].startswith("id1:") and len(tbmt_shared["identity_key"]) == 68

    # Provenance/cardinality: one observation per record, receipts per page, versions per record.
    assert writer.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 3
    assert writer.conn.execute("SELECT COUNT(*) FROM source_record_versions").fetchone()[0] == 3
    assert writer.conn.execute("SELECT COUNT(*) FROM page_receipts").fetchone()[0] == 2
    hashes = {row[0] for row in writer.conn.execute("SELECT sha256 FROM raw_objects")}
    referenced = {row[0] for row in writer.conn.execute(
        "SELECT response_object_sha FROM observations UNION SELECT record_object_sha FROM observations"
    )}
    assert referenced <= hashes
    assert all(h and len(h) == 64 for h in referenced)
