"""Coverage persistence: one transaction for proof+interval, and a watermark that cannot lie.

These tests exist because §4.1's guarantees are easy to write down and easy to break in the
writer: an interval marked COMPLETE with no proof row, or a proof whose transaction committed
separately from its interval, both leave a database that *looks* verified.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from santhau_studio.domain.coverage import Interval
from santhau_studio.storage import coverage
from santhau_studio.storage.warehouse import WarehouseWriter, connect

DAY_START = datetime(2026, 10, 31, 17, 0, tzinfo=UTC)  # 2026-11-01 00:00 VN


def day_interval(offset_days: int = 0) -> Interval:
    start = DAY_START + timedelta(days=offset_days)
    return Interval(start, start + timedelta(days=1))


def make_proof(interval: Interval, *, boundary_rule_id: int, complete: bool = True) -> coverage.ProofRecord:
    return coverage.ProofRecord(
        proof_version=1,
        source_key="egp_home",
        family="TBMT",
        interval=interval,
        granularity="DAY",
        boundary_rule_id=boundary_rule_id,
        source_query_fingerprint=hashlib.sha256(b"query").hexdigest(),
        adapter_sha=hashlib.sha256(b"adapter").hexdigest(),
        request_count=12,
        attempt_count=12,
        attempt_error_count=0 if complete else 1,
        unrecovered_request_count=0 if complete else 1,
        malformed_count=0,
        distinct_ids=118,
        ids_union_hash=hashlib.sha256(b"ids").hexdigest(),
        total_first=118 if complete else None,
        total_last=118 if complete else None,
        total_trusted=1 if complete else 0,
        terminal_evidence={"kind": "SHORT_PAGE", "last_page_rows": 8} if complete else {},
        page_size=10,
        max_pages_hit=0,
        max_records_hit=0,
        verified_at_utc="2026-11-02T00:00:00Z",
    )


@pytest.fixture()
def env(tmp_path: Path):
    data_root = tmp_path / "data"
    writer = WarehouseWriter.open(data_root, create_schema=True, role="verify")
    conn = writer.conn
    rule_sha = hashlib.sha256(b"adapter").hexdigest()
    conn.execute(
        "INSERT INTO filter_boundary_rules (rule_key, rule_version, source_key, family, endpoint, adapter_sha,"
        " boundary_kind, precision, source_timezone, offset_minutes, conversion_params, canary_verified,"
        " verified_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "egp-tbmt-day", 1, "egp_home", "TBMT", "/o/egp-portal-home/services/smart/search", rule_sha,
            "CLOSED_LOCAL", "DAY", "Asia/Ho_Chi_Minh", 420,
            json.dumps({"to_minus": "1ms"}), 1, "2026-10-31T00:00:00Z",
        ),
    )
    rule_id = int(conn.execute("SELECT id FROM filter_boundary_rules WHERE rule_key='egp-tbmt-day'").fetchone()[0])
    yield conn, rule_id, rule_sha
    writer.close()


# ------------------------------------------------------------------ proof + interval atomicity

def test_verified_complete_interval_resolves_as_skippable(env) -> None:
    conn, rule_id, _sha = env
    interval = day_interval()
    proof = make_proof(interval, boundary_rule_id=rule_id)
    interval_id = coverage.record_interval(
        conn, run_id="run-1", status="COMPLETE", proof=proof,
        source_key="egp_home", family="TBMT", interval=interval, granularity="DAY",
    )
    assert interval_id > 0
    assert conn.execute("SELECT COUNT(*) FROM coverage_proofs").fetchone()[0] == 1
    resolution = coverage.resolve(conn, "egp_home", "TBMT", interval.from_utc, interval.to_utc)
    assert resolution.status == "COMPLETE"
    assert resolution.can_skip
    assert resolution.proven == (interval,)
    assert resolution.gaps == ()
    assert resolution.proof_ids


def test_complete_without_a_proof_is_refused_before_the_database(env) -> None:
    conn, _rule_id, _sha = env
    with pytest.raises(ValueError, match="requires a proof"):
        coverage.record_interval(
            conn, run_id="run-1", status="COMPLETE", proof=None,
            source_key="egp_home", family="TBMT", interval=day_interval(), granularity="DAY",
        )
    assert conn.execute("SELECT COUNT(*) FROM coverage_intervals").fetchone()[0] == 0


def test_proof_and_interval_are_written_in_one_transaction(env) -> None:
    """A dangling boundary rule id must roll the proof back too, not leave it stranded."""
    conn, rule_id, _sha = env
    interval = day_interval()
    broken = make_proof(interval, boundary_rule_id=rule_id)
    object.__setattr__(broken, "boundary_rule_id", 999_999)
    with pytest.raises(sqlite3.IntegrityError):
        coverage.record_interval(
            conn, run_id="run-1", status="COMPLETE", proof=broken,
            source_key="egp_home", family="TBMT", interval=interval, granularity="DAY",
        )
    assert conn.execute("SELECT COUNT(*) FROM coverage_proofs").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM coverage_intervals").fetchone()[0] == 0
    assert not conn.in_transaction


def test_interval_row_carries_the_proof_id(env) -> None:
    conn, rule_id, _sha = env
    interval = day_interval()
    interval_id = coverage.record_interval(
        conn, run_id="run-1", status="COMPLETE", proof=make_proof(interval, boundary_rule_id=rule_id),
        source_key="egp_home", family="TBMT", interval=interval, granularity="DAY",
    )
    row = conn.execute("SELECT proof_id, status, granularity FROM coverage_intervals WHERE id=?", (interval_id,)).fetchone()
    assert row["proof_id"] is not None and row["status"] == "COMPLETE" and row["granularity"] == "DAY"


def test_proof_interval_mismatch_is_a_programmer_error(env) -> None:
    conn, rule_id, _sha = env
    with pytest.raises(ValueError, match="same span"):
        coverage.record_interval(
            conn, run_id="run-1", status="COMPLETE", proof=make_proof(day_interval(1), boundary_rule_id=rule_id),
            source_key="egp_home", family="TBMT", interval=day_interval(0), granularity="DAY",
        )


def test_partial_interval_is_recorded_without_a_proof(env) -> None:
    conn, _rule_id, _sha = env
    interval = day_interval()
    coverage.record_interval(
        conn, run_id="run-1", status="PARTIAL", proof=None,
        source_key="egp_home", family="TBMT", interval=interval, granularity="DAY",
    )
    resolution = coverage.resolve(conn, "egp_home", "TBMT", interval.from_utc, interval.to_utc)
    assert resolution.status != "COMPLETE"
    assert not resolution.can_skip
    assert resolution.gaps == (interval,)


# ------------------------------------------------------------------ blockers

def test_evidence_that_falls_short_lists_every_blocker() -> None:
    interval = day_interval()
    weak = make_proof(interval, boundary_rule_id=1, complete=False)
    blockers = weak.complete_blockers()
    assert "UNRECOVERED_REQUESTS=1" in blockers
    assert "TOTAL_UNTRUSTED" in blockers
    assert "NO_TOTAL_OBSERVED" in blockers
    assert "NO_TERMINAL_EVIDENCE" in blockers
    strong = make_proof(interval, boundary_rule_id=1)
    assert strong.complete_blockers() == ()


def test_changed_total_between_first_and_last_is_not_complete() -> None:
    interval = day_interval()
    proof = make_proof(interval, boundary_rule_id=1)
    object.__setattr__(proof, "total_last", 119)
    assert any(blocker.startswith("TOTAL_CHANGED") for blocker in proof.complete_blockers())


def test_distinct_below_trusted_total_is_not_complete() -> None:
    interval = day_interval()
    proof = make_proof(interval, boundary_rule_id=1)
    object.__setattr__(proof, "distinct_ids", 110)
    assert "DISTINCT_VS_TOTAL 110!=118" in proof.complete_blockers()


def test_hitting_a_cap_forbids_complete_even_with_terminal_looking_pages() -> None:
    """COV-15: max_pages or max_records reached means PARTIAL, whatever the last page looked like."""
    interval = day_interval()
    for field, value in (("max_pages_hit", 1), ("max_records_hit", 4000)):
        proof = make_proof(interval, boundary_rule_id=1)
        object.__setattr__(proof, field, value)
        assert proof.complete_blockers(), f"{field} must block COMPLETE"


def test_total_at_the_es_window_does_not_require_distinct_equals_total() -> None:
    interval = day_interval()
    proof = make_proof(interval, boundary_rule_id=1)
    object.__setattr__(proof, "total_first", 10_000)
    object.__setattr__(proof, "total_last", 10_000)
    object.__setattr__(proof, "distinct_ids", 9_840)
    assert not any(blocker.startswith("DISTINCT_VS_TOTAL") for blocker in proof.complete_blockers())


# ------------------------------------------------------------------ watermark

def test_watermark_advances_only_over_verified_complete(env) -> None:
    conn, rule_id, _sha = env
    interval = day_interval()
    interval_id = coverage.record_interval(
        conn, run_id="run-1", status="COMPLETE", proof=make_proof(interval, boundary_rule_id=rule_id),
        source_key="egp_home", family="TBMT", interval=interval, granularity="DAY",
    )
    coverage.advance_watermark(
        conn, source_key="egp_home", family="TBMT", to_utc=interval.to_utc, complete_interval_id=interval_id
    )
    assert coverage.watermark_for(conn, "egp_home", "TBMT") == interval.to_utc


def test_watermark_refuses_a_partial_interval(env) -> None:
    conn, _rule_id, _sha = env
    interval = day_interval()
    partial_id = coverage.record_interval(
        conn, run_id="run-1", status="PARTIAL", proof=None,
        source_key="egp_home", family="TBMT", interval=interval, granularity="DAY",
    )
    with pytest.raises(ValueError, match="verified COMPLETE"):
        coverage.advance_watermark(
            conn, source_key="egp_home", family="TBMT", to_utc=interval.to_utc, complete_interval_id=partial_id
        )
    assert coverage.watermark_for(conn, "egp_home", "TBMT") is None


def test_watermark_never_moves_backwards(env) -> None:
    conn, rule_id, _sha = env
    later = day_interval(2)
    first_id = coverage.record_interval(
        conn, run_id="run-1", status="COMPLETE", proof=make_proof(later, boundary_rule_id=rule_id),
        source_key="egp_home", family="TBMT", interval=later, granularity="DAY",
    )
    coverage.advance_watermark(
        conn, source_key="egp_home", family="TBMT", to_utc=later.to_utc, complete_interval_id=first_id
    )
    earlier = day_interval()
    second_id = coverage.record_interval(
        conn, run_id="run-1", status="COMPLETE", proof=make_proof(earlier, boundary_rule_id=rule_id),
        source_key="egp_home", family="TBMT", interval=earlier, granularity="DAY",
    )
    coverage.advance_watermark(
        conn, source_key="egp_home", family="TBMT", to_utc=earlier.to_utc, complete_interval_id=second_id
    )
    assert coverage.watermark_for(conn, "egp_home", "TBMT") == later.to_utc


def test_watermark_refuses_an_unknown_interval(env) -> None:
    conn, _rule_id, _sha = env
    with pytest.raises(ValueError, match="verified COMPLETE interval"):
        coverage.advance_watermark(
            conn, source_key="egp_home", family="TBMT", to_utc=day_interval().to_utc, complete_interval_id=999_999
        )


def test_watermark_does_not_jump_over_an_unproven_gap(env) -> None:
    """A proven day two days ahead must not pull the horizon past the unproven day between."""
    conn, rule_id, _sha = env
    first = day_interval()
    first_id = coverage.record_interval(
        conn, run_id="run-1", status="COMPLETE", proof=make_proof(first, boundary_rule_id=rule_id),
        source_key="egp_home", family="TBMT", interval=first, granularity="DAY",
    )
    assert coverage.advance_watermark(
        conn, source_key="egp_home", family="TBMT", to_utc=first.to_utc, complete_interval_id=first_id
    )
    far = day_interval(3)
    far_id = coverage.record_interval(
        conn, run_id="run-1", status="COMPLETE", proof=make_proof(far, boundary_rule_id=rule_id),
        source_key="egp_home", family="TBMT", interval=far, granularity="DAY",
    )
    assert not coverage.advance_watermark(
        conn, source_key="egp_home", family="TBMT", to_utc=far.to_utc, complete_interval_id=far_id
    )
    assert coverage.watermark_for(conn, "egp_home", "TBMT") == first.to_utc


def test_adjacent_complete_interval_does_advance(env) -> None:
    conn, rule_id, _sha = env
    first, second = day_interval(), day_interval(1)
    first_id = coverage.record_interval(
        conn, run_id="run-1", status="COMPLETE", proof=make_proof(first, boundary_rule_id=rule_id),
        source_key="egp_home", family="TBMT", interval=first, granularity="DAY",
    )
    coverage.advance_watermark(
        conn, source_key="egp_home", family="TBMT", to_utc=first.to_utc, complete_interval_id=first_id
    )
    second_id = coverage.record_interval(
        conn, run_id="run-1", status="COMPLETE", proof=make_proof(second, boundary_rule_id=rule_id),
        source_key="egp_home", family="TBMT", interval=second, granularity="DAY",
    )
    assert coverage.advance_watermark(
        conn, source_key="egp_home", family="TBMT", to_utc=second.to_utc, complete_interval_id=second_id
    )
    assert coverage.watermark_for(conn, "egp_home", "TBMT") == second.to_utc


# ------------------------------------------------------------------ staleness and boundaries

def test_drift_gap_makes_a_proven_interval_stale(env) -> None:
    conn, rule_id, _sha = env
    interval = day_interval()
    coverage.record_interval(
        conn, run_id="run-1", status="COMPLETE", proof=make_proof(interval, boundary_rule_id=rule_id),
        source_key="egp_home", family="TBMT", interval=interval, granularity="DAY",
    )
    conn.execute(
        "INSERT INTO reconcile_gaps (run_id, source_key, family, interval_from_utc, interval_to_utc, kind,"
        " evidence_json, detected_at_utc) VALUES (?,?,?,?,?,?,?,?)",
        ("run-2", "egp_home", "TBMT", interval.from_utc.isoformat(), interval.to_utc.isoformat(), "HASH_DRIFT",
         json.dumps({"identity_key": "x"}), "2026-11-03T00:00:00Z"),
    )
    resolution = coverage.resolve(conn, "egp_home", "TBMT", interval.from_utc, interval.to_utc)
    # Frozen vocabulary: the status is NEEDS_RECONCILE and the reason carries the
    # spec's STALE_NEEDS_RECONCILE label (DATA_CONTRACT resolution status enum).
    assert resolution.status == "NEEDS_RECONCILE"
    assert any("STALE_NEEDS_RECONCILE" in reason for reason in resolution.reasons)
    assert not resolution.can_skip
    assert resolution.stale == (interval,)


def test_acknowledged_drift_no_longer_marks_stale(env) -> None:
    conn, rule_id, _sha = env
    interval = day_interval()
    coverage.record_interval(
        conn, run_id="run-1", status="COMPLETE", proof=make_proof(interval, boundary_rule_id=rule_id),
        source_key="egp_home", family="TBMT", interval=interval, granularity="DAY",
    )
    conn.execute(
        "INSERT INTO reconcile_gaps (run_id, source_key, family, interval_from_utc, interval_to_utc, kind,"
        " evidence_json, detected_at_utc, acknowledged_at_utc) VALUES (?,?,?,?,?,?,?,?,?)",
        ("run-2", "egp_home", "TBMT", interval.from_utc.isoformat(), interval.to_utc.isoformat(), "HASH_DRIFT",
         json.dumps({"identity_key": "x"}), "2026-11-03T00:00:00Z", "2026-11-03T01:00:00Z"),
    )
    assert coverage.resolve(conn, "egp_home", "TBMT", interval.from_utc, interval.to_utc).status == "COMPLETE"


def test_absence_finding_does_not_make_a_proven_interval_stale(env) -> None:
    """RECONCILE asserting a record is gone from the source is a finding, not a coverage defect."""
    conn, rule_id, _sha = env
    interval = day_interval()
    coverage.record_interval(
        conn, run_id="run-1", status="COMPLETE", proof=make_proof(interval, boundary_rule_id=rule_id),
        source_key="egp_home", family="TBMT", interval=interval, granularity="DAY",
    )
    conn.execute(
        "INSERT INTO reconcile_gaps (run_id, source_key, family, interval_from_utc, interval_to_utc, kind,"
        " evidence_json, detected_at_utc) VALUES (?,?,?,?,?,?,?,?)",
        ("run-2", "egp_home", "TBMT", interval.from_utc.isoformat(), interval.to_utc.isoformat(),
         "MISSING_FROM_SOURCE_IN_VERIFIED_WINDOW", json.dumps({"identity_key": "x"}), "2026-11-03T00:00:00Z"),
    )
    assert coverage.resolve(conn, "egp_home", "TBMT", interval.from_utc, interval.to_utc).status == "COMPLETE"


def test_boundary_rule_lookup_is_exact_on_adapter_and_endpoint(env) -> None:
    conn, rule_id, rule_sha = env
    endpoint = "/o/egp-portal-home/services/smart/search"
    found = coverage.boundary_rule(conn, source_key="egp_home", family="TBMT", endpoint=endpoint, adapter_sha=rule_sha)
    assert found["id"] == rule_id and found["boundary_kind"] == "CLOSED_LOCAL"
    assert coverage.boundary_rule(conn, source_key="egp_home", family="TBMT", endpoint=endpoint,
                                  adapter_sha="0" * 64) is None
    assert coverage.boundary_rule(conn, source_key="egp_home", family="KQLCNT", endpoint=endpoint,
                                  adapter_sha=rule_sha) is None


def test_unverified_boundary_rule_is_never_offered(env) -> None:
    conn, _rule_id, rule_sha = env
    conn.execute(
        "INSERT INTO filter_boundary_rules (rule_key, rule_version, source_key, family, endpoint, adapter_sha,"
        " boundary_kind, precision, source_timezone, offset_minutes, conversion_params, canary_verified)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,0)",
        ("egp-tbmt-day", 2, "egp_home", "TBMT", "/o/egp-portal-home/services/smart/search", rule_sha,
         "UNKNOWN", "DAY", "Asia/Ho_Chi_Minh", 420, json.dumps({})),
    )
    found = coverage.boundary_rule(
        conn, source_key="egp_home", family="TBMT", endpoint="/o/egp-portal-home/services/smart/search",
        adapter_sha=rule_sha,
    )
    assert found["rule_version"] == 1, "a newer but unverified rule must not displace a verified one"


# ------------------------------------------------------------------ resolution shape

def test_partial_and_complete_neighbours_are_reported_disjointly(env) -> None:
    conn, rule_id, _sha = env
    covered = day_interval()
    coverage.record_interval(
        conn, run_id="run-1", status="COMPLETE", proof=make_proof(covered, boundary_rule_id=rule_id),
        source_key="egp_home", family="TBMT", interval=covered, granularity="DAY",
    )
    window = Interval(covered.from_utc - timedelta(hours=12), covered.to_utc + timedelta(hours=12))
    resolution = coverage.resolve(conn, "egp_home", "TBMT", window.from_utc, window.to_utc)
    assert resolution.proven == (covered,)
    assert sum((gap.duration for gap in resolution.gaps), timedelta(0)) == window.duration - covered.duration
    assert resolution.status != "COMPLETE"
    assert 0 < resolution.proven_fraction < 1


def test_complete_older_beats_a_newer_partial_for_the_same_span(env) -> None:
    """§4.5 precedence: MAX(id) would silently drop proven coverage."""
    conn, rule_id, _sha = env
    interval = day_interval()
    coverage.record_interval(
        conn, run_id="run-1", status="COMPLETE", proof=make_proof(interval, boundary_rule_id=rule_id),
        source_key="egp_home", family="TBMT", interval=interval, granularity="DAY",
    )
    coverage.record_interval(
        conn, run_id="run-2", status="PARTIAL", proof=None,
        source_key="egp_home", family="TBMT", interval=interval, granularity="DAY",
    )
    resolution = coverage.resolve(conn, "egp_home", "TBMT", interval.from_utc, interval.to_utc)
    assert resolution.status == "COMPLETE" and resolution.proven == (interval,)


def test_naive_datetime_is_refused_rather_than_assumed_utc(tmp_path: Path) -> None:
    conn = connect(tmp_path / "x.sqlite", create_schema=True)
    with pytest.raises(ValueError, match="timezone-aware"):
        coverage.resolve(conn, "egp_home", "TBMT", datetime(2026, 10, 31), datetime(2026, 11, 1))
    conn.close()


def test_granularity_split_chain_stops_at_the_bottom() -> None:
    assert coverage.finer_granularity("DAY") == "HOUR"
    assert coverage.finer_granularity("MILLISECOND") is None
    assert coverage.granularity_rank("HOUR") > coverage.granularity_rank("DAY")
