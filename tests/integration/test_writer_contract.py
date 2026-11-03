"""Contract invariants executed through the PRODUCTION writer, not test helpers.

Closes V-1: the G0-B suite proved the schema *permits* the §3.5 decision table. These tests
prove the real code path produces the right rows — extraction, derivation, hashing, identity,
versions, revisions, receipts.

They earned their keep immediately: the first run exposed that current_content_version was
being set to the version row's primary key, so a record with two content versions reported
"4". A schema-only test could never have caught that.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from santhau_studio.collectors.extraction import (
    LocatorError,
    extract_page,
    resolve_locator,
    verify_extraction,
)
from santhau_studio.collectors.source_registry import binding_for
from santhau_studio.domain.records import (
    IntegrityMismatchError,
    NormalizedRecord,
    RecordContractError,
)
from santhau_studio.storage.rawstore import RawObjectStore
from santhau_studio.storage.warehouse import WarehouseWriter, connect


@pytest.fixture()
def env(tmp_path: Path):
    store = RawObjectStore(tmp_path / "data")
    conn = connect(tmp_path / "data" / "warehouse.sqlite", create_schema=True)
    writer = WarehouseWriter(conn, store)
    writer.register_run("run-1", "job-1", "BACKFILL")
    yield writer, conn, store
    conn.close()


def page_of(records: list[dict]) -> dict:
    return {"code": 200, "page": {"content": records}}


def commit(writer, page: dict, *, attempt_no: int = 1, family: str = "TBMT", page_no: int = 0):
    """Run the full production path for one page."""
    binding = binding_for(family)
    response_ref = writer.store_response(page)
    attempt = writer.ensure_attempt(
        run_id="run-1", family=family, endpoint="smart/search",
        interval_from="2026-10-31T00:00:00Z", interval_to="2026-11-01T00:00:00Z",
        page_no=page_no, attempt_no=attempt_no, outcome="OK", response=response_ref,
    )
    report = extract_page(page, binding.spec)
    verify_extraction(page, report)
    return writer.commit_page(run_id="run-1", attempt_id=attempt, response_ref=response_ref, records=report.records)


def scalar(conn: sqlite3.Connection, sql: str, *args: object):
    return conn.execute(sql, args).fetchone()[0]


# ------------------------------------------------------------------ idempotency

def test_ten_identical_fetches_yield_one_record_one_version(env) -> None:
    """DEDUP-01 through the writer: records=1, versions=1, revisions=1, observations=10."""
    writer, conn, _ = env
    page = page_of([{"notifyNo": "IB26777", "notifyVersion": "00", "bidName": "t"}])
    for i in range(10):
        commit(writer, page, attempt_no=i + 1)
    assert scalar(conn, "SELECT COUNT(*) FROM source_records") == 1
    assert scalar(conn, "SELECT COUNT(*) FROM source_record_versions") == 1
    assert scalar(conn, "SELECT COUNT(*) FROM raw_revisions") == 1
    assert scalar(conn, "SELECT COUNT(*) FROM observations") == 10


def test_abab_produces_three_content_versions(env) -> None:
    """DEDUP-02 / §13.3 decision 2: A→B→A is three versions; the trailing repeat adds none."""
    writer, conn, _ = env
    for i, title in enumerate(("A", "B", "A", "A")):
        commit(writer, page_of([{"notifyNo": "IB26778", "notifyVersion": "00", "bidName": title}]), attempt_no=i + 1)
    assert scalar(conn, "SELECT COUNT(*) FROM source_record_versions") == 3
    assert scalar(conn, "SELECT COUNT(*) FROM raw_revisions") == 3
    assert scalar(conn, "SELECT current_content_version FROM source_records") == 3


def test_content_version_counter_equals_actual_version_count(env) -> None:
    """Regression for the defect this suite caught on its first run.

    current_content_version was assigned the version row's primary key, so a record with
    two versions reported 4. The counter is a sequence, not an id.
    """
    writer, conn, _ = env
    commit(writer, page_of([{"notifyNo": "IB26779", "notifyVersion": "00", "bidName": "one"}]))
    commit(writer, page_of([{"notifyNo": "IB26779", "notifyVersion": "00", "bidName": "two"}]), attempt_no=2)
    row = conn.execute(
        "SELECT current_content_version, (SELECT COUNT(*) FROM source_record_versions v"
        " WHERE v.source_record_id=sr.id) AS actual FROM source_records sr"
    ).fetchone()
    assert row["current_content_version"] == row["actual"] == 2


def test_retry_creates_attempt_not_request(env) -> None:
    """B3 / DEDUP-13: two attempts, one request, both bodies kept as objects."""
    writer, conn, _ = env
    page = page_of([{"notifyNo": "IB26780", "notifyVersion": "00", "bidName": "t"}])
    commit(writer, page, attempt_no=1)
    commit(writer, page, attempt_no=2)
    assert scalar(conn, "SELECT COUNT(*) FROM fetch_requests") == 1
    assert scalar(conn, "SELECT COUNT(*) FROM fetch_attempts") == 2
    assert scalar(conn, "SELECT COUNT(*) FROM page_receipts") == 1, "one receipt per logical request"


# ------------------------------------------------------------------ DEDUP-15 / raw vs content axes

def test_page_of_ten_where_only_one_changed(env) -> None:
    """DEDUP-15 through the writer: only record #10 gains a revision; nine keep their hash."""
    writer, conn, _store = env

    def build(revise_last: bool) -> dict:
        return page_of(
            [
                {"notifyNo": f"IB2600{i}", "notifyVersion": "00", "bidName": "changed" if revise_last and i == 9 else f"t{i}"}
                for i in range(10)
            ]
        )

    commit(writer, build(False))
    before = {row["id"]: row["current_record_object_sha"] for row in conn.execute(
        "SELECT id, current_record_object_sha FROM source_records"
    )}
    commit(writer, build(True), attempt_no=2)

    assert scalar(conn, "SELECT COUNT(*) FROM source_records") == 10
    changed_id = int(scalar(conn, "SELECT id FROM source_records WHERE current_content_version=2"))
    rows = {row["id"]: dict(row) for row in conn.execute(
        "SELECT id, current_record_object_sha, current_content_version FROM source_records"
    )}
    for record_id, sha in before.items():
        if record_id == changed_id:
            continue
        assert rows[record_id]["current_record_object_sha"] == sha
        assert rows[record_id]["current_content_version"] == 1

    counts = {
        row["source_record_id"]: row["n"]
        for row in conn.execute("SELECT source_record_id, COUNT(*) AS n FROM raw_revisions GROUP BY source_record_id")
    }
    assert counts[changed_id] == 2
    assert all(counts[rid] == 1 for rid in before if rid != changed_id)


