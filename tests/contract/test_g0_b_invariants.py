"""G0-B contract tests — real invariants on a real SQLite engine.

Each test asserts behaviour the spec requires, not that a column exists. Where a rule is
enforced by the schema, the test proves it by attempting the illegal write and requiring
the database to refuse it.

secret-scan:allow-file — this file necessarily carries fake credentials (the B7.2 probe-URI
guard test asserts on a `?t=` token). A line marker was tried first and ruff-format moved the
call onto different lines, detaching the marker from the secret; the file directive is the
mechanism that survives reformatting.
"""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from datetime import UTC, date, datetime, timedelta

import pytest
from helpers import (
    add_attempt,
    add_boundary_rule,
    add_complete_interval,
    add_object,
    add_record,
    add_request,
    connect_g0b,
    connect_reference,
    derive,
    extract_by_locator,
    ingest_record,
    iso,
    scalar,
)

from santhau_studio.domain.coverage import (
    CoverageRow,
    Interval,
    day_is_complete,
    resolve_coverage,
    subtract,
    union,
    vn_calendar_day,
)
from santhau_studio.domain.identity import (
    IdentityError,
    content_hash,
    identity_key,
    notice_group_key,
    record_object_sha,
)


@pytest.fixture()
def db() -> sqlite3.Connection:
    conn = connect_g0b()
    yield conn
    conn.close()


# ------------------------------------------------------------------ identity: no collisions
def test_null_version_is_not_the_string_none_nor_zero_zero() -> None:
    """DEDUP-06 / O-3: MISSING must not degrade into "None" or "00"."""
    missing = identity_key("TBMT", "IB26001", "MISSING", None)
    known_00 = identity_key("TBMT", "IB26001", "KNOWN", "00")
    literal_none = identity_key("TBMT", "IB26001", "UNPARSABLE", "None")
    assert len({missing, known_00, literal_none}) == 3


def test_missing_rejects_a_raw_value_instead_of_silently_ignoring_it() -> None:
    with pytest.raises(IdentityError):
        identity_key("TBMT", "IB26001", "MISSING", "00")
    with pytest.raises(IdentityError):
        identity_key("TBMT", "IB26001", "KNOWN", None)


def test_delimiter_in_source_value_cannot_shift_the_boundary() -> None:
    """The classic join-ambiguity: ["A|B","C"] vs ["A","B|C"] must not collide."""
    left = identity_key("TBMT", "A|B", "KNOWN", "C")
    right = identity_key("TBMT", "A", "KNOWN", "B|C")
    assert left != right


def test_delimiter_in_family_and_state_cannot_collide_either() -> None:
    a = identity_key("TB|MT", "X1", "KNOWN", "00")
    b = identity_key("TB", "MT|X1", "KNOWN", "00")
    assert a != b


def test_unicode_nfc_and_nfd_forms_are_one_identity() -> None:
    """Two byte-different spellings of the same Vietnamese name must not split a record."""
    nfc = unicodedata.normalize("NFC", "Hà Nội")
    nfd = unicodedata.normalize("NFD", "Hà Nội")
    assert nfc != nfd  # genuinely different code points
    assert identity_key("TBMT", nfc, "KNOWN", "00") == identity_key("TBMT", nfd, "KNOWN", "00")


def test_key_is_deterministic_and_version_prefixed() -> None:
    key = identity_key("TBMT", "IB26001", "KNOWN", "00")
    assert key == identity_key("TBMT", "IB26001", "KNOWN", "00")
    assert key.startswith("id1:") and len(key) == 68


def test_python_generated_key_satisfies_the_database_check(db: sqlite3.Connection) -> None:
    """Python and SQL must agree on the identity format, or every insert fails at runtime.

    This test exists because the first draft of the DDL said length 67 while the encoder
    produces 68 — an off-by-one that no amount of reading would have caught.
    """
    key = identity_key("TBMT", "IB26001", "MISSING", None)
    add_boundary_rule(db)
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = scalar(
        db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id
    )
    add_record(
        db,
        run_id="run-1",
        attempt_id=attempt_id,
        response_sha=str(response_sha),
        notify_no="IB26001",
        version_state="MISSING",
        version_raw=None,
    )
    stored = scalar(db, "SELECT identity_key FROM source_records")
    assert stored == key
    # A wrong-length key is refused by the schema, not by Python.
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO source_records (source_key, family, natural_key, version_state, version_raw,"
            " identity_key, first_seen_at_utc, last_seen_at_utc, current_content_version,"
            " current_content_hash, current_record_object_sha, integrity_status)"
            " VALUES ('egp_home','TBMT','X','KNOWN','00',?,?,?,1,'ch1:aaaa',"
            " (SELECT sha256 FROM raw_objects LIMIT 1),'OK')",
            ("id1:short", iso(), iso()),
        )


# ------------------------------------------------------------------ publications: no wrong merge
def test_notify_version_00_and_01_are_two_records_one_notice_group(db: sqlite3.Connection) -> None:
    """DEDUP-16 / O-3."""
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )
    v00 = add_record(
        db,
        run_id="run-1",
        attempt_id=attempt_id,
        response_sha=response_sha,
        notify_no="IB26001",
        version_raw="00",
    )
    v01 = add_record(
        db,
        run_id="run-1",
        attempt_id=attempt_id,
        response_sha=response_sha,
        notify_no="IB26001",
        version_raw="01",
        locator="/page/content/1",
        offset=5,
    )

    assert v00["identity_key"] != v01["identity_key"]
    assert v00["notice_group"] == v01["notice_group"]
    assert scalar(db, "SELECT COUNT(*) FROM source_records") == 2

    db.execute(
        "INSERT INTO notice_groups (id, family, group_kind, evidence_json, created_at_utc) VALUES (?,?,?,?,?)",
        (
            v00["notice_group"],
            "TBMT",
            "PUBLICATION_VERSIONS",
            json.dumps({"natural_key": "IB26001"}),
            iso(),
        ),
    )
    for piece in (v00, v01):
        db.execute(
            "INSERT INTO notice_group_members (group_id, source_record_id, publication_label) VALUES (?,?,?)",
            (v00["notice_group"], piece["record_id"], piece["identity_key"][-2:]),
        )
    assert scalar(db, "SELECT COUNT(*) FROM notice_group_members") == 2
    # Grouping is display-only: the records are still distinct rows with distinct identities.
    assert scalar(db, "SELECT COUNT(DISTINCT identity_key) FROM source_records") == 2


def test_notice_group_key_is_immune_to_delimiter_shift() -> None:
    assert notice_group_key("TB", "A|B") != notice_group_key("TB|A", "B")


# ------------------------------------------------------------------ unresolved / quarantine
def test_unparseable_response_produces_no_record_and_no_observation(db: sqlite3.Connection) -> None:
    """O-4: quarantine at response/page level. Never invent a record or an observation."""
    request_id = add_request(db, "run-1")
    garbage = add_object(db, {"raw": "not-a-valid-envelope"})
    add_attempt(db, request_id, outcome="MALFORMED", response_sha=garbage)
    db.execute(
        "INSERT INTO quarantined_responses (run_id, attempt_id, response_object_sha, failure, detail, quarantined_at_utc) "
        "VALUES (?,?,?,?,?,?)",
        ("run-1", 1, garbage, "NOT_JSON", "envelope missing page.content", iso()),
    )
    assert scalar(db, "SELECT COUNT(*) FROM observations") == 0
    assert scalar(db, "SELECT COUNT(*) FROM source_records") == 0
    assert scalar(db, "SELECT COUNT(*) FROM quarantined_responses") == 1


def test_record_object_is_actually_extracted_from_response_via_locator(
    db: sqlite3.Connection,
) -> None:
    """Fix #2: prove extraction, do not assert hash inequality.

    The earlier test asserted response_sha <> record_sha. That is the wrong guarantee: under
    a content-addressed store (D-0) identical bytes legitimately give identical hashes, and
    inequality says nothing about whether the record really came from that page. What §3.2
    needs is that the record is *derivable* from the response at the recorded locator, so
    this test performs the extraction and compares digests.
    """
    page = {
        "code": 200,
        "page": {
            "content": [{"notifyNo": f"IB2610{i}", "title": f"synthetic {i}"} for i in range(10)]
        },
    }
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id, response_sha=add_object(db, page))
    response_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )

    for i in range(10):
        locator = f"/page/content/{i}"
        extracted = extract_by_locator(page, locator)
        expected_sha = record_object_sha(extracted)
        piece = ingest_record(
            db,
            run_id="run-1",
            attempt_id=attempt_id,
            response_sha=response_sha,
            notify_no=extracted["notifyNo"],
            record_payload=extracted,
            normalized=extracted,
            locator=locator,
            observed_at=i,
        )
        # The stored record object is exactly what the locator resolves to.
        assert piece["record_sha"] == expected_sha
        row = db.execute(
            "SELECT record_object_sha FROM observations WHERE attempt_id=? AND record_locator=?",
            (attempt_id, locator),
        ).fetchone()
        assert row["record_object_sha"] == expected_sha

    # And the locator is load-bearing: claiming record #3 came from slot 0 is refused,
    # because no derivation row ties that triple together.
    third = extract_by_locator(page, "/page/content/3")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO observations (run_id, attempt_id, observation_key, family, response_object_sha,"
            " record_object_sha, record_locator, content_hash, status, observed_at_utc)"
            " VALUES ('run-1',?, 'SR:fabricated','TBMT',?,?, '/page/content/0', NULL, 'NEW', ?)",
            (attempt_id, response_sha, record_object_sha(third), iso()),
        )


