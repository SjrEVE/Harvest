"""G0-B — contract tests for the §3.10 DDL and the B1-B7 decisions.

These tests pin the *contract*, not an implementation: no production schema or engine
exists yet, and the spec forbids writing one before G0-B closes. What they guarantee
is that the frozen decisions stay expressed in the reference DDL, and that the five
open conflicts are recorded rather than quietly ignored.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from ddl_contract_check import check, columns, strip_sql_comments

DDL_PATH = Path(__file__).resolve().parents[2] / "docs" / "ddl" / "v1_3_reference.sql"
SQL = DDL_PATH.read_text(encoding="utf-8").replace("\r\n", "\n")
RESULTS = {item.check_id: item for item in check(SQL)}


def test_reference_ddl_is_extracted_not_retyped() -> None:
    """The checked SQL must be a verbatim slice of §3.10, so findings apply to the spec."""
    assert len(SQL) > 6000
    assert SQL.count("CREATE TABLE") == 15
    for table in (
        "fetch_requests",
        "raw_objects",
        "observations",
        "coverage_proofs",
        "notice_group_members",
    ):
        assert f"CREATE TABLE {table}" in SQL, table


@pytest.mark.parametrize(
    "check_id",
    ["B1.1", "B1.2", "B1.3", "B2.1", "B3.1", "B3.2", "B4.1", "B4.2", "B5.1", "B5.2", "B7.1"],
)
def test_frozen_contracts_are_expressed_in_the_ddl(check_id: str) -> None:
    item = RESULTS[check_id]
    assert item.verdict == "PASS", f"{check_id} — {item.detail}"


def test_b1_two_identifiers_are_distinct_columns() -> None:
    """§3.2: a page hash and a record hash are different identifiers and must stay separate."""
    obs = columns(SQL, "observations")
    assert "response_object_sha" in obs and "record_object_sha" in obs
    assert obs["response_object_sha"] != obs["record_object_sha"]
    assert "REFERENCES raw_objects" in obs["response_object_sha"]
    assert "REFERENCES raw_objects" in obs["record_object_sha"]


def test_b1_locator_is_required_so_extraction_is_provable() -> None:
    obs = columns(SQL, "observations")
    assert "NOT NULL" in obs["record_locator"]


def test_raw_objects_carries_redaction_version_and_kind() -> None:
    """§5.1: raw is the post-redaction payload, so the policy version must be recorded."""
    raw = columns(SQL, "raw_objects")
    assert "redaction_policy_version" in raw
    assert "NOT NULL" in raw["redaction_policy_version"]
    assert "wire_sha256" in raw  # hash of pre-redaction bytes; bytes themselves not stored


def test_vocabulary_columns_lacking_check_constraints_are_the_known_gap() -> None:
    """BUG-03 recurred because one vocabulary leaked into another column's field.

    §3.6 enumerates five status vocabularies but the DDL only CHECKs version_state and
    object_kind. Until CHECKs are added, no schema-level guard stops a writer from
    storing COMPLETED in interval_status. Pinned so the gap cannot be forgotten.
    """
    unguarded = [
        table
        for table, column in (
            ("coverage_intervals", "status"),
            ("fetch_attempts", "outcome"),
            ("observations", "status"),
        )
        if "CHECK" not in columns(SQL, table).get(column, "").upper()
    ]
    assert set(unguarded) == {"coverage_intervals", "fetch_attempts", "observations"}
    assert RESULTS["VOCAB.coverage_intervals"].verdict == "FAIL"
    assert RESULTS["VOCAB.source_records"].verdict == "PASS"


def test_notify_version_double_encoding_is_recorded_as_conflict() -> None:
    """§3.7 puts notifyVersion inside natural_key; §3.4 puts it in identity_key again."""
    assert RESULTS["KEY.1"].verdict == "FAIL"
    records = columns(SQL, "source_records")
    assert "natural_key" in records and "identity_key" in records and "version_raw" in records


def test_unresolved_observation_conflict_is_recorded() -> None:
    """observation_key admits UNRESOLVED:<hash> while record_object_sha is NOT NULL."""
    assert RESULTS["UNRES.1"].verdict == "FAIL"
    obs = columns(SQL, "observations")
    assert "NOT NULL" in obs["record_object_sha"]


def test_identity_key_encoding_gaps_are_recorded() -> None:
    assert RESULTS["IDK.1"].verdict == "GAP"
    assert RESULTS["IDK.2"].verdict == "GAP"


def test_deep_capture_refresh_has_no_schema_surface_yet() -> None:
    """B6 requires captured_at + refresh_due_at; §3.10 defines no table carrying them."""
    assert RESULTS["B6.1"].verdict == "GAP"
    assert "refresh_due_at" not in SQL
    assert "captured_at_utc" not in SQL


def test_filter_boundary_conversion_rule_not_persisted() -> None:
    """§4.2 demands the adapter declare its UTC->source conversion rule; DDL has no column."""
    assert RESULTS["B5.3"].verdict == "GAP"
    proofs = columns(SQL, "coverage_proofs")
    assert "filter_boundary_kind" in proofs
    assert not any("boundary_rule" in name or "conversion" in name for name in proofs)


def test_no_column_stores_credential_material() -> None:
    """§6/B7: reason/report/manifest must never hold a token."""
    forbidden = ("token", "secret", "cookie", "authorization", "password")
    for table in (
        "fetch_requests",
        "fetch_attempts",
        "observations",
        "coverage_proofs",
        "source_records",
    ):
        for name in columns(SQL, table):
            assert not any(word in name.lower() for word in forbidden), f"{table}.{name}"


def test_comment_stripping_does_not_alter_column_names() -> None:
    """Vietnamese comments contain commas; stripping them is what makes parsing correct.

    Without it, a comment like "-- vận hành, KHÔNG chặn COMPLETE" reads as a separator
    and the following column definition disappears — which would have reported a
    missing column as a spec gap.
    """
    assert "unrecovered_request_count" in columns(SQL, "coverage_proofs")
    assert "object_kind" in columns(SQL, "raw_objects")
    assert "--" not in strip_sql_comments("x INT, -- a, b")


def test_coverage_proof_and_interval_share_granularity() -> None:
    """§4.3: a day is COMPLETE only when unioned intervals cover it at a proven granularity."""
    assert "granularity" in columns(SQL, "coverage_intervals")
    assert "granularity" in columns(SQL, "coverage_proofs")


def test_raw_revision_can_exist_without_a_content_version() -> None:
    """§3.5 / DEDUP-09: RAW_ONLY_CHANGE adds a revision and no content version."""
    revisions = columns(SQL, "raw_revisions")
    assert "REFERENCES source_record_versions" in revisions["content_version_id"]
    # Nullable by omission: no NOT NULL, which is what lets a raw-only change have no version.
    assert "NOT NULL" not in revisions["content_version_id"]