def test_raw_only_change_bumps_revision_not_version(env) -> None:
    """DEDUP-09: same normalized content, different raw bytes."""
    writer, conn, _ = env
    # notifyVersion is part of identity but not of the normalized fields, so changing the
    # raw payload shape while fields stay equal exercises the raw-only branch.
    commit(writer, page_of([{"notifyNo": "IB26781", "notifyVersion": "00", "bidName": "same"}]))
    record_id = int(scalar(conn, "SELECT id FROM source_records"))
    before_sha = scalar(conn, "SELECT current_record_object_sha FROM source_records")

    # A second page carrying an extra field the normalizer ignores.
    commit(
        writer,
        page_of([{"notifyNo": "IB26781", "notifyVersion": "00", "bidName": "same", "irrelevantField": "x"}]),
        attempt_no=2,
    )
    after_sha = scalar(conn, "SELECT current_record_object_sha FROM source_records")
    assert after_sha != before_sha
    assert scalar(conn, "SELECT COUNT(*) FROM source_record_versions WHERE source_record_id=?", record_id) == 1
    assert scalar(conn, "SELECT COUNT(*) FROM raw_revisions WHERE source_record_id=?", record_id) == 2
    assert scalar(conn, "SELECT current_content_version FROM source_records") == 1
    assert scalar(
        conn, "SELECT reason FROM raw_revisions WHERE source_record_id=? AND revision_no=2", record_id
    ) == "RAW_ONLY_CHANGE"