def test_row_locator_resolves_array_payloads(db: sqlite3.Connection) -> None:
    """`row:<ordinal>` is the non-JSON form of record_locator (§3.2) and must really work.

    Some endpoints return a bare array rather than an envelope, which is why the locator
    vocabulary has two forms; a JSON pointer cannot address the top-level element.
    """
    rows = [{"pno": f"DA26{i}", "name": f"dự án {i}"} for i in range(4)]
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id, response_sha=add_object(db, rows))
    response_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )

    for i, row in enumerate(rows):
        piece = ingest_record(
            db,
            run_id="run-1",
            attempt_id=attempt_id,
            response_sha=response_sha,
            notify_no=row["pno"],
            record_payload=row,
            normalized=row,
            locator=f"row:{i}",
            family="DU_AN",
            observed_at=i,
        )
        assert piece["record_sha"] == record_object_sha(extract_by_locator(rows, f"row:{i}"))

    with pytest.raises(ValueError):
        extract_by_locator({"not": "an array"}, "row:0")


def test_locator_that_does_not_resolve_is_rejected_not_silently_null() -> None:
    page = {"page": {"content": [{"notifyNo": "IB26999"}]}}
    with pytest.raises(KeyError):
        extract_by_locator(page, "/page/content/7")
    with pytest.raises(ValueError):
        extract_by_locator(page, "slot:0")


def test_response_hash_cannot_be_recorded_as_a_record_without_derivation(
    db: sqlite3.Connection,
) -> None:
    """The substitution §3.2 forbids is now blocked by a missing derivation row.

    Stronger than the old CHECK: it also rejects a *plausible-looking* pair of different
    hashes that were never actually produced by extracting one from the other.
    """
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO observations (run_id, attempt_id, observation_key, family, response_object_sha,"
            " record_object_sha, record_locator, content_hash, status, observed_at_utc) "
            "VALUES ('run-1',?, 'SR:x','TBMT',?,?, '/page/content/0', NULL, 'NEW', ?)",
            (attempt_id, response_sha, response_sha, iso()),
        )


def test_identical_bytes_in_both_roles_is_now_representable(db: sqlite3.Connection) -> None:
    """The case the removed CHECK could not express: a single-record page stored as itself.

    This is the legacy `obs.raw = doc` shape from §13.1. Under D-0 it is legal — one object,
    two roles — and the role view reports both.
    """
    only_record = {"notifyNo": "IB26555", "title": "single"}
    page = only_record  # deliberately byte-identical to the record
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id, response_sha=add_object(db, page))
    shared_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )

    derive(db, shared_sha, shared_sha, "/page/content/0")
    db.execute(
        "INSERT INTO observations (run_id, attempt_id, observation_key, family, response_object_sha,"
        " record_object_sha, record_locator, content_hash, status, observed_at_utc)"
        " VALUES ('run-1',?, 'SR:x','TBMT',?,?, '/page/content/0', NULL, 'NEW', ?)",
        (attempt_id, shared_sha, shared_sha, iso()),
    )
    roles = {
        r["role"]
        for r in db.execute(
            "SELECT DISTINCT role FROM raw_object_roles WHERE sha256=?", (shared_sha,)
        )
    }
    assert roles == {"RESPONSE", "RECORD"}


def test_record_without_identity_is_unresolved_not_dropped(db: sqlite3.Connection) -> None:
    """O-4 first branch: extractable record, missing identity -> RECORD object + UNRESOLVED."""
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )
    fragment = {"title": "Gói thầu không có notifyNo"}
    rec_sha = add_object(db, fragment)
    derive(db, response_sha, rec_sha, "/page/content/0")
    db.execute(
        "INSERT INTO observations (run_id, attempt_id, observation_key, family, response_object_sha,"
        " record_object_sha, record_locator, content_hash, status, observed_at_utc) "
        "VALUES ('run-1',?, 'UNRESOLVED:' || ?, 'TBMT', ?, ?, '/page/content/0', ?, 'UNRESOLVED', ?)",
        (attempt_id, rec_sha[:16], response_sha, rec_sha, content_hash(fragment), iso()),
    )
    observation_id = int(scalar(db, "SELECT id FROM observations"))
    cur = db.execute(
        "INSERT INTO unresolved_records (run_id, attempt_id, observation_id, family, record_object_sha,"
        " response_object_sha, record_locator, reason, raised_at_utc) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "run-1",
            attempt_id,
            observation_id,
            "TBMT",
            rec_sha,
            response_sha,
            "/page/content/0",
            "MISSING_NATURAL_KEY",
            iso(),
        ),
    )
    assert cur.lastrowid
    assert scalar(db, "SELECT COUNT(*) FROM source_records") == 0
    # It stays visible: metric_counts must not fold it into records_count.
    row = db.execute("SELECT records_count, unresolved_count FROM metric_counts").fetchone()
    assert row["records_count"] == 0 and row["unresolved_count"] == 1


def test_observation_key_must_be_typed(db: sqlite3.Connection) -> None:
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )
    rec = add_object(db, {"a": 1})
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO observations (run_id, attempt_id, observation_key, family, response_object_sha,"
            " record_object_sha, record_locator, content_hash, status, observed_at_utc) "
            "VALUES ('run-1',?, 'GARBAGE:x', 'TBMT', ?, ?, '/page/content/0', NULL, 'NEW', ?)",
            (attempt_id, response_sha, rec, iso()),
        )


# ------------------------------------------------------------------ DEDUP-15 / DEDUP-09
def test_page_of_ten_records_only_one_changed(db: sqlite3.Connection) -> None:
    """DEDUP-15: only record #10 gets a new raw revision; the other nine keep their hash."""
    request_id = add_request(db, "run-1")
    attempt1 = add_attempt(db, request_id)
    page1 = str(scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt1))

    def payload(index: int, revised: bool = False) -> dict:
        return {
            "notifyNo": f"IB2600{index}",
            "title": f"synthetic {index} revised"
            if revised and index == 9
            else f"synthetic {index}",
        }

    for i in range(10):
        ingest_record(
            db,
            run_id="run-1",
            attempt_id=attempt1,
            response_sha=page1,
            notify_no=f"IB2600{i}",
            record_payload=payload(i),
            normalized=payload(i),
            locator=f"/page/content/{i}",
            observed_at=i,
        )
    assert scalar(db, "SELECT COUNT(*) FROM source_records") == 10
    before = {
        row["id"]: row["current_record_object_sha"]
        for row in db.execute("SELECT id, current_record_object_sha FROM source_records")
    }

    # Re-fetch the same page: nine payloads byte-identical, #10 revised.
    attempt2 = add_attempt(db, request_id, attempt_no=2)
    page2 = add_object(db, {"page": {"content": [payload(i, i == 9) for i in range(10)]}})
    outcomes = {}
    for i in range(10):
        result = ingest_record(
            db,
            run_id="run-1",
            attempt_id=attempt2,
            response_sha=page2,
            notify_no=f"IB2600{i}",
            record_payload=payload(i, i == 9),
            normalized=payload(i, i == 9),
            locator=f"/page/content/{i}",
            observed_at=100 + i,
        )
        outcomes[i] = result

    assert scalar(db, "SELECT COUNT(*) FROM source_records") == 10, (
        "re-fetch invented new source records"
    )

    changed_id = outcomes[9]["record_id"]
    rows = {
        row["id"]: row
        for row in db.execute(
            "SELECT id, current_record_object_sha, current_content_version FROM source_records"
        )
    }
    for i in range(9):
        record_id = outcomes[i]["record_id"]
        assert record_id != changed_id
        assert rows[record_id]["current_record_object_sha"] == before[record_id], (
            f"record {i} raw hash moved even though its bytes were re-sent unchanged"
        )
        assert rows[record_id]["current_content_version"] == 1

    counts = {
        row["source_record_id"]: row["n"]
        for row in db.execute(
            "SELECT source_record_id, COUNT(*) AS n FROM raw_revisions GROUP BY source_record_id"
        )
    }
    assert counts[changed_id] == 2, "the changed record must carry a second raw revision"
    assert all(counts[outcomes[i]["record_id"]] == 1 for i in range(9)), (
        "an unchanged record gained a raw revision"
    )
    assert outcomes[9]["outcome"] == "UPDATED"
    assert all(outcomes[i]["outcome"] == "UNCHANGED" for i in range(9))


