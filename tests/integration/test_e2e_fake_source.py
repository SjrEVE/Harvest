"""End-to-end over the production path with a fake e-GP portal (§6, §4).

Every layer above the socket is real: `FetchLane` (HTTPS + allowlist + pacing + retry +
classification), `EgpHomeLane` pagination, `extract_page`/`verify_extraction`, the production
`WarehouseWriter`, coverage proofs, reconcile, serving snapshot and the desktop backend. Only
`httpx.MockTransport` replaces the network, so retry/429/403/timeout are the real code paths.

Six families run through the same envelope, exactly as the portal does it.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from fake_egp_portal import FakePortal

from santhau_studio.collectors.extraction import resolve_locator
from santhau_studio.collectors.lane_source import egp_page_source
from santhau_studio.collectors.network_policy import HostRateLimiter
from santhau_studio.collectors.source_registry import FAMILY_ORDER, SOURCE_EGP_HOME, SOURCES
from santhau_studio.domain.coverage import vn_calendar_day
from santhau_studio.domain.identity import canonical_bytes
from santhau_studio.storage import coverage, serving
from santhau_studio.storage.warehouse import WarehouseWriter
from santhau_studio.workers.runner import Runner

ENDPOINT = SOURCES[SOURCE_EGP_HOME].endpoint
ADAPTER_SHA = hashlib.sha256(b"e2e-fake-source").hexdigest()
DAY = vn_calendar_day(date(2026, 11, 1))
EXPECTED_ROWS = 10 + 3  # two pages: full page + short page


@pytest.fixture(autouse=True)
def _fresh_pacing():
    HostRateLimiter.reset_registry()
    yield
    HostRateLimiter.reset_registry()


def make_writer(root: Path) -> WarehouseWriter:
    writer = WarehouseWriter.open(root, create_schema=True, role="e2e")
    for family in FAMILY_ORDER:
        writer.conn.execute(
            "INSERT OR IGNORE INTO filter_boundary_rules (rule_key, rule_version, source_key, family, endpoint,"
            " adapter_sha, boundary_kind, precision, source_timezone, offset_minutes, conversion_params,"
            " canary_verified, verified_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"e2e-{family}", 1, SOURCE_EGP_HOME, family, ENDPOINT, ADAPTER_SHA,
             "CLOSED_LOCAL", "DAY", "Asia/Ho_Chi_Minh", 420, '{"to_minus":"1s"}', 1,
             "2026-10-31T00:00:00Z"),
        )
    writer.conn.commit()
    return writer


def make_runner(writer: WarehouseWriter, family: str, portal: FakePortal) -> Runner:
    return Runner(
        writer=writer,
        source_key=SOURCE_EGP_HOME,
        family=family,
        endpoint=ENDPOINT,
        adapter_sha=ADAPTER_SHA,
        page_source=egp_page_source(family=family, lane_factory=portal.lane),
    )


def run_family(root: Path, family: str, portal: FakePortal, run_id: str = "run-e2e"):
    writer = make_writer(root)
    try:
        writer.register_run(run_id, f"job-{run_id}", "BACKFILL")
        outcomes = make_runner(writer, family, portal).backfill(run_id, DAY)
        return outcomes
    finally:
        writer.close()


@pytest.mark.parametrize("family", FAMILY_ORDER)
def test_six_families_reach_a_complete_proof(tmp_path: Path, family: str) -> None:
    root = tmp_path / "data"
    writer = make_writer(root)
    try:
        writer.register_run("run-e2e", "job-e2e", "BACKFILL")
        portal = FakePortal()
        outcomes = make_runner(writer, family, portal).backfill("run-e2e", DAY)
        assert [outcome.status for outcome in outcomes] == ["COMPLETE"], [
            outcome.blockers for outcome in outcomes
        ]
        conn = writer.conn
        records = conn.execute(
            "SELECT COUNT(*) FROM source_records WHERE family=?", (family,)
        ).fetchone()[0]
        versions = conn.execute(
            "SELECT COUNT(*) FROM source_record_versions v JOIN source_records sr"
            " ON sr.id = v.source_record_id WHERE sr.family=?", (family,)
        ).fetchone()[0]
        observations = conn.execute(
            "SELECT COUNT(*) FROM observations WHERE family=?", (family,)
        ).fetchone()[0]
        assert (records, versions, observations) == (EXPECTED_ROWS, EXPECTED_ROWS, EXPECTED_ROWS)
        proof = conn.execute(
            "SELECT cp.distinct_ids, cp.request_count, cp.attempt_count, cp.total_trusted, cp.terminal_evidence_json"
            " FROM coverage_proofs cp WHERE cp.family=?", (family,)
        ).fetchone()
        assert proof["distinct_ids"] == EXPECTED_ROWS and proof["total_trusted"] == 1
        assert proof["request_count"] == 2
        # Proof ⇒ the resolver may skip this day (§4.5).
        resolution = coverage.resolve(conn, SOURCE_EGP_HOME, family, DAY.from_utc, DAY.to_utc)
        assert resolution.can_skip

        # Locator + digest verified on the production writer: the stored response resolves the
        # recorded locator to bytes whose canonical hash is the recorded record_object_sha.
        for row in conn.execute(
            "SELECT o.record_locator, o.record_object_sha, o.response_object_sha FROM observations o"
            " WHERE o.family=? LIMIT 5", (family,)
        ):
            response = json.loads(writer.store.read(row["response_object_sha"]))
            resolved = resolve_locator(response, row["record_locator"])
            digest = hashlib.sha256(canonical_bytes(resolved)).hexdigest()
            assert digest == row["record_object_sha"]
            writer.store.read(row["record_object_sha"])  # raises if bytes/hash disagree

        # Version semantics per family: no "00" invention, no silent coercion.
        states = conn.execute(
            "SELECT version_state, version_raw, COUNT(*) AS n FROM source_records"
            " WHERE family=? GROUP BY version_state, version_raw ORDER BY version_state", (family,)
        ).fetchall()
        by_state = {(row["version_state"], row["version_raw"]): row["n"] for row in states}
        if family == "YCBG":
            assert by_state == {("MISSING", None): EXPECTED_ROWS}
        elif family == "DU_AN":
            assert by_state[("UNPARSABLE", "abc")] == 1
            assert by_state[("KNOWN", "01")] == EXPECTED_ROWS - 1
        else:
            assert all(state == "KNOWN" and raw not in (None, "") for state, raw in by_state)
    finally:
        writer.close()


def test_retry_after_429_counts_attempts_not_requests(tmp_path: Path) -> None:
    root = tmp_path / "data"
    writer = make_writer(root)
    try:
        writer.register_run("run-retry", "job-retry", "BACKFILL")
        portal = FakePortal(faults={"KQLCNT": ["429"]})
        outcomes = make_runner(writer, "KQLCNT", portal).backfill("run-retry", DAY)
        assert outcomes[0].status == "COMPLETE"
        proof = writer.conn.execute(
            "SELECT request_count, attempt_count, attempt_error_count, unrecovered_request_count"
            " FROM coverage_proofs WHERE family='KQLCNT'"
        ).fetchone()
        assert proof["request_count"] == 2
        assert proof["attempt_count"] == 3  # one retry on page 0
        assert proof["attempt_error_count"] == 1
        assert proof["unrecovered_request_count"] == 0
        attempts = [tuple(row) for row in writer.conn.execute(
            "SELECT fa.outcome, fa.error_kind FROM fetch_attempts fa"
            " JOIN fetch_requests fr ON fr.id = fa.request_id WHERE fr.run_id='run-retry' ORDER BY fa.id"
        )]
        assert [outcome for outcome, _kind in attempts].count("OK") == 2
        assert ("HTTP_ERROR", "RATE_LIMIT_429") in attempts, attempts
    finally:
        writer.close()


def test_403_blocks_the_interval_without_a_proof(tmp_path: Path) -> None:
    root = tmp_path / "data"
    writer = make_writer(root)
    try:
        writer.register_run("run-block", "job-block", "BACKFILL")
        portal = FakePortal(faults={"TBMT": ["403"]})
        outcomes = make_runner(writer, "TBMT", portal).backfill("run-block", DAY)
        assert outcomes[0].status == "BLOCKED"
        assert any("ACCESS_BLOCKED" in blocker for blocker in outcomes[0].blockers)
        assert writer.conn.execute("SELECT COUNT(*) FROM coverage_proofs").fetchone()[0] == 0
        interval = writer.conn.execute("SELECT status FROM coverage_intervals").fetchone()
        assert interval["status"] == "BLOCKED"
    finally:
        writer.close()


def test_timeout_is_partial_and_unrecovered(tmp_path: Path) -> None:
    root = tmp_path / "data"
    writer = make_writer(root)
    try:
        writer.register_run("run-timeout", "job-timeout", "BACKFILL")
        portal = FakePortal(faults={"YCBG": ["timeout", "timeout", "timeout"]})
        outcomes = make_runner(writer, "YCBG", portal).backfill("run-timeout", DAY)
        assert outcomes[0].status == "PARTIAL"
        assert any("REQUEST_UNRECOVERED" in blocker for blocker in outcomes[0].blockers)
        assert writer.conn.execute("SELECT COUNT(*) FROM coverage_proofs").fetchone()[0] == 0
    finally:
        writer.close()


def test_overlap_rerun_does_not_duplicate(tmp_path: Path) -> None:
    root = tmp_path / "data"
    writer = make_writer(root)
    try:
        writer.register_run("run-overlap", "job-overlap", "BACKFILL")
        runner = make_runner(writer, "TBMT", FakePortal())
        first = runner.backfill("run-overlap", DAY, skip_proven=False)
        def counts(table: str) -> int:
            return writer.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        once = (counts("source_records"), counts("source_record_versions"), counts("observations"))
        # A resumed/overlapping pass is a new run (D4); reusing one run_id would reuse the
        # request/attempt key of a page already fetched by the same run.
        writer.register_run("run-overlap-2", "job-overlap", "BACKFILL")
        second = make_runner(writer, "TBMT", FakePortal()).backfill("run-overlap-2", DAY, skip_proven=False)
        twice = (counts("source_records"), counts("source_record_versions"), counts("observations"))
        assert first[0].status == second[0].status == "COMPLETE"
        assert twice[0] == once[0], "overlap must not create a second record"
        assert twice[1] == once[1], "identical content must not create a new content version"
        assert twice[2] == once[2] * 2, "each pass observes the same records again"
    finally:
        writer.close()


def test_reconcile_reports_gaps_without_deleting_records(tmp_path: Path) -> None:
    root = tmp_path / "data"
    writer = make_writer(root)
    try:
        writer.register_run("run-rec", "job-rec", "BACKFILL")
        make_runner(writer, "TBMT", FakePortal()).backfill("run-rec", DAY)
        before = writer.conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0]
        writer.register_run("run-rec-2", "job-rec", "RECONCILE")
        gaps = make_runner(writer, "TBMT", FakePortal(drop_doc=("TBMT", 0))).reconcile("run-rec-2", DAY)
        assert any(gap["kind"] == "MISSING_FROM_SOURCE_IN_VERIFIED_WINDOW" for gap in gaps)
        after = writer.conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0]
        assert after == before, "RECONCILE never deletes source records"
        stored = writer.conn.execute(
            "SELECT COUNT(*) FROM reconcile_gaps WHERE kind='MISSING_FROM_SOURCE_IN_VERIFIED_WINDOW'"
        ).fetchone()[0]
        assert stored >= 1
    finally:
        writer.close()


def test_restart_after_collection_leaves_an_intact_store(tmp_path: Path) -> None:
    root = tmp_path / "data"
    writer = make_writer(root)
    try:
        writer.register_run("run-restart", "job-restart", "BACKFILL")
        make_runner(writer, "KHLCNT", FakePortal()).backfill("run-restart", DAY)
    finally:
        writer.close()
    reopened = WarehouseWriter.open(root)  # schema already exists; this is the restart path
    try:
        assert reopened.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert reopened.conn.execute("PRAGMA foreign_key_check").fetchall() == []
        report = reopened.store.recover(reopened.conn)
        assert report.missing_files == () and report.unreadable == ()
        assert report.orphan_files == ()
    finally:
        reopened.close()


def test_serving_snapshot_and_ui_read_all_six_families(tmp_path: Path) -> None:
    from santhau_studio.desktop.backend import StudioBackend

    root = tmp_path / "data"
    writer = make_writer(root)
    try:
        for family in FAMILY_ORDER:
            run_id = f"run-{family.lower()}"
            writer.register_run(run_id, f"job-{family.lower()}", "BACKFILL")
            outcomes = make_runner(writer, family, FakePortal()).backfill(run_id, DAY)
            assert outcomes[0].status == "COMPLETE", (family, outcomes[0].blockers)
        writer.conn.execute(
            "INSERT INTO source_registry (source_key, display_name, endpoint_family, access_state)"
            " VALUES (?,?,?,?)",
            (SOURCE_EGP_HOME, "e-GP quốc gia", "egp", "ALLOWED_AND_VERIFIED"),
        )
        writer.conn.commit()
    finally:
        writer.close()

    published = serving.build_serving_db(root / "warehouse.sqlite", root / "serving.sqlite")
    assert published.published, published.reason

    backend = StudioBackend(root)
    center = backend.control_center()
    families = {entry["family"]: entry for entry in center["families"]}
    assert set(families) == set(FAMILY_ORDER)
    assert all(entry["records"] == EXPECTED_ROWS for entry in families.values())

    coverage_payload = backend.coverage(None)
    assert len(coverage_payload["summary"]) == len(FAMILY_ORDER)

    hits = backend.search({"text": "gói thầu"})["hits"]
    assert any(hit["family"] == "TBMT" for hit in hits), hits
    ycbg = backend.search({"text": "yêu cầu báo giá", "family": "YCBG"})["hits"]
    assert ycbg and all(hit["version_state"] == "MISSING" for hit in ycbg)

    detail_id = ycbg[0]["record_id"]
    detail = backend.record_detail(detail_id)
    assert detail["present"] and detail["record"]["version_state"] == "MISSING"
    assert detail["record"]["version_raw"] is None

    with contextlib.closing(sqlite3.connect(root / "serving.sqlite")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM search_index").fetchone()[0] == EXPECTED_ROWS * 6


def test_pipeline_over_the_segment_layout(tmp_path: Path) -> None:
    """The production ingest layout (segments) carries the same proof and serving guarantees."""
    root = tmp_path / "data"
    writer = WarehouseWriter.open(root, create_schema=True, role="e2e", raw_layout="segments")
    try:
        writer.conn.execute(
            "INSERT OR IGNORE INTO filter_boundary_rules (rule_key, rule_version, source_key, family, endpoint,"
            " adapter_sha, boundary_kind, precision, source_timezone, offset_minutes, conversion_params,"
            " canary_verified, verified_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("e2e-seg", 1, SOURCE_EGP_HOME, "TBMT", ENDPOINT, ADAPTER_SHA,
             "CLOSED_LOCAL", "DAY", "Asia/Ho_Chi_Minh", 420, '{"to_minus":"1s"}', 1,
             "2026-10-31T00:00:00Z"),
        )
        writer.conn.commit()
        writer.register_run("run-seg", "job-seg", "BACKFILL")
        outcomes = make_runner(writer, "TBMT", FakePortal()).backfill("run-seg", DAY)
        assert outcomes[0].status == "COMPLETE", outcomes[0].blockers
        assert (root / "raw" / "segments").is_dir()
        assert not list((root / "raw" / "objects").rglob("*.bin.gz")), "new bytes go to segments"
        rows = writer.conn.execute(
            "SELECT o.response_object_sha, o.record_object_sha FROM observations o LIMIT 3"
        ).fetchall()
        for row in rows:
            for sha in row:
                writer.store.read(sha)  # segment read + hash verification
        report = writer.store.recover(writer.conn)
        assert report.missing_files == () and report.unreadable == ()
        count, bad = writer.store.segments.verify_all()
        assert count == 2 + EXPECTED_ROWS and bad == ()  # two page payloads + 13 records
    finally:
        writer.close()