# ------------------------------------------------------------------ redaction policy (V-6)

def test_redaction_policy_replay_does_not_inflate_business_counters(env) -> None:
    """Fix #3 through the writer: policy change is REPLAY, not a business revision."""
    writer, conn, _store = env
    page = page_of([{"notifyNo": "IB26782", "notifyVersion": "00", "bidName": "t"}])
    commit(writer, page)
    record_id = int(scalar(conn, "SELECT id FROM source_records"))
    business_before = scalar(conn, "SELECT COUNT(*) FROM business_raw_revisions WHERE source_record_id=?", record_id)

    # Same source content, but we now redact differently -> new policy version.
    writer.store.redaction_policy_version = 2
    commit(writer, page, attempt_no=2)

    assert scalar(conn, "SELECT COUNT(*) FROM source_record_versions WHERE source_record_id=?", record_id) == 1
    assert scalar(conn, "SELECT current_content_version FROM source_records") == 1
    assert scalar(
        conn, "SELECT COUNT(*) FROM business_raw_revisions WHERE source_record_id=?", record_id
    ) == business_before, "policy replay created a business revision"
    assert scalar(
        conn, "SELECT COUNT(*) FROM policy_driven_revisions WHERE source_record_id=?", record_id
    ) == 1
    metrics = dict(conn.execute("SELECT * FROM metric_counts").fetchone())
    assert metrics["raw_revisions_count"] == business_before
    assert metrics["policy_driven_revisions_count"] == 1


# ------------------------------------------------------------------ identity / missing version

def test_missing_version_is_never_coerced_to_zero_zero(env) -> None:
    """The `or "00"` bug in the source adapter, asserted at the writer boundary."""
    writer, conn, _ = env
    commit(writer, page_of([{"notifyNo": "IB26783", "bidName": "no version"}]))
    row = conn.execute("SELECT version_state, version_raw FROM source_records").fetchone()
    assert row["version_state"] == "MISSING"
    assert row["version_raw"] is None

    # The same notifyNo published as version 00 is a DIFFERENT record, not an update.
    commit(writer, page_of([{"notifyNo": "IB26783", "notifyVersion": "00", "bidName": "no version"}]), attempt_no=2)
    assert scalar(conn, "SELECT COUNT(*) FROM source_records") == 2


def test_unparsable_version_is_recorded_not_dropped(env) -> None:
    writer, conn, _ = env
    commit(writer, page_of([{"notifyNo": "IB26784", "notifyVersion": "0O", "bidName": "letter O"}]))
    row = conn.execute("SELECT version_state, version_raw FROM source_records").fetchone()
    assert (row["version_state"], row["version_raw"]) == ("UNPARSABLE", "0O")


def test_record_contract_rejects_missing_with_raw_value() -> None:
    # Enforced at the value object, before any database is involved.
    """The coercion is refused before it reaches SQL, with a message naming the bug."""
    with pytest.raises(RecordContractError, match="MISSING"):
        NormalizedRecord(family="TBMT", natural_key="X", version_state="MISSING", version_raw="00", fields={})


# ------------------------------------------------------------------ extraction integrity (V-2)