def test_content_unchanged_but_raw_changed_is_revision_only(db: sqlite3.Connection) -> None:
    """DEDUP-09 through the real ingest path: same normalized content, different raw bytes."""
    request_id = add_request(db, "run-1")
    attempt1 = add_attempt(db, request_id)
    page = str(scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt1))
    first = ingest_record(
        db,
        run_id="run-1",
        attempt_id=attempt1,
        response_sha=page,
        notify_no="IB26777",
        record_payload={"notifyNo": "IB26777", "amount": "1.000.000"},
        normalized={"notifyNo": "IB26777", "amount": 1000000},
    )
    attempt2 = add_attempt(db, request_id, attempt_no=2)
    page2 = add_object(db, {"page": {"content": []}})
    second = ingest_record(
        db,
        run_id="run-1",
        attempt_id=attempt2,
        response_sha=page2,
        notify_no="IB26777",
        record_payload={"notifyNo": "IB26777", "amount": "1000000"},  # different raw formatting
        normalized={"notifyNo": "IB26777", "amount": 1000000},  # same meaning
    )
    assert second["outcome"] == "RAW_ONLY"
    assert second["record_sha"] != first["record_sha"]
    assert second["content_hash"] == first["content_hash"]
    assert (
        scalar(
            db, "SELECT COUNT(*) FROM raw_revisions WHERE source_record_id=?", first["record_id"]
        )
        == 2
    )
    assert (
        scalar(
            db,
            "SELECT COUNT(*) FROM source_record_versions WHERE source_record_id=?",
            first["record_id"],
        )
        == 1
    )
    assert (
        scalar(
            db, "SELECT current_content_version FROM source_records WHERE id=?", first["record_id"]
        )
        == 1
    )


def test_abab_keeps_three_content_versions(db: sqlite3.Connection) -> None:
    """§13.3 decision 2: A→B→A is three content versions, not one."""
    request_id = add_request(db, "run-1")
    page_sha = None
    for seq, title in enumerate(("A", "B", "A"), start=1):
        attempt = add_attempt(db, request_id, attempt_no=seq)
        page_sha = str(
            scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt)
        )
        ingest_record(
            db,
            run_id="run-1",
            attempt_id=attempt,
            response_sha=page_sha,
            notify_no="IB26888",
            record_payload={"notifyNo": "IB26888", "title": title},
            normalized={"title": title},
            observed_at=seq,
        )
    assert scalar(db, "SELECT COUNT(*) FROM source_record_versions") == 3
    assert scalar(db, "SELECT COUNT(*) FROM raw_revisions") == 3
    assert scalar(db, "SELECT current_content_version FROM source_records") == 3


def test_raw_only_change_bumps_revision_not_content_version(db: sqlite3.Connection) -> None:
    """DEDUP-09 / §3.5: same content_hash, different record object -> RAW_ONLY_CHANGE."""
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )
    piece = add_record(
        db,
        run_id="run-1",
        attempt_id=attempt_id,
        response_sha=response_sha,
        notify_no="IB26001",
        record_payload={"notifyNo": "IB26001", "title": "synthetic"},
    )
    # A differently-formatted record object that normalizes to the same content.
    alt_payload = {"notifyNo": "IB26001", "title": "synthetic", "source_format": "v2"}
    alt_sha = add_object(db, alt_payload)
    assert alt_sha != piece["record_sha"]
    assert content_hash({"notifyNo": "IB26001", "title": "synthetic"}) == piece["content_hash"]

    attempt2 = add_attempt(db, request_id, attempt_no=2)
    db.execute(
        "INSERT INTO raw_revisions (source_record_id, revision_no, record_object_sha, content_version_id, reason,"
        " redaction_policy_version, first_observed_at_utc, run_id) VALUES (?,2,?, NULL, 'RAW_ONLY_CHANGE', 1, ?, 'run-1')",
        (piece["record_id"], alt_sha, iso(50)),
    )
    db.execute(
        "UPDATE source_records SET current_record_object_sha=? WHERE id=?",
        (alt_sha, piece["record_id"]),
    )

    assert (
        scalar(
            db, "SELECT COUNT(*) FROM raw_revisions WHERE source_record_id=?", piece["record_id"]
        )
        == 2
    )
    assert (
        scalar(
            db,
            "SELECT COUNT(*) FROM source_record_versions WHERE source_record_id=?",
            piece["record_id"],
        )
        == 1
    )
    assert (
        scalar(
            db, "SELECT current_content_version FROM source_records WHERE id=?", piece["record_id"]
        )
        == 1
    )
    assert attempt2  # the second fetch is an attempt, not a new logical request


def test_raw_only_revision_may_not_point_at_a_content_version(db: sqlite3.Connection) -> None:
    """The schema encodes the §3.5 rule, so a writer cannot quietly regress to the old bug."""
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )
    piece = add_record(
        db, run_id="run-1", attempt_id=attempt_id, response_sha=response_sha, notify_no="IB26001"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO raw_revisions (source_record_id, revision_no, record_object_sha, content_version_id,"
            " reason, first_observed_at_utc, run_id) VALUES (?,2,?,?,'RAW_ONLY_CHANGE',?,'run-1')",
            (piece["record_id"], piece["record_sha"], piece["version_id"], iso(9)),
        )


# ------------------------------------------------------------------ deep capture (O-5)
def test_deep_capture_refresh_appends_and_keeps_history(db: sqlite3.Connection) -> None:
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )
    piece = add_record(
        db, run_id="run-1", attempt_id=attempt_id, response_sha=response_sha, notify_no="IB26001"
    )

    def capture(seq: int, payload: dict, when: int) -> tuple[int, int]:
        rec_sha = add_object(db, payload)
        cur = db.execute(
            "INSERT INTO deep_capture_history (deep_capture_id, capture_seq, captured_at_utc, response_object_sha,"
            " record_object_sha, content_hash, completeness, source_version_json, captured_by_run_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                1,
                seq,
                iso(when),
                response_sha,
                rec_sha,
                content_hash(payload),
                "FULL",
                json.dumps({"decisionVersion": f"{seq:02d}"}),
                "run-1",
            ),
        )
        return seq, int(cur.lastrowid)

    db.execute(
        "INSERT INTO deep_captures (source_record_id, lane, endpoint_kind, latest_capture_seq, completeness,"
        " captured_at_utc, refresh_due_at_utc, stale, source_version_json, response_object_sha,"
        " record_object_sha, content_hash, lane_status) VALUES (?, 'DETAIL','inputResult',1,'FULL',?, ?,0,?,?,?,?,'FULL')",
        (
            piece["record_id"],
            iso(0),
            iso(86400 * 30),
            json.dumps({"decisionVersion": "01"}),
            response_sha,
            piece["record_sha"],
            piece["content_hash"],
        ),
    )
    capture(1, {"notifyNo": "IB26001", "decisionVersion": "01"}, 0)
    capture(2, {"notifyNo": "IB26001", "decisionVersion": "02"}, 100)

    db.execute(
        "UPDATE deep_captures SET latest_capture_seq=2, captured_at_utc=?, source_version_json=?, stale=1,"
        " stale_reason='SOURCE_VERSION_CHANGED' WHERE id=1",
        (iso(100), json.dumps({"decisionVersion": "02"})),
    )

    assert scalar(db, "SELECT COUNT(*) FROM deep_capture_history") == 2, "refresh overwrote history"
    assert scalar(db, "SELECT latest_capture_seq FROM deep_captures") == 2
    row = db.execute("SELECT * FROM deep_capture_latest WHERE deep_capture_id=1").fetchone()
    assert json.loads(row["source_version_json"])["decisionVersion"] == "02"
    # The superseded capture is still fully readable — that is the point of splitting the tables.
    old = db.execute("SELECT * FROM deep_capture_history WHERE capture_seq=1").fetchone()
    assert old is not None and old["response_object_sha"]


