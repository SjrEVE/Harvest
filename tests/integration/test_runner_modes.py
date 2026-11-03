"""Milestone C: the three run modes, proven on a fake portal but through the production writer.

Everything here asserts *observable* consequences — which rows exist, which interval is skippable,
where the watermark sits — rather than internal call counts, because the acceptance criteria in §4
are stated that way.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, date, timedelta
from pathlib import Path

import pytest

from santhau_studio.collectors.boundary import BoundaryRule
from santhau_studio.collectors.egp_home import Page
from santhau_studio.collectors.http_client import AccessBlocked, Attempt, FetchFailed
from santhau_studio.collectors.source_registry import SOURCE_EGP_HOME, SOURCES
from santhau_studio.domain.coverage import Interval, vn_calendar_day
from santhau_studio.storage import coverage
from santhau_studio.storage.warehouse import WarehouseWriter
from santhau_studio.workers.runner import RunLimits, Runner

ENDPOINT = SOURCES[SOURCE_EGP_HOME].endpoint
ADAPTER_SHA = hashlib.sha256(b"runner-fixture").hexdigest()
DAY = vn_calendar_day(date(2026, 11, 1))
NEXT_DAY = vn_calendar_day(date(2026, 11, 2))


def records(page_no: int, count: int, prefix: str = "N") -> list[dict]:
    return [{"notifyNo": f"{prefix}{page_no}-{i}", "notifyVersion": "00", "bidName": f"Gói {i}"} for i in range(count)]


def envelope(page_no: int, count: int, *, prefix: str = "N") -> dict:
    return {"page": {"content": records(page_no, count, prefix), "totalElements": count}}


def page_of(page_no: int, count: int, *, total: int | None = None, trusted: bool = True,
            attempts: tuple[Attempt, ...] = (), prefix: str = "N") -> Page:
    content = records(page_no, count, prefix)
    return Page(
        payload={"page": {"content": content, "totalElements": total if total is not None else len(content)}},
        page_no=page_no,
        rows=len(content),
        has_more=count >= 10,
        total_hint=total if total is not None else len(content),
        total_hint_trusted=trusted,
        http_status=200,
        attempt_log=attempts,
    )


def attempt(no: int, outcome: str, status: int | None = 200, error: str | None = None) -> Attempt:
    return Attempt(attempt_no=no, started_at_utc="2026-11-03T00:00:00Z", http_status=status,
                   outcome=outcome, error_kind=error, elapsed_seconds=0.1, byte_len=100)


class FakePortal:
    """Answers per granularity; records every request it receives."""

    def __init__(self, by_granularity: dict[str, list[Page] | Exception]) -> None:
        self.by_granularity = by_granularity
        self.calls: list[tuple[str, Interval, object]] = []

    def __call__(self, interval: Interval, granularity: str, window) -> Iterator[Page]:
        self.calls.append((granularity, interval, window))
        answer = self.by_granularity[granularity]
        if isinstance(answer, Exception):
            raise answer
        return iter(list(answer))

    @property
    def request_count(self) -> int:
        return len(self.calls)


@pytest.fixture()
def writer(tmp_path: Path):
    writer = WarehouseWriter.open(tmp_path / "data", create_schema=True, role="backfill")
    writer.register_run("run-1", "job-1", "BACKFILL")
    # The proof trigger resolves boundary_rule_id against this table, so the fixture
    # registers the same rule the runner will cite — canary-verified, same adapter_sha.
    writer.conn.execute(
        "INSERT INTO filter_boundary_rules (rule_key, rule_version, source_key, family, endpoint,"
        " adapter_sha, boundary_kind, precision, source_timezone, offset_minutes, conversion_params,"
        " canary_verified, verified_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("egp-tbmt-runner", 1, SOURCE_EGP_HOME, "TBMT", ENDPOINT, ADAPTER_SHA,
         "CLOSED_LOCAL", "HOUR", "Asia/Ho_Chi_Minh", 420, '{"to_minus":"1s"}', 1,
         "2026-10-31T00:00:00Z"),
    )
    writer.conn.commit()
    yield writer
    writer.close()


def verified_rule(writer, precision: str = "HOUR") -> BoundaryRule:
    row = writer.conn.execute(
        "SELECT id FROM filter_boundary_rules WHERE adapter_sha = ?", (ADAPTER_SHA,)
    ).fetchone()
    return BoundaryRule(
        rule_id=row["id"], boundary_kind="CLOSED_LOCAL", precision=precision,
        source_timezone="Asia/Ho_Chi_Minh", offset_minutes=420, canary_verified=True,
    )


_UNSET = object()


def test_current_day_keeps_collected_rows_without_claiming_complete(writer):
    portal = FakePortal({"DAY": [page_of(0, 3)]})
    runner = make_runner(writer, portal)
    runner.clock = lambda: DAY.from_utc + timedelta(hours=8)
    outcome = runner.run_interval(DAY, "DAY", "run-1")
    assert outcome.rows == 3
    assert outcome.status == "PARTIAL"
    assert "INTERVAL_NOT_CLOSED" in outcome.blockers
    row = writer.conn.execute("SELECT status,proof_id FROM coverage_intervals WHERE id=?", (outcome.interval_id,)).fetchone()
    assert row["status"] == "PARTIAL" and row["proof_id"] is None


def make_runner(writer, portal: FakePortal, *, rule=_UNSET,
                limits: RunLimits | None = None, stop=None) -> Runner:
    if rule is _UNSET:
        rule = verified_rule(writer)
    def rule_for(granularity: str) -> BoundaryRule | None:
        if rule is None:
            return None
        if coverage.granularity_rank(rule.precision) < coverage.granularity_rank(granularity):
            return None
        return BoundaryRule(rule_id=rule.rule_id, boundary_kind=rule.boundary_kind, precision=granularity,
                            source_timezone=rule.source_timezone, offset_minutes=rule.offset_minutes,
                            canary_verified=True)

    return Runner(
        writer=writer, source_key=SOURCE_EGP_HOME, family="TBMT", endpoint=ENDPOINT,
        adapter_sha=ADAPTER_SHA, page_source=portal, rule_for=rule_for,
        limits=limits or RunLimits(max_pages=450, max_records=4000, page_size=10), stop_requested=stop,
        max_split_granularity="HOUR",
    )


# ------------------------------------------------------------------ COMPLETE path

def test_a_full_interval_writes_proof_and_is_skippable(writer) -> None:
    portal = FakePortal({"DAY": [page_of(0, 10, total=25), page_of(1, 10, total=25), page_of(2, 5, total=25)]})
    runner = make_runner(writer, portal)
    outcome = runner.run_interval(DAY, "DAY", "run-1")
    assert outcome.status == "COMPLETE", outcome.blockers
    assert outcome.rows == 25 and outcome.requests == 3
    assert outcome.identity_keys and outcome.distinct_ids == 25
    row = writer.conn.execute(
        "SELECT ci.status, cp.proof_version, cp.distinct_ids, cp.total_trusted, cp.request_count,"
        " cp.attempt_count, cp.malformed_count, cp.max_pages_hit, cp.unrecovered_request_count,"
        " cp.terminal_evidence_json, cp.boundary_rule_id, cp.ids_union_hash"
        " FROM coverage_intervals ci JOIN coverage_proofs cp ON cp.id = ci.proof_id"
    ).fetchone()
    assert row["status"] == "COMPLETE" and row["proof_version"] == 1
    assert row["distinct_ids"] == 25 and row["total_trusted"] == 1
    assert row["request_count"] == 3 and row["max_pages_hit"] == 0
    assert row["unrecovered_request_count"] == 0 and row["malformed_count"] == 0
    assert row["boundary_rule_id"] == verified_rule(writer).rule_id and len(row["ids_union_hash"]) == 64
    resolution = coverage.resolve(writer.conn, SOURCE_EGP_HOME, "TBMT", DAY.from_utc, DAY.to_utc)
    assert resolution.can_skip and resolution.status == "COMPLETE"


def test_the_source_filter_uses_the_verified_boundary_window(writer) -> None:
    portal = FakePortal({"DAY": [page_of(0, 1)]})
    make_runner(writer, portal).run_interval(DAY, "DAY", "run-1")
    _granularity, _interval, window = portal.calls[0]
    assert (window.from_value, window.to_value) == ("2026-11-01 00:00:00", "2026-11-01 23:59:59")


def test_pages_are_committed_through_the_production_writer(writer) -> None:
    portal = FakePortal({"DAY": [page_of(0, 10), page_of(1, 3)]})
    make_runner(writer, portal).run_interval(DAY, "DAY", "run-1")
    assert writer.conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0] == 13
    receipt = writer.conn.execute(
        "SELECT records_seen, records_new, response_received, page_committed FROM page_receipts"
    ).fetchall()
    assert len(receipt) == 2
    for row in receipt:
        assert row["response_received"] == 1 and row["page_committed"] == 1
        assert row["records_new"] > 0
    assert writer.conn.execute("SELECT COUNT(*) FROM raw_objects").fetchone()[0] == 2 + 13


def test_retry_that_ultimately_succeeds_still_proves_complete(writer) -> None:
    """COV-16: one timeout then success keeps COMPLETE, records the error, and keeps both attempts."""
    portal = FakePortal({
        "DAY": [
            page_of(0, 10, total=14, attempts=(attempt(1, "RETRY", 504, "SERVER_ERROR"), attempt(2, "OK"))),
            page_of(1, 4, total=14, attempts=(attempt(1, "OK"),)),
        ]
    })
    outcome = make_runner(writer, portal).run_interval(DAY, "DAY", "run-1")
    assert outcome.status == "COMPLETE", outcome.blockers
    assert outcome.requests == 2 and outcome.attempts == 3 and outcome.attempt_errors == 1
    assert outcome.unrecovered_requests == 0
    assert writer.conn.execute("SELECT COUNT(*) FROM fetch_requests").fetchone()[0] == 2
    assert writer.conn.execute("SELECT COUNT(*) FROM fetch_attempts").fetchone()[0] == 3
    assert writer.conn.execute("SELECT COUNT(*) FROM page_receipts").fetchone()[0] == 2
    proof = writer.conn.execute(
        "SELECT attempt_error_count, attempt_count, unrecovered_request_count FROM coverage_proofs"
    ).fetchone()
    assert proof["attempt_error_count"] == 1 and proof["attempt_count"] == 3
    assert proof["unrecovered_request_count"] == 0


# ------------------------------------------------------------------ refusals to claim COMPLETE

def test_missing_boundary_rule_cannot_produce_a_complete_interval(writer) -> None:
    portal = FakePortal({"DAY": [page_of(0, 2)]})
    outcome = make_runner(writer, portal, rule=None).run_interval(DAY, "DAY", "run-1")
    assert outcome.status == "PARTIAL"
    assert "BOUNDARY_RULE_UNVERIFIED" in outcome.blockers
    assert writer.conn.execute("SELECT COUNT(*) FROM coverage_proofs").fetchone()[0] == 0
    assert writer.conn.execute("SELECT status FROM coverage_intervals").fetchone()[0] == "PARTIAL"
    assert not coverage.resolve(writer.conn, SOURCE_EGP_HOME, "TBMT", DAY.from_utc, DAY.to_utc).can_skip


def test_untrusted_total_is_partial(writer) -> None:
    portal = FakePortal({"DAY": [page_of(0, 10, total=50, trusted=False)]})
    outcome = make_runner(writer, portal).run_interval(DAY, "DAY", "run-1")
    assert outcome.status == "PARTIAL" and "TOTAL_UNTRUSTED" in outcome.blockers


def test_total_changing_midwalk_is_partial(writer) -> None:
    portal = FakePortal({"DAY": [page_of(0, 10, total=12), page_of(1, 2, total=13)]})
    outcome = make_runner(writer, portal).run_interval(DAY, "DAY", "run-1")
    assert outcome.status == "PARTIAL"
    assert any("TOTAL_CHANGED" in b for b in outcome.blockers)
    assert writer.conn.execute("SELECT COUNT(*) FROM coverage_proofs").fetchone()[0] == 0


def test_source_exhaustion_without_terminal_signal_is_not_complete(writer) -> None:
    """Every page says has_more, then the source simply stops: no terminal evidence, no proof."""
    portal = FakePortal({"DAY": [page_of(0, 10), page_of(1, 10)]})  # both full, no short page
    outcome = make_runner(writer, portal).run_interval(DAY, "DAY", "run-1")
    assert outcome.status == "PARTIAL"
    assert "NO_TERMINAL_EVIDENCE" in outcome.blockers


def test_max_pages_hit_is_partial_and_carries_the_count(writer) -> None:
    portal = FakePortal({"DAY": [page_of(i, 10) for i in range(6)]})
    limits = RunLimits(max_pages=3, max_records=10_000, page_size=10)
    runner = make_runner(writer, portal, limits=limits)
    runner.max_split_granularity = "DAY"  # forbid the split, so the cap is what is measured
    outcome = runner.fetch_interval(DAY, "DAY", "run-1")
    assert outcome.status == "PARTIAL" and outcome.max_pages_hit == 3
    assert any("MAX_PAGES_HIT" in b for b in outcome.blockers)


def test_max_records_hit_is_partial(writer) -> None:
    portal = FakePortal({"DAY": [page_of(0, 10), page_of(1, 10), page_of(2, 5)]})
    limits = RunLimits(max_pages=450, max_records=20, page_size=10)
    runner = make_runner(writer, portal, limits=limits)
    runner.max_split_granularity = "DAY"
    outcome = runner.fetch_interval(DAY, "DAY", "run-1")
    assert outcome.status == "PARTIAL" and outcome.max_records_hit == 20


def test_access_blocked_interval_is_recorded_as_blocked_not_partial(writer) -> None:
    portal = FakePortal({"DAY": AccessBlocked("egp_home refused access (HTTP_403)")})
    outcome = make_runner(writer, portal).run_interval(DAY, "DAY", "run-1")
    assert outcome.status == "BLOCKED"
    assert "ACCESS_BLOCKED:AccessBlocked" in outcome.blockers
    assert writer.conn.execute("SELECT status FROM coverage_intervals").fetchone()[0] == "BLOCKED"
    assert writer.conn.execute("SELECT COUNT(*) FROM coverage_proofs").fetchone()[0] == 0


def test_request_that_never_recovered_forbids_complete(writer) -> None:
    portal = FakePortal({"DAY": FetchFailed("egp_home: SERVER_ERROR persisted over 3 attempts")})
    outcome = make_runner(writer, portal).run_interval(DAY, "DAY", "run-1")
    assert outcome.status == "PARTIAL"
    assert outcome.unrecovered_requests == 1
    assert any("REQUEST_UNRECOVERED" in b for b in outcome.blockers)
    assert writer.conn.execute("SELECT COUNT(*) FROM coverage_proofs").fetchone()[0] == 0


def test_stop_request_leaves_an_interrupted_interval(writer) -> None:
    calls = {"n": 0}

    def source(_interval, _granularity, _window):
        calls["n"] += 1
        if calls["n"] > 1:
            raise AssertionError("the runner must not fetch after a stop")
        return iter([page_of(0, 10), page_of(1, 10)])

    runner = Runner(
        writer=writer, source_key=SOURCE_EGP_HOME, family="TBMT", endpoint=ENDPOINT, adapter_sha=ADAPTER_SHA,
        page_source=source, rule_for=lambda _g: verified_rule(writer),
        limits=RunLimits(max_pages=450, max_records=4000, page_size=10),
        stop_requested=lambda: calls["n"] >= 1,
    )
    span = Interval(DAY.from_utc, NEXT_DAY.to_utc)
    outcomes = runner.backfill("run-1", span, order="OLDEST_FIRST")
    assert len(outcomes) == 1
    assert outcomes[0].status == "INTERRUPTED"
    assert writer.conn.execute("SELECT status FROM coverage_intervals").fetchone()[0] == "PARTIAL"


# ------------------------------------------------------------------ adaptive split

def test_es_truncation_splits_a_day_into_hours_and_unions_coverage(writer) -> None:
    """§4.3: total at the ES window means the query is truncated, so split and merge identity sets."""
    hour_pages = [page_of(0, 4, total=4)]
    portal = FakePortal({"DAY": [page_of(0, 10, total=10_000)], "HOUR": hour_pages})
    runner = make_runner(writer, portal)
    outcome = runner.run_interval(DAY, "DAY", "run-1")
    assert outcome.status == "COMPLETE"
    assert len(outcome.children) == 24
    assert all(child.status == "COMPLETE" for child in outcome.children)
    granularity_seen = {call[0] for call in portal.calls}
    assert granularity_seen == {"DAY", "HOUR"}
    # Only the leaves are recorded; the day is COMPLETE because their union covers it.
    assert writer.conn.execute("SELECT COUNT(*) FROM coverage_intervals").fetchone()[0] == 24
    resolution = coverage.resolve(writer.conn, SOURCE_EGP_HOME, "TBMT", DAY.from_utc, DAY.to_utc)
    assert resolution.status == "COMPLETE" and resolution.can_skip
    assert len(resolution.proven) == 1, "adjacent leaves must union into one span"


def test_split_children_that_fail_keep_the_parent_partial(writer) -> None:
    portal = FakePortal({"DAY": [page_of(0, 10, total=10_000)], "HOUR": [page_of(0, 2, trusted=False)]})
    outcome = make_runner(writer, portal).run_interval(DAY, "DAY", "run-1")
    assert outcome.status == "PARTIAL"
    assert any(child.status == "PARTIAL" for child in outcome.children)
    resolution = coverage.resolve(writer.conn, SOURCE_EGP_HOME, "TBMT", DAY.from_utc, DAY.to_utc)
    assert not resolution.can_skip


def test_a_rule_verified_only_to_day_precision_cannot_certify_an_hour(writer) -> None:
    portal = FakePortal({"DAY": [page_of(0, 10, total=10_000)], "HOUR": [page_of(0, 2)]})
    runner = make_runner(writer, portal)
    runner._rule_for = lambda granularity: verified_rule(writer) if granularity == "DAY" else None
    runner._rule_cache.clear()
    outcome = runner.run_interval(DAY, "DAY", "run-1")
    assert outcome.status == "PARTIAL"
    assert all("BOUNDARY_RULE_UNVERIFIED" in child.blockers for child in outcome.children)


def test_split_stops_at_the_declared_floor(writer) -> None:
    portal = FakePortal({"DAY": [page_of(0, 10, total=10_000)], "HOUR": [page_of(0, 10, total=10_000)]})
    runner = make_runner(writer, portal)
    runner.max_split_granularity = "HOUR"
    outcome = runner.run_interval(DAY, "DAY", "run-1")
    assert len(outcome.children) == 24
    assert all(child.status == "PARTIAL" for child in outcome.children)
    assert portal.calls[1][0] == "HOUR"
    assert {call[0] for call in portal.calls} == {"DAY", "HOUR"}, "no MINUTE work below the floor"


# ------------------------------------------------------------------ backfill

def test_backfill_skips_only_time_the_resolver_proves(writer) -> None:
    proven = FakePortal({"DAY": [page_of(0, 3)]})
    make_runner(writer, proven).run_interval(DAY, "DAY", "run-1")

    later = FakePortal({"DAY": [page_of(0, 4)]})
    outcomes = make_runner(writer, later).backfill("run-2", Interval(DAY.from_utc, NEXT_DAY.to_utc))
    assert len(outcomes) == 1, "the proven day must not be re-fetched"
    assert outcomes[0].interval == NEXT_DAY
    assert later.request_count == 1


def test_backfill_fetches_newest_first_by_default(writer) -> None:
    portal = FakePortal({"DAY": [page_of(0, 1)]})
    runner = make_runner(writer, portal)
    outcomes = runner.backfill("run-1", Interval(DAY.from_utc, NEXT_DAY.to_utc))
    assert [o.interval for o in outcomes] == [NEXT_DAY, DAY]


def test_partial_coverage_of_a_day_leaves_the_rest_as_a_gap_to_fetch(writer) -> None:
    """A PARTIAL interval must not block work; the proven hours are skipped and the rest fetched."""
    half = Interval(DAY.from_utc, DAY.from_utc + timedelta(hours=12))
    portal = FakePortal({"HOUR": [page_of(0, 2)]})
    make_runner(writer, portal, rule=verified_rule(writer, "HOUR")).run_interval(half, "HOUR", "run-1")

    seen: list[Interval] = []

    def source(interval, _granularity, _window):
        seen.append(interval)
        return iter([page_of(0, 1)])

    runner = Runner(
        writer=writer, source_key=SOURCE_EGP_HOME, family="TBMT", endpoint=ENDPOINT, adapter_sha=ADAPTER_SHA,
        page_source=source, rule_for=lambda _g: verified_rule(writer, "HOUR"),
        limits=RunLimits(max_pages=450, max_records=4000, page_size=10),
    )
    runner.backfill("run-2", DAY)
    assert seen and seen[0].from_utc == half.to_utc, "the unproven afternoon is what gets fetched"
    for interval in seen:
        assert interval.from_utc >= half.to_utc


# ------------------------------------------------------------------ incremental

def test_incremental_advances_the_watermark_over_complete_days_only(writer) -> None:
    portal = FakePortal({"DAY": [page_of(0, 2)]})
    runner = make_runner(writer, portal)
    span_end = DAY.to_utc
    outcomes = runner.incremental("run-1", now=span_end)
    assert outcomes
    assert all(o.status == "COMPLETE" for o in outcomes)
    watermark = coverage.watermark_for(writer.conn, SOURCE_EGP_HOME, "TBMT")
    assert watermark is not None and watermark <= span_end


def test_incremental_does_not_jump_the_watermark_over_a_partial_day(writer) -> None:
    """A later COMPLETE day may not pull the horizon past an earlier gap (§4.4)."""
    partial_portal = FakePortal({"DAY": [page_of(0, 10, total=10_000), page_of(1, 10, total=10_000)]})
    partial_runner = make_runner(writer, partial_portal)
    partial_runner.max_split_granularity = "DAY"
    partial_runner.run_interval(DAY, "DAY", "run-1")

    portal = FakePortal({"DAY": [page_of(0, 3)]})
    runner = make_runner(writer, portal)
    runner.incremental("run-2", now=NEXT_DAY.to_utc)
    assert coverage.watermark_for(writer.conn, SOURCE_EGP_HOME, "TBMT") is None


def test_incremental_uses_two_days_of_overlap(writer) -> None:
    portal = FakePortal({"DAY": [page_of(0, 1)]})
    writer.conn.execute(
        "INSERT INTO watermarks (source_key, family, watermark_from_utc, watermark_to_utc,"
        " last_complete_interval_id, updated_at_utc) VALUES (?,?,?,?,?,?)",
        (SOURCE_EGP_HOME, "TBMT", None, DAY.to_utc.astimezone(UTC).isoformat().replace("+00:00", "Z"), None,
         "2026-11-02T00:00:00Z"),
    )
    runner = make_runner(writer, portal)
    outcomes = runner.incremental("run-2", now=NEXT_DAY.to_utc)
    covered = {o.interval for o in outcomes}
    assert DAY in covered, "the watermark day is re-entered so late edits are not missed"


# ------------------------------------------------------------------ reconcile

def test_reconcile_records_absence_without_deleting_anything(writer) -> None:
    portal = FakePortal({"DAY": [page_of(0, 3, prefix="A")]})
    make_runner(writer, portal).run_interval(DAY, "DAY", "run-1")
    before = writer.conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0]

    empty_portal = FakePortal({"DAY": [page_of(0, 0, total=0)]})
    gaps = make_runner(writer, empty_portal).reconcile("run-2", DAY)
    kinds = {g["kind"] for g in gaps}
    assert "MISSING_FROM_SOURCE_IN_VERIFIED_WINDOW" in kinds
    assert writer.conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0] == before, "nothing deleted"
    rows = writer.conn.execute("SELECT kind, evidence_json FROM reconcile_gaps").fetchall()
    assert rows and rows[0]["kind"] == "MISSING_FROM_SOURCE_IN_VERIFIED_WINDOW"
    assert "identity_hash" in rows[0]["evidence_json"]


def test_partial_reconcile_cannot_claim_missing_records(writer) -> None:
    make_runner(writer, FakePortal({"DAY": [page_of(0, 3, prefix="A")]})).run_interval(DAY, "DAY", "run-1")
    incomplete = FakePortal({"DAY": [page_of(0, 1, total=3, prefix="A")]})
    gaps = make_runner(writer, incomplete).reconcile("run-2", DAY)
    assert gaps == [], "a truncated re-scan does not prove that the other records disappeared"


def test_reconcile_flags_new_rows_as_count_drift(writer) -> None:
    portal = FakePortal({"DAY": [page_of(0, 2, prefix="A")]})
    make_runner(writer, portal).run_interval(DAY, "DAY", "run-1")
    grown = FakePortal({"DAY": [page_of(0, 3, prefix="B")]})
    gaps = make_runner(writer, grown).reconcile("run-2", DAY)
    assert any(g["kind"] == "COUNT_DRIFT" for g in gaps)


def test_reconcile_makes_no_claims_when_the_rescan_is_blocked(writer) -> None:
    portal = FakePortal({"DAY": [page_of(0, 2)]})
    make_runner(writer, portal).run_interval(DAY, "DAY", "run-1")
    blocked = FakePortal({"DAY": AccessBlocked("refused")})
    gaps = make_runner(writer, blocked).reconcile("run-2", DAY)
    assert gaps == [], "a scan that never completed cannot assert absence"
    assert writer.conn.execute("SELECT COUNT(*) FROM reconcile_gaps").fetchone()[0] == 0


def test_reconcile_over_an_unproven_interval_writes_nothing(writer) -> None:
    portal = FakePortal({"DAY": [page_of(0, 1)]})
    gaps = make_runner(writer, portal).reconcile("run-1", DAY)
    assert gaps == []


def test_drift_gap_then_resolution_reports_needs_reconcile(writer) -> None:
    portal = FakePortal({"DAY": [page_of(0, 3, prefix="A")]})
    make_runner(writer, portal).run_interval(DAY, "DAY", "run-1")
    grown = FakePortal({"DAY": [page_of(0, 4, prefix="B")]})
    make_runner(writer, grown).reconcile("run-2", DAY)
    resolution = coverage.resolve(writer.conn, SOURCE_EGP_HOME, "TBMT", DAY.from_utc, DAY.to_utc)
    assert resolution.status == "NEEDS_RECONCILE" and not resolution.can_skip

def test_first_incremental_midday_collects_a_complete_vietnamese_day(writer):
    writer.conn.execute("UPDATE filter_boundary_rules SET precision='DAY'")
    writer.conn.commit()
    portal = FakePortal({'DAY':[page_of(0,5)]})
    outcomes = make_runner(writer,portal,rule=verified_rule(writer,precision='DAY')).incremental('run-1',now=NEXT_DAY.from_utc+timedelta(hours=8,minutes=15))
    assert any(row.status == 'COMPLETE' and row.interval == DAY for row in outcomes)
    assert coverage.watermark_for(writer.conn,SOURCE_EGP_HOME,'TBMT') == NEXT_DAY.from_utc