def test_derivation_rows_come_from_real_extraction(env) -> None:
    writer, conn, _ = env
    page = page_of([{"notifyNo": f"IB2678{i}", "notifyVersion": "00", "bidName": "t"} for i in range(3)])
    commit(writer, page)
    rows = conn.execute(
        "SELECT d.record_object_sha, d.record_locator, r.sha256 AS response_sha"
        " FROM object_derivation d JOIN raw_objects r ON r.sha256=d.response_object_sha"
    ).fetchall()
    assert len(rows) == 3
    for row in rows:
        resolved = resolve_locator(page, row["record_locator"])
        import json

        from santhau_studio.domain.identity import record_object_sha

        assert row["record_object_sha"] == record_object_sha(json.loads(json.dumps(resolved, ensure_ascii=False)))


def test_fabricated_payload_is_refused_by_the_writer(env) -> None:
    """A caller cannot hand commit_page a payload that does not match the extractor's hash."""
    writer, conn, _ = env
    binding = binding_for("TBMT")
    page = page_of([{"notifyNo": "IB26790", "notifyVersion": "00", "bidName": "real"}])
    response_ref = writer.store_response(page)
    attempt = writer.ensure_attempt(
        run_id="run-1", family="TBMT", endpoint="smart/search",
        interval_from="2026-10-31T00:00:00Z", interval_to="2026-11-01T00:00:00Z",
        page_no=0, attempt_no=1, outcome="OK", response=response_ref,
    )
    report = extract_page(page, binding.spec)
    tampered = report.records[0]
    forged = NormalizedRecord(
        family=tampered.normalized.family, natural_key=tampered.normalized.natural_key,
        version_state=tampered.normalized.version_state, version_raw=tampered.normalized.version_raw,
        fields=tampered.normalized.fields,
    )
    object.__setattr__(tampered, "payload", {"notifyNo": "IB26790", "bidName": "DIFFERENT"})
    object.__setattr__(tampered, "normalized", forged)
    with pytest.raises(IntegrityMismatchError):
        writer.commit_page(run_id="run-1", attempt_id=attempt, response_ref=response_ref, records=[tampered])
    # Nothing half-written survived the refusal.
    assert scalar(conn, "SELECT COUNT(*) FROM source_records") == 0
    assert scalar(conn, "SELECT COUNT(*) FROM observations") == 0


def test_verify_extraction_catches_a_bad_locator() -> None:
    binding = binding_for("TBMT")
    page = page_of([{"notifyNo": "IB26791", "notifyVersion": "00", "bidName": "t"}])
    report = extract_page(page, binding.spec)
    with pytest.raises(LocatorError):
        resolve_locator(page, "/page/content/9")
    with pytest.raises(LocatorError):
        resolve_locator(page, "/page/nope/0")
    assert report.records  # the good extraction is unaffected


# ------------------------------------------------------------------ quarantine (O-4)

def test_malformed_response_is_quarantined_without_records(env) -> None:
    writer, conn, _store = env
    garbage = writer.store_response({"raw": "not an envelope"})
    attempt = writer.ensure_attempt(
        run_id="run-1", family="TBMT", endpoint="smart/search",
        interval_from="2026-10-31T00:00:00Z", interval_to="2026-11-01T00:00:00Z",
        page_no=0, attempt_no=1, outcome="MALFORMED", response=garbage,
    )
    writer.quarantine_response(
        run_id="run-1", attempt_id=attempt, response_ref=garbage, failure="NOT_JSON", detail="no page.content"
    )
    assert scalar(conn, "SELECT COUNT(*) FROM quarantined_responses") == 1
    assert scalar(conn, "SELECT COUNT(*) FROM observations") == 0
    assert scalar(conn, "SELECT COUNT(*) FROM source_records") == 0


def test_record_without_identity_lands_in_unresolved(env) -> None:
    """O-4 branch 2: extractable payload, no natural key -> RECORD object + UNRESOLVED."""
    writer, conn, _ = env
    commit(writer, page_of([{"notifyVersion": "00", "bidName": "no notifyNo at all"}]))
    assert scalar(conn, "SELECT COUNT(*) FROM source_records") == 0
    assert scalar(conn, "SELECT COUNT(*) FROM unresolved_records") == 1
    assert scalar(conn, "SELECT status FROM observations") == "UNRESOLVED"
    metrics = dict(conn.execute("SELECT * FROM metric_counts").fetchone())
    assert metrics["records_count"] == 0 and metrics["unresolved_count"] == 1