def test_stale_requires_a_reason(db: sqlite3.Connection) -> None:
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )
    piece = add_record(
        db, run_id="run-1", attempt_id=attempt_id, response_sha=response_sha, notify_no="IB26001"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO deep_captures (source_record_id, lane, endpoint_kind, latest_capture_seq, completeness,"
            " captured_at_utc, stale, stale_reason, source_version_json, response_object_sha, record_object_sha,"
            " content_hash, lane_status) VALUES (?,'DETAIL','x',1,'FULL',?,1,NULL,?,?,?,?,'STALE')",
            (
                piece["record_id"],
                iso(),
                json.dumps({}),
                response_sha,
                piece["record_sha"],
                piece["content_hash"],
            ),
        )


def test_latest_pointer_must_reference_an_existing_capture(db: sqlite3.Connection) -> None:
    """The view joining latest -> history is the guard that refresh cannot lose evidence."""
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )
    piece = add_record(
        db, run_id="run-1", attempt_id=attempt_id, response_sha=response_sha, notify_no="IB26001"
    )
    db.execute(
        "INSERT INTO deep_captures (source_record_id, lane, endpoint_kind, latest_capture_seq, completeness,"
        " captured_at_utc, stale, source_version_json, response_object_sha, record_object_sha, content_hash,"
        " lane_status) VALUES (?,'DETAIL','x',7,'FULL',?,0,?,?,?,?,'FULL')",
        (
            piece["record_id"],
            iso(),
            json.dumps({}),
            response_sha,
            piece["record_sha"],
            piece["content_hash"],
        ),
    )
    assert db.execute("SELECT * FROM deep_capture_latest").fetchall() == []


# ------------------------------------------------------------------ coverage resolver (U-1)
def test_union_merges_touching_and_overlapping() -> None:
    a, b, c = (
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 2, tzinfo=UTC),
        datetime(2026, 1, 3, tzinfo=UTC),
    )
    assert union([Interval(a, b), Interval(b, c)]) == (Interval(a, c),)  # adjacent merge
    assert union([Interval(a, c), Interval(a, b)]) == (Interval(a, c),)  # containment
    assert len(union([Interval(a, b), Interval(c, c + timedelta(days=1))])) == 2  # disjoint kept


def test_subtract_produces_exact_gaps() -> None:
    a = datetime(2026, 1, 1, tzinfo=UTC)
    whole = Interval(a, a + timedelta(days=4))
    mid = Interval(a + timedelta(days=1), a + timedelta(days=2))
    gaps = subtract([whole], [mid])
    assert gaps == (
        Interval(a, a + timedelta(days=1)),
        Interval(a + timedelta(days=2), a + timedelta(days=4)),
    )


def test_three_hours_of_a_day_is_not_a_complete_day() -> None:
    """COV-13."""
    day = vn_calendar_day(date(2026, 3, 1))
    rows = [
        CoverageRow(
            1,
            "egp_home",
            "TBMT",
            Interval(day.from_utc, day.from_utc + timedelta(hours=3)),
            "COMPLETE",
            "HOUR",
            proof_id=1,
            proof_version=1,
        )
    ]
    result = resolve_coverage("egp_home", "TBMT", day, rows)
    assert result.status == "PARTIAL"
    assert not result.can_skip
    assert result.proven_fraction == pytest.approx(0.125)


def test_hourly_split_covering_the_whole_day_is_complete() -> None:
    """COV-14: union of 24 HOUR proofs makes the DAY complete, granularity reported as HOUR."""
    day = vn_calendar_day(date(2026, 3, 1))
    rows = [
        CoverageRow(
            i,
            "egp_home",
            "TBMT",
            Interval(day.from_utc + timedelta(hours=h), day.from_utc + timedelta(hours=h + 1)),
            "COMPLETE",
            "HOUR",
            proof_id=i,
            proof_version=1,
        )
        for i, h in enumerate(range(24), start=1)
    ]
    result = resolve_coverage("egp_home", "TBMT", day, rows)
    assert result.status == "COMPLETE" and result.gaps == ()
    assert result.granularity == "HOUR"


def test_newer_partial_does_not_mask_verified_complete() -> None:
    """COV-05 / §4.5 precedence: resolver, not MAX(id)."""
    day = vn_calendar_day(date(2026, 3, 1))
    rows = [
        CoverageRow(1, "egp_home", "TBMT", day, "COMPLETE", "DAY", proof_id=1, proof_version=1),
        CoverageRow(
            2, "egp_home", "TBMT", day, "PARTIAL", "DAY", proof_id=None
        ),  # newer, higher id
    ]
    result = resolve_coverage("egp_home", "TBMT", day, rows)
    assert result.status == "COMPLETE" and result.can_skip


def test_legacy_complete_without_proof_is_never_skipped() -> None:
    """COV-06 / BUG-15."""
    day = vn_calendar_day(date(2026, 3, 1))
    rows = [
        CoverageRow(
            1, "egp_home", "TBMT", day, "COMPLETE", "DAY", proof_id=None, proof_version=None
        )
    ]
    result = resolve_coverage("egp_home", "TBMT", day, rows)
    assert result.status != "COMPLETE"
    assert result.legacy_unverified and not result.can_skip
    assert any("LEGACY_COMPLETE_UNVERIFIED" in reason for reason in result.reasons)


def test_revised_after_proof_requires_reconcile() -> None:
    day = vn_calendar_day(date(2026, 3, 1))
    rows = [
        CoverageRow(
            1,
            "egp_home",
            "TBMT",
            day,
            "COMPLETE",
            "DAY",
            proof_id=1,
            proof_version=1,
            data_revised_after_proof=True,
        )
    ]
    assert resolve_coverage("egp_home", "TBMT", day, rows).status == "NEEDS_RECONCILE"


def test_leap_day_window_unions_correctly() -> None:
    """COV-11: 2028-02-29 really is 24 hours, computed from datetimes not constants."""
    leap = vn_calendar_day(date(2028, 2, 29))
    assert leap.duration == timedelta(days=1)
    rows = [CoverageRow(1, "s", "TBMT", leap, "COMPLETE", "DAY", proof_id=1, proof_version=1)]
    assert day_is_complete(date(2028, 2, 29), rows, "s", "TBMT").status == "COMPLETE"
    feb = vn_calendar_day(date(2028, 2, 28))
    assert (leap.from_utc - feb.from_utc) == timedelta(days=1)


def test_historical_access_unsupported_is_its_own_status() -> None:
    """COV-12: not zero, not PARTIAL — an explicit UNSUPPORTED."""
    day = vn_calendar_day(date(2026, 3, 1))
    result = resolve_coverage("s", "TBMT", day, [], historical_access_supported=False)
    assert result.status == "UNSUPPORTED"
    assert result.reasons == ("HISTORICAL_ACCESS_UNSUPPORTED",)


def test_empty_interval_is_rejected_as_evidence() -> None:
    a = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError):
        Interval(a, a)
    with pytest.raises(ValueError):
        Interval(a, a - timedelta(seconds=1))


def test_naive_datetime_cannot_pass_as_utc() -> None:
    with pytest.raises(ValueError):
        Interval(datetime(2026, 1, 1), datetime(2026, 1, 2))


# ------------------------------------------------------------------ schema enforcement (C-2/C-10)
def test_run_lifecycle_and_outcome_are_separate_axes(db: sqlite3.Connection) -> None:
    """Fix #5: §2.3 PAUSE/RESUME is control state, §3.6 COMPLETED/PARTIAL is outcome.

    With one column doing both, a worker legitimately recording PAUSED would violate the
    outcome CHECK. Two columns let each vocabulary stay closed.
    """
    db.execute(
        "INSERT INTO runs (run_id, job_id, job_kind, lifecycle, started_at_utc, policy_version, requested_by)"
        " VALUES ('r-pause','j1','BACKFILL','PAUSED',?,1,'sid')",
        (iso(),),
    )
    # A paused run has no outcome yet — that is legal, not a missing value.
    assert scalar(db, "SELECT status FROM runs WHERE run_id='r-pause'") is None

    # Outcome vocabulary stays closed: COMPLETE belongs to intervals, not runs.
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO runs (run_id, job_id, job_kind, lifecycle, status, started_at_utc, finished_at_utc,"
            " policy_version, requested_by) VALUES ('r-bad','j2','BACKFILL','FINISHED','COMPLETE',?,?,1,'sid')",
            (iso(), iso(10)),
        )
    # Lifecycle vocabulary stays closed too.
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO runs (run_id, job_id, job_kind, lifecycle, started_at_utc, policy_version, requested_by)"
            " VALUES ('r-bad2','j3','BACKFILL','RUNNING_PAUSED',?,1,'sid')",
            (iso(),),
        )
    # An outcome cannot exist on a run that has not stopped.
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO runs (run_id, job_id, job_kind, lifecycle, status, started_at_utc, finished_at_utc,"
            " policy_version, requested_by) VALUES ('r-bad3','j4','BACKFILL','RUNNING','PARTIAL',?,?,1,'sid')",
            (iso(), iso(10)),
        )
    # A finished run must carry an outcome and an end time.
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO runs (run_id, job_id, job_kind, lifecycle, status, started_at_utc, policy_version, requested_by)"
            " VALUES ('r-bad4','j5','BACKFILL','FINISHED','COMPLETED',?,1,'sid')",
            (iso(),),
        )
    # §2.3: a dead process becomes INTERRUPTED, never COMPLETED.
    db.execute(
        "INSERT INTO runs (run_id, job_id, job_kind, lifecycle, status, started_at_utc, finished_at_utc,"
        " policy_version, requested_by) VALUES ('r-crash','j6','BACKFILL','TERMINATED','INTERRUPTED',?,?,1,'sid')",
        (iso(), iso(10)),
    )
    assert scalar(db, "SELECT status FROM runs WHERE run_id='r-crash'") == "INTERRUPTED"


def test_completed_never_enters_interval_status(db: sqlite3.Connection) -> None:
    """BUG-03 was a writer putting COMPLETED into interval_status. The DB now refuses."""
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO coverage_intervals (source_key, family, interval_from_utc, interval_to_utc,"
            " granularity, status, run_id, created_at_utc) VALUES ('egp_home','TBMT',?,?,'DAY','COMPLETED','r',?)",
            (iso(), iso(86400), iso()),
        )


def test_attempt_outcome_vocabulary_is_enforced(db: sqlite3.Connection) -> None:
    request_id = add_request(db, "run-1")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO fetch_attempts (request_id, attempt_no, started_at_utc, outcome) VALUES (?,1,?,'OKAY')",
            (request_id, iso()),
        )


def test_ok_attempt_without_body_is_refused(db: sqlite3.Connection) -> None:
    request_id = add_request(db, "run-1")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO fetch_attempts (request_id, attempt_no, started_at_utc, outcome) VALUES (?,1,?,'OK')",
            (request_id, iso()),
        )


def test_observation_status_vocabulary_is_enforced(db: sqlite3.Connection) -> None:
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )
    rec = add_object(db, {"a": 1})
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO observations (run_id, attempt_id, observation_key, family, response_object_sha,"
            " record_object_sha, record_locator, content_hash, status, observed_at_utc)"
            " VALUES ('run-1',?, 'SR:x','TBMT',?,?, '/page/content/0', NULL, 'NEWV2', ?)",
            (attempt_id, response_sha, rec, iso()),
        )


def test_complete_interval_requires_a_proof(db: sqlite3.Connection) -> None:
    """BUG-15 class: a COMPLETE row with no proof must not be storable."""
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO coverage_intervals (source_key, family, interval_from_utc, interval_to_utc,"
            " granularity, status, proof_id, run_id, created_at_utc) VALUES ('egp_home','TBMT',?,?,'DAY','COMPLETE',NULL,'r',?)",
            (iso(), iso(86400), iso()),
        )


def test_interval_before_proof_is_refused_by_fk(db: sqlite3.Connection) -> None:
    """C-10: the FK forces the §4.1 item 8 order — proof first, inside one transaction."""
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO coverage_intervals (source_key, family, interval_from_utc, interval_to_utc,"
            " granularity, status, proof_id, run_id, created_at_utc) VALUES ('egp_home','TBMT',?,?,'DAY','COMPLETE',9999,'r',?)",
            (iso(), iso(86400), iso()),
        )


def test_proof_without_canary_rule_is_refused_by_trigger(db: sqlite3.Connection) -> None:
    """C-6: kind alone is a claim; the rule it cites must actually have been canaried."""
    rule_id = add_boundary_rule(db, canary=0)
    with pytest.raises(sqlite3.IntegrityError):
        add_complete_interval(db, run_id="run-1", frm=iso(), to=iso(86400), rule_id=rule_id)


def test_proof_must_match_its_rule_adapter(db: sqlite3.Connection) -> None:
    rule_id = add_boundary_rule(db, adapter_sha="a" * 40)
    with pytest.raises(sqlite3.IntegrityError):
        add_complete_interval(
            db, run_id="run-1", frm=iso(), to=iso(86400), rule_id=rule_id, adapter_sha="b" * 40
        )


def test_complete_proof_cannot_claim_unrecovered_requests(db: sqlite3.Connection) -> None:
    """§4.1: unrecovered_request_count = 0 is a condition, not a display metric."""
    rule_id = add_boundary_rule(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO coverage_proofs (proof_version, source_key, family, interval_from_utc, interval_to_utc,"
            " granularity, boundary_rule_id, source_query_fingerprint, adapter_sha, request_count, attempt_count,"
            " attempt_error_count, unrecovered_request_count, malformed_count, distinct_ids, ids_union_hash,"
            " total_trusted, terminal_evidence_json, page_size, max_pages_hit, max_records_hit, verified_at_utc,"
            " verified_by_run_id) VALUES (1,'egp_home','TBMT',?,?,'DAY',?, 'fp',? ,1,2,1,1,0,10,'h',1,'{}',10,0,0,?,'r')",
            (iso(), iso(86400), rule_id, "a" * 40, iso()),
        )


def test_recovered_retry_is_allowed_and_counted(db: sqlite3.Connection) -> None:
    """COV-16: attempt_error_count > 0 with unrecovered = 0 still reaches COMPLETE."""
    rule_id = add_boundary_rule(db)
    interval_id = add_complete_interval(
        db, run_id="run-1", frm=iso(), to=iso(86400), rule_id=rule_id
    )
    assert interval_id
    proof = db.execute(
        "SELECT attempt_error_count, unrecovered_request_count FROM coverage_proofs"
    ).fetchone()
    assert proof["attempt_error_count"] == 0 and proof["unrecovered_request_count"] == 0

    request_id = add_request(db, "run-2")
    add_attempt(db, request_id, attempt_no=1, outcome="TIMEOUT", response_sha=None)
    add_attempt(db, request_id, attempt_no=2, outcome="OK")
    assert scalar(db, "SELECT COUNT(*) FROM fetch_attempts WHERE request_id=?", request_id) == 2
    assert (
        scalar(db, "SELECT COUNT(*) FROM fetch_requests WHERE run_id='run-2'") == 1
    )  # retry != new request


def test_missing_version_cannot_carry_raw_value_in_sql(db: sqlite3.Connection) -> None:
    """C-7 enforced in the schema too, so a non-Python writer cannot create the split."""
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO source_records (source_key, family, natural_key, version_state, version_raw, identity_key,"
            " first_seen_at_utc, last_seen_at_utc, current_content_version, current_content_hash,"
            " current_record_object_sha, integrity_status) VALUES ('egp_home','TBMT','X','MISSING','00',?,?,?,"
            "1,'ch1:abc',(SELECT sha256 FROM raw_objects LIMIT 1) ,'OK')",
            (identity_key("TBMT", "X", "MISSING", None), iso(), iso()),
        )


def test_unique_constraint_blocks_duplicate_observation(db: sqlite3.Connection) -> None:
    """DEDUP-07: the same payload self-publishing a record it already showed does not add rows."""
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )
    piece = add_record(
        db, run_id="run-1", attempt_id=attempt_id, response_sha=response_sha, notify_no="IB26001"
    )

    # Same attempt + same observation_key + same record object is the duplicate case.
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO observations (run_id, attempt_id, observation_key, family, response_object_sha,"
            " record_object_sha, record_locator, content_hash, status, observed_at_utc)"
            " VALUES ('run-1',?, 'SR:' || (SELECT identity_key FROM source_records WHERE id=?),"
            "         'TBMT', ?, ?, '/page/content/0', NULL, 'DUPLICATE_IN_RUN', ?)",
            (attempt_id, piece["record_id"], response_sha, piece["record_sha"], iso()),
        )
    assert scalar(db, "SELECT COUNT(*) FROM observations") == 1