# ------------------------------------------------------------------ durability

def test_transaction_rolls_back_entirely_when_one_record_fails(env) -> None:
    """§5.2: a page is one transaction. Half a page is never visible."""
    writer, conn, _ = env
    binding = binding_for("TBMT")
    page = page_of([{"notifyNo": "IB26800", "notifyVersion": "00", "bidName": "ok"}])
    response_ref = writer.store_response(page)
    attempt = writer.ensure_attempt(
        run_id="run-1", family="TBMT", endpoint="smart/search",
        interval_from="2026-10-31T00:00:00Z", interval_to="2026-11-01T00:00:00Z",
        page_no=0, attempt_no=1, outcome="OK", response=response_ref,
    )
    records = extract_page(page, binding.spec).records
    # Second record duplicates the first identity but with different bytes: the UNIQUE on
    # observations fires mid-loop and the whole page must unwind.
    object.__setattr__(records[0], "locator", "/page/content/0")
    writer.commit_page(run_id="run-1", attempt_id=attempt, response_ref=response_ref, records=records)
    before = scalar(conn, "SELECT COUNT(*) FROM source_records")

    bad_attempt = writer.ensure_attempt(
        run_id="run-1", family="TBMT", endpoint="smart/search",
        interval_from="2026-10-31T00:00:00Z", interval_to="2026-11-01T00:00:00Z",
        page_no=1, attempt_no=1, outcome="OK", response=response_ref,
    )
    forged = extract_page(page, binding.spec).records
    object.__setattr__(forged[0], "record_sha", "0" * 64)  # hash the store will never agree to
    with pytest.raises(IntegrityMismatchError):
        writer.commit_page(run_id="run-1", attempt_id=bad_attempt, response_ref=response_ref, records=forged)
    assert scalar(conn, "SELECT COUNT(*) FROM source_records") == before
    assert scalar(conn, "SELECT COUNT(*) FROM page_receipts WHERE page_no=1") == 0


def test_recovery_is_clean_after_normal_writing(env) -> None:
    writer, conn, store = env
    commit(writer, page_of([{"notifyNo": f"IB2681{i}", "notifyVersion": "00", "bidName": "t"} for i in range(3)]))
    report = store.recover(conn)
    assert report.clean, report
    assert store.collect_orphan_files(conn, dry_run=True) == ()


def test_different_pages_of_one_window_are_different_requests(env) -> None:
    """Regression: the fingerprint used to truncate the JSON hex, dropping page_no.

    Page 0 and page 1 of the same interval produced the same request_fingerprint, so the
    second page collapsed onto the first request and its receipt overwrote it — silent
    loss of every page after the first.
    """
    writer, conn, _ = env
    page_a = page_of([{"notifyNo": "IB26820", "notifyVersion": "00", "bidName": "a"}])
    page_b = page_of([{"notifyNo": "IB26821", "notifyVersion": "00", "bidName": "b"}])
    commit(writer, page_a, page_no=0)
    commit(writer, page_b, page_no=1)

    assert scalar(conn, "SELECT COUNT(*) FROM fetch_requests") == 2
    assert scalar(conn, "SELECT COUNT(*) FROM page_receipts") == 2
    assert sorted(r["page_no"] for r in conn.execute("SELECT page_no FROM page_receipts")) == [0, 1]
    assert scalar(conn, "SELECT COUNT(*) FROM source_records") == 2


def test_wal_and_synchronous_pragmas_are_actually_set(tmp_path: Path) -> None:
    """§3.10 requires these explicitly; a default silently gives synchronous=NORMAL in WAL."""
    conn = connect(tmp_path / "wh.sqlite", create_schema=True)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # 2 == FULL
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()