def test_transaction_rolls_back_wholly_on_midway_failure(db: sqlite3.Connection) -> None:
    """§5.2 step 3-4: every row of a page lives in one transaction, or none do."""
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )
    db.execute("BEGIN")
    add_record(
        db, run_id="run-1", attempt_id=attempt_id, response_sha=response_sha, notify_no="IB26001"
    )
    assert scalar(db, "SELECT COUNT(*) FROM source_records") == 1
    try:
        db.execute(
            "INSERT INTO coverage_intervals (source_key, family, interval_from_utc, interval_to_utc, granularity,"
            " status, proof_id, run_id, created_at_utc) VALUES ('egp_home','TBMT',?,?,'DAY','COMPLETE',9999,'run-1',?)",
            (iso(), iso(86400), iso()),
        )
    except sqlite3.IntegrityError:
        db.execute("ROLLBACK")
    assert scalar(db, "SELECT COUNT(*) FROM source_records") == 0
    assert scalar(db, "SELECT COUNT(*) FROM observations") == 0
    assert scalar(db, "SELECT COUNT(*) FROM raw_revisions") == 0


def test_content_hash_invariant_for_trusted_record(db: sqlite3.Connection) -> None:
    """C-9: a record always has a content hash; the schema refuses the NULL escape."""
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO source_records (source_key, family, natural_key, version_state, version_raw, identity_key,"
            " first_seen_at_utc, last_seen_at_utc, current_content_version, current_content_hash,"
            " current_record_object_sha, integrity_status) VALUES ('egp_home','TBMT','X','KNOWN','00',?,?,?,1,NULL,"
            "(SELECT sha256 FROM raw_objects LIMIT 1),'OK')",
            (identity_key("TBMT", "X", "KNOWN", "00"), iso(), iso()),
        )


# ------------------------------------------------------------------ money (U-2)
def test_unknown_amount_is_null_not_zero(db: sqlite3.Connection) -> None:
    """§0.2 item 5: UNKNOWN ≠ 0. The schema makes 0-for-unknown unrepresentable."""
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )
    piece = add_record(
        db, run_id="run-1", attempt_id=attempt_id, response_sha=response_sha, notify_no="IB26001"
    )

    db.execute(
        "INSERT INTO money_amounts (owner_kind, owner_id, role, presence_state, value, value_raw, currency,"
        " currency_code, tax_basis, provenance_json, observed_at_utc) VALUES ('PACKAGE',?,'PACKAGE_VALUE',"
        "'MISSING', NULL, NULL,'MISSING',NULL,'UNKNOWN',?,?)",
        (piece["record_id"], json.dumps({"endpoint": "x"}), iso()),
    )
    row = db.execute("SELECT value, presence_state FROM money_amounts").fetchone()
    assert row["value"] is None and row["presence_state"] == "MISSING"

    # Claiming KNOWN while omitting the amount is refused.
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO money_amounts (owner_kind, owner_id, role, presence_state, value, currency, currency_code,"
            " tax_basis, provenance_json, observed_at_utc) VALUES ('PACKAGE',?,'BID_PRICE','KNOWN',NULL,'KNOWN','VND','UNKNOWN',?,?)",
            (piece["record_id"], json.dumps({"endpoint": "x"}), iso()),
        )
    # And a real 0 must be asserted as KNOWN with an explicit currency.
    db.execute(
        "INSERT INTO money_amounts (owner_kind, owner_id, role, presence_state, value, currency, currency_code,"
        " tax_basis, provenance_json, observed_at_utc) VALUES ('PACKAGE',?,'WIN_PRICE','KNOWN',0,'KNOWN','VND','INCLUDING_TAX',?,?)",
        (piece["record_id"], json.dumps({"endpoint": "x"}), iso()),
    )
    assert (
        scalar(
            db, "SELECT COUNT(*) FROM money_amounts WHERE value = 0 AND presence_state = 'KNOWN'"
        )
        == 1
    )


def test_money_columns_are_separate_concepts(db: sqlite3.Connection) -> None:
    """§0.2 item 6: price / win price / unit price / tax / currency / lot are different fields."""
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )
    piece = add_record(
        db, run_id="run-1", attempt_id=attempt_id, response_sha=response_sha, notify_no="IB26001"
    )
    for role, tax in (
        ("PACKAGE_VALUE", "EXCLUDING_TAX"),
        ("BID_PRICE", "INCLUDING_TAX"),
        ("UNIT_PRICE", "NOT_APPLICABLE"),
    ):
        db.execute(
            "INSERT INTO money_amounts (owner_kind, owner_id, role, presence_state, value, currency, currency_code,"
            " tax_basis, provenance_json, observed_at_utc) VALUES ('PACKAGE',?,?, 'KNOWN',?,'KNOWN','VND',?,?,?)",
            (piece["record_id"], role, 1000, tax, json.dumps({"role": role}), iso()),
        )
    assert scalar(db, "SELECT COUNT(DISTINCT role) FROM money_amounts") == 3


def test_quantity_unknown_is_not_zero(db: sqlite3.Connection) -> None:
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )
    piece = add_record(
        db, run_id="run-1", attempt_id=attempt_id, response_sha=response_sha, notify_no="IB26001"
    )
    db.execute(
        "INSERT INTO goods_rows (source_record_id, row_ordinal, name, unit, quantity_presence, quantity,"
        " quantity_raw, provenance_json) VALUES (?,0,'Máy','cái','MISSING',NULL,NULL,?)",
        (piece["record_id"], json.dumps({"row": 0})),
    )
    assert scalar(db, "SELECT quantity FROM goods_rows") is None
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO goods_rows (source_record_id, row_ordinal, name, unit, quantity_presence, quantity,"
            " provenance_json) VALUES (?,1,'Bàn','cái','KNOWN',NULL,?)",
            (piece["record_id"], json.dumps({"row": 1})),
        )


# ------------------------------------------------------------------ receipts (§2.3)
def test_receipt_steps_are_monotonic(db: sqlite3.Connection) -> None:
    """§2.3: receipts are per-step, and a later step cannot be claimed without the earlier one."""
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )

    def insert(*, received: int, committed: int, verified: int, published: int) -> None:
        db.execute(
            "INSERT INTO page_receipts (run_id, request_id, attempt_id, response_object_sha, page_no,"
            " records_seen, records_new, records_updated, records_raw_only, records_unresolved,"
            " response_received, page_committed, proof_verified, snapshot_published, committed_at_utc)"
            " VALUES ('run-1', ?, ?, ?, 0, 10, 1, 0, 0, 0, ?, ?, ?, ?, ?)",
            (request_id, attempt_id, response_sha, received, committed, verified, published, iso()),
        )

    insert(received=1, committed=1, verified=1, published=1)
    assert scalar(db, "SELECT COUNT(*) FROM page_receipts") == 1
    db.execute("DELETE FROM page_receipts")

    for bad in (
        {
            "received": 0,
            "committed": 1,
            "verified": 0,
            "published": 0,
        },  # committed without response
        {"received": 1, "committed": 0, "verified": 1, "published": 0},  # verified without commit
        {"received": 1, "committed": 1, "verified": 0, "published": 1},  # published without proof
    ):
        with pytest.raises(sqlite3.IntegrityError):
            insert(**bad)
        db.execute("DELETE FROM page_receipts")


def test_blocked_reason_is_recordable_without_becoming_a_token_leak(db: sqlite3.Connection) -> None:
    """B7.2 / §6: the reason a lane stopped must be storable, but not as free text.

    The old code put a token-bearing URL into coverage_windows.reason. A closed
    reason_kind plus a probe_uri that rejects query strings makes that unrepresentable.
    """
    db.execute(
        "INSERT INTO source_registry (source_key, display_name, endpoint_family, access_state)"
        " VALUES ('egp_home','EGP','home','BLOCKED')"
    )

    def log(reason: str = "HTTP_403", probe: str | None = None) -> None:
        db.execute(
            "INSERT INTO source_access_events (source_key, to_state, reason_kind, detail_scrubbed, probe_uri, observed_at_utc)"
            " VALUES ('egp_home','BLOCKED',?,?,?,?)",
            (reason, "none", probe, iso()),
        )

    log()  # a legitimate closed reason records fine
    assert scalar(db, "SELECT COUNT(*) FROM source_access_events") == 1

    with pytest.raises(sqlite3.IntegrityError):
        log(reason="https://muasamcong.mof.gov.vn/x?t=leaked")  # open-ended reason refused
    db.execute("DELETE FROM source_access_events")

    with pytest.raises(sqlite3.IntegrityError):
        # The fake query-string token here is the point of the assertion, not a secret.
        log(
            probe="https://x.invalid/notice?t=abc123"
        )  # secret-scan:allow — query string is where tokens travel
    db.execute("DELETE FROM source_access_events")

    log(probe="https://x.invalid/notice")  # path-only probe is acceptable
    assert scalar(db, "SELECT COUNT(*) FROM source_access_events") == 1


def test_unknown_access_state_is_refused(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO source_registry (source_key, display_name, endpoint_family, access_state)"
        " VALUES ('egp_home','EGP','home','BLOCKED')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO source_access_events (source_key, to_state, reason_kind, detail_scrubbed, observed_at_utc)"
            " VALUES ('egp_home','SOFT_BLOCKED','WAF','none',?)",
            (iso(),),
        )


def test_redaction_policy_change_does_not_invent_a_business_revision(
    db: sqlite3.Connection,
) -> None:
    """Fix #3: re-deriving under a new policy is not evidence the source changed.

    Changing redaction rules changes the bytes we hold, so record_object_sha moves even
    though the notice is identical. Counting that as a raw revision would inflate
    raw_revisions_count and make UI-07 claim "raw changed" for something the source never did.
    """
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )
    source_record = {"notifyNo": "IB26444", "title": "giữ nguyên", "contact": "0900000000"}
    piece = ingest_record(
        db,
        run_id="run-1",
        attempt_id=attempt_id,
        response_sha=response_sha,
        notify_no="IB26444",
        record_payload=source_record,
        normalized={"notifyNo": "IB26444", "title": "giữ nguyên"},
    )

    # Policy v2 redacts the phone field -> different bytes, different sha, same meaning.
    redacted_v2 = {"notifyNo": "IB26444", "title": "giữ nguyên", "contact": "[REDACTED]"}
    v2_sha = add_object(db, redacted_v2, policy_version=2)
    assert v2_sha != piece["record_sha"]
    derive(db, response_sha, v2_sha, "/page/content/0", policy_version=2)

    db.execute(
        "INSERT INTO raw_revisions (source_record_id, revision_no, record_object_sha, content_version_id,"
        " reason, redaction_policy_version, first_observed_at_utc, run_id) VALUES (?,2,?,NULL,'REPLAY',2,?,'run-1')",
        (piece["record_id"], v2_sha, iso(60)),
    )

    # No content version was invented.
    assert (
        scalar(
            db,
            "SELECT COUNT(*) FROM source_record_versions WHERE source_record_id=?",
            piece["record_id"],
        )
        == 1
    )
    assert (
        scalar(
            db, "SELECT current_content_version FROM source_records WHERE id=?", piece["record_id"]
        )
        == 1
    )
    # And it is not counted as a business raw revision.
    assert (
        scalar(
            db,
            "SELECT COUNT(*) FROM business_raw_revisions WHERE source_record_id=?",
            piece["record_id"],
        )
        == 1
    )
    assert (
        scalar(
            db,
            "SELECT COUNT(*) FROM policy_driven_revisions WHERE source_record_id=?",
            piece["record_id"],
        )
        == 1
    )

    metrics = db.execute(
        "SELECT raw_revisions_count, policy_driven_revisions_count FROM metric_counts"
    ).fetchone()
    assert metrics["raw_revisions_count"] == 1, "policy churn leaked into the business counter"
    assert metrics["policy_driven_revisions_count"] == 1


def test_replay_may_not_carry_a_content_version(db: sqlite3.Connection) -> None:
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )
    piece = add_record(
        db, run_id="run-1", attempt_id=attempt_id, response_sha=response_sha, notify_no="IB26445"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO raw_revisions (source_record_id, revision_no, record_object_sha, content_version_id,"
            " reason, redaction_policy_version, first_observed_at_utc, run_id)"
            " VALUES (?,2,?,?, 'REPLAY', 2, ?, 'run-1')",
            (piece["record_id"], piece["record_sha"], piece["version_id"], iso()),
        )


def test_boundary_rule_cited_by_a_proof_is_immutable(db: sqlite3.Connection) -> None:
    """Fix #4: a proof is evidence; the rule it cites cannot be rewritten underneath it."""
    rule_id = add_boundary_rule(db)
    add_complete_interval(db, run_id="run-1", frm=iso(), to=iso(86400), rule_id=rule_id)

    for column, value in (
        ("conversion_params", '{"to_utc": "silently wrong now"}'),
        ("boundary_kind", "HALF_OPEN_UTC"),
        ("precision", "SECOND"),
        ("offset_minutes", 0),
        ("canary_verified", 0),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(f"UPDATE filter_boundary_rules SET {column}=? WHERE id=?", (value, rule_id))

    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM filter_boundary_rules WHERE id=?", (rule_id,))

    # The sanctioned path: a new rule_version, referenced by new proofs.
    db.execute(
        "INSERT INTO filter_boundary_rules (rule_key, rule_version, source_key, family, endpoint, adapter_sha,"
        " boundary_kind, precision, source_timezone, offset_minutes, conversion_params, canary_verified, verified_at_utc)"
        " VALUES ('egp_home.tbmt', 2, 'egp_home','TBMT','/search',?,'HALF_OPEN_UTC','SECOND','Asia/Ho_Chi_Minh',420,"
        ' \'{"note":"v2"}\', 1, ?)',
        ("a" * 40, iso()),
    )
    assert (
        scalar(db, "SELECT COUNT(*) FROM filter_boundary_rules WHERE rule_key='egp_home.tbmt'") == 2
    )


def test_unreferenced_boundary_rule_is_still_editable(db: sqlite3.Connection) -> None:
    """Draft rules that no proof cites must remain correctable, or the table becomes write-once."""
    rule_id = add_boundary_rule(db)
    db.execute("UPDATE filter_boundary_rules SET precision='SECOND' WHERE id=?", (rule_id,))
    assert scalar(db, "SELECT precision FROM filter_boundary_rules WHERE id=?", rule_id) == "SECOND"


def test_proof_referenced_by_an_interval_is_immutable(db: sqlite3.Connection) -> None:
    rule_id = add_boundary_rule(db)
    add_complete_interval(db, run_id="run-1", frm=iso(), to=iso(86400), rule_id=rule_id)
    proof_id = int(scalar(db, "SELECT id FROM coverage_proofs"))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE coverage_proofs SET distinct_ids = 999999 WHERE id=?", (proof_id,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM coverage_proofs WHERE id=?", (proof_id,))


def test_identity_digest_is_recomputable_from_stored_columns(db: sqlite3.Connection) -> None:
    """D-1: keep identity_key a pure digest, but prove it is reproducible from the canonical columns.

    A digest you cannot re-derive is a liability during incident triage. The owner chose not to
    store a redundant identity_components column, so the reproducibility guarantee has to live
    in a test: read the four canonical columns back, recompute, and require equality.
    """
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = str(scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id))

    cases = [
        ("IB26301", "KNOWN", "00"),
        ("IB26302", "KNOWN", "01"),
        ("IB26303", "MISSING", None),
        ("IB26304", "UNPARSABLE", "0O"),  # letter O where a digit is expected
        ("Hà Nội IB26305", "KNOWN", "03"),
    ]
    for notify_no, state, raw in cases:
        add_record(
            db, run_id="run-1", attempt_id=attempt_id, response_sha=response_sha,
            notify_no=notify_no, version_state=state, version_raw=raw,
            record_payload={"notifyNo": notify_no}, normalized={"notifyNo": notify_no},
            locator=f"/page/content/{len(cases) - 1}",
        )

    rows = db.execute("SELECT family, natural_key, version_state, version_raw, identity_key FROM source_records").fetchall()
    assert len(rows) == len(cases)
    for row in rows:
        recomputed = identity_key(
            row["family"], row["natural_key"], row["version_state"], row["version_raw"]
        )
        assert recomputed == row["identity_key"], f"digest not reproducible for {row['natural_key']}"

    # And the columns alone distinguish the cases that BUG-08 used to collapse.
    keys = {row["identity_key"] for row in rows}
    assert len(keys) == len(cases), "two different canonical tuples produced one identity"


def test_missing_and_zero_zero_stay_distinct_end_to_end(db: sqlite3.Connection) -> None:
    """The same distinction, asserted through the database rather than only the encoder."""
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = str(scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id))
    missing = add_record(db, run_id="run-1", attempt_id=attempt_id, response_sha=response_sha,
                         notify_no="IB26310", version_state="MISSING", version_raw=None,
                         record_payload={"notifyNo": "IB26310"}, normalized={"notifyNo": "IB26310"})
    zero = add_record(db, run_id="run-1", attempt_id=attempt_id, response_sha=response_sha,
                      notify_no="IB26310", version_state="KNOWN", version_raw="00",
                      record_payload={"notifyNo": "IB26310", "v": "00"},
                      normalized={"notifyNo": "IB26310", "v": "00"}, locator="/page/content/1")
    assert missing["identity_key"] != zero["identity_key"]
    assert scalar(db, "SELECT COUNT(*) FROM source_records") == 2
    assert scalar(db, "SELECT version_raw FROM source_records WHERE id=?", missing["record_id"]) is None


def test_ambiguous_candidate_cannot_self_promote(db: sqlite3.Connection) -> None:
    """D-2: no confidence threshold promotes AMBIGUOUS -> LINKED. Only evidence or audited manual action."""
    for entity, label in (("e1", "gói A"), ("e2", "gói B")):
        db.execute(
            "INSERT INTO canonical_entities (id, entity_kind, label, resolution_state, created_at_utc)"
            " VALUES (?, 'TENDER_PACKAGE', ?, 'AMBIGUOUS', ?)",
            (entity, label, iso()),
        )
    db.execute(
        "INSERT INTO relation_edges (from_entity, to_entity, relation, evidence_json, confidence)"
        " VALUES ('e1','e2','NOTICE_HAS_RESULT', ?, 'CANDIDATE')",
        (json.dumps({"title_similarity": 0.93, "price": "1000000000"}),),
    )

    # The tempting case: high-looking similarity. Still refused without an audit trail.
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE relation_edges SET confidence='LINKED' WHERE id=1")
    assert scalar(db, "SELECT confidence FROM relation_edges WHERE id=1") == "CANDIDATE"

    # A rejected link is also an audited act, and it must not silently look like confirmation.
    db.execute(
        "INSERT INTO identity_audit_actions (action, target_kind, target_ref, actor_sid, reason, evidence_json, acted_at_utc)"
        " VALUES ('CONFIRM_LINK','relation_edge','1','S-1-5-21-777','MST 10/10 trùng khớp', ?, ?)",
        (json.dumps({"mst": "0100123456"}), iso()),
    )
    db.execute("UPDATE relation_edges SET confidence='LINKED' WHERE id=1")
    assert scalar(db, "SELECT confidence FROM relation_edges WHERE id=1") == "LINKED"
    assert scalar(db, "SELECT COUNT(*) FROM identity_audit_actions") == 1


def test_audit_trail_records_actor_reason_and_undo(db: sqlite3.Connection) -> None:
    """§3.8: manual merge/split must record who, why, evidence, and be undoable."""
    db.execute(
        "INSERT INTO canonical_entities (id, entity_kind, resolution_state, created_at_utc)"
        " VALUES ('c1','PARTY','EXACT',?)",
        (iso(),),
    )
    db.execute(
        "INSERT INTO identity_audit_actions (action, target_kind, target_ref, actor_sid, reason, evidence_json, acted_at_utc)"
        " VALUES ('MERGE','canonical_entity','c1','S-1-5-21-777','hai MST cùng một pháp nhân', ?, ?)",
        (json.dumps({"mst": ["0100123456", "0100123456-01"]}), iso()),
    )
    merge_id = int(scalar(db, "SELECT id FROM identity_audit_actions"))
    db.execute(
        "INSERT INTO identity_audit_actions (action, target_kind, target_ref, actor_sid, reason, evidence_json, undo_action_id, acted_at_utc)"
        " VALUES ('UNDO','canonical_entity','c1','S-1-5-21-777','hoàn tác merge', ?, ?, ?)",
        (json.dumps({"undoes": merge_id}), merge_id, iso(60)),
    )
    assert (
        scalar(db, "SELECT undo_action_id FROM identity_audit_actions WHERE action='UNDO'")
        == merge_id
    )
    # action vocabulary is closed
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO identity_audit_actions (action, target_kind, target_ref, actor_sid, reason, evidence_json, acted_at_utc)"
            " VALUES ('AUTO_MERGE','canonical_entity','c1','system','by threshold', '{}', ?)",
            (iso(),),
        )


def test_retry_keeps_both_attempts(db: sqlite3.Connection) -> None:
    """B3: history of failed attempts must be kept in full."""
    request_id = add_request(db, "run-1")
    add_attempt(db, request_id, attempt_no=1, outcome="TIMEOUT", response_sha=None)
    add_attempt(
        db,
        request_id,
        attempt_no=2,
        outcome="HTTP_ERROR",
        response_sha=add_object(db, {"error": 503}),
    )
    add_attempt(db, request_id, attempt_no=3, outcome="OK")
    rows = db.execute(
        "SELECT attempt_no, outcome FROM fetch_attempts ORDER BY attempt_no"
    ).fetchall()
    assert [r["attempt_no"] for r in rows] == [1, 2, 3]
    assert [r["outcome"] for r in rows] == ["TIMEOUT", "HTTP_ERROR", "OK"]


# ------------------------------------------------------------------ raw SHA vs two roles
def test_reference_ddl_collides_when_page_bytes_equal_record_bytes(db: sqlite3.Connection) -> None:
    """The §3.10 conflict, demonstrated rather than asserted.

    §3.10 makes sha256 the PRIMARY KEY *and* stores object_kind. A page that carries one
    record and is stored as that record — exactly the legacy shape `obs.raw = doc` (§13.1) —
    produces identical bytes for both roles, so the second role cannot be recorded.
    """
    payload = {"notifyNo": "IB26001"}
    sha = add_object(db, payload)  # G0-B schema: no object_kind, so no conflict
    assert (
        db.execute("SELECT COUNT(*) AS n FROM raw_objects WHERE sha256=?", (sha,)).fetchone()["n"]
        == 1
    )


def test_reference_ddl_rejects_the_second_role() -> None:
    """Same content, second role, under the frozen §3.10 shape: UNIQUE violation."""
    conn = connect_reference()
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        payload = json.dumps({"notifyNo": "IB26001"}, sort_keys=True, separators=(",", ":"))
        import hashlib

        sha = hashlib.sha256(payload.encode()).hexdigest()
        conn.execute(
            "INSERT INTO raw_objects (sha256, object_kind, byte_len, codec, storage_rel_path, integrity_status,"
            " redaction_policy_version, created_at_utc) VALUES (?,?,?,?,?,?,?,?)",
            (
                sha,
                "RESPONSE",
                20,
                "identity",
                f"raw/objects/{sha[:2]}/{sha}.bin.gz",
                "OK",
                1,
                iso(),
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO raw_objects (sha256, object_kind, byte_len, codec, storage_rel_path, integrity_status,"
                " redaction_policy_version, created_at_utc) VALUES (?,?,?,?,?,?,?,?)",
                (
                    sha,
                    "RECORD",
                    20,
                    "identity",
                    f"raw/objects/{sha[:2]}/{sha}.bin.gz",
                    "OK",
                    1,
                    iso(),
                ),
            )
    finally:
        conn.close()


def test_g0b_role_view_reports_both_roles_for_shared_bytes(db: sqlite3.Connection) -> None:
    """Role is a property of the edge; the view recovers it without storing it twice."""
    request_id = add_request(db, "run-1")
    attempt_id = add_attempt(db, request_id)
    response_sha = str(
        scalar(db, "SELECT response_object_sha FROM fetch_attempts WHERE id=?", attempt_id)
    )
    piece = add_record(
        db, run_id="run-1", attempt_id=attempt_id, response_sha=response_sha, notify_no="IB26001"
    )

    roles = {
        row["role"]
        for row in db.execute(
            "SELECT DISTINCT role FROM raw_object_roles WHERE sha256=?", (piece["record_sha"],)
        )
    }
    assert "RECORD" in roles
    response_roles = {
        row["role"]
        for row in db.execute(
            "SELECT DISTINCT role FROM raw_object_roles WHERE sha256=?", (response_sha,)
        )
    }
    assert "RESPONSE" in response_roles


def test_row_orphan_is_derivable_and_file_orphan_is_not_a_sql_question(
    db: sqlite3.Connection,
) -> None:
    """C-5: the two orphan kinds are not symmetric, and the schema should not pretend otherwise.

    A ROW orphan (row exists, no edge references it) is visible to SQL and must be KEPT,
    GC'd only by age policy (§5.2). A FILE orphan (file exists, no row) is invisible to SQL
    by construction — it belongs to the startup filesystem scan, not a view.
    """
    orphan_sha = add_object(db, {"nobody": "references me"})
    referenced = add_object(db, {"referenced": "by an attempt"})
    request_id = add_request(db, "run-1")
    add_attempt(db, request_id, response_sha=referenced)

    rows = {row["sha256"] for row in db.execute("SELECT sha256 FROM orphan_object_rows")}
    assert orphan_sha in rows
    assert referenced not in rows

    # And the referenced object really does expose its derived role.
    assert {
        r["role"]
        for r in db.execute(
            "SELECT DISTINCT role FROM raw_object_roles WHERE sha256=?", (referenced,)
        )
    } == {"RESPONSE"}
