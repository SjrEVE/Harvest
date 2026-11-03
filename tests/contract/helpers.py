"""Test-only scaffolding that writes valid rows into the G0-B schema.

This is deliberately NOT the product writer. It exists so contract tests can state an
invariant ("only record #10 gets a new raw revision") without each test re-deriving the
fifteen columns needed to make a row legal. G1 replaces it with the real ingest path.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from santhau_studio.domain.identity import (
    content_hash,
    identity_key,
    notice_group_key,
)

UTC = UTC
DDL_PATH = Path(__file__).resolve().parents[2] / "docs" / "ddl" / "g0_b_proposed.sql"
REFERENCE_DDL_PATH = Path(__file__).resolve().parents[2] / "docs" / "ddl" / "v1_3_reference.sql"

BASE = datetime(2026, 10, 31, 3, 0, 0, tzinfo=UTC)


def iso(offset_seconds: int = 0) -> str:
    return (BASE + timedelta(seconds=offset_seconds)).isoformat().replace("+00:00", "Z")


def connect_g0b() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL_PATH.read_text(encoding="utf-8").replace("\r\n", "\n"))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def connect_reference() -> sqlite3.Connection:
    """A database built from the frozen §3.10 DDL, for comparison tests."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(REFERENCE_DDL_PATH.read_text(encoding="utf-8").replace("\r\n", "\n"))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def add_object(
    conn: sqlite3.Connection, payload: object, *, codec: str = "identity", policy_version: int = 1
) -> str:
    """Store a raw object and return its sha256. Idempotent by content.

    policy_version exists because the same source record re-redacted under a newer policy
    produces different bytes, and the schema must be able to tell that apart from the
    source actually changing (fix #3).
    """
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    sha = hashlib.sha256(raw).hexdigest()
    conn.execute(
        "INSERT OR IGNORE INTO raw_objects "
        "(sha256, byte_len, codec, storage_rel_path, integrity_status, redaction_policy_version, created_at_utc) "
        "VALUES (?,?,?,?,?,?,?)",
        (sha, len(raw), codec, f"raw/objects/{sha[:2]}/{sha}.bin.gz", "OK", policy_version, iso()),
    )
    return sha


def derive(
    conn: sqlite3.Connection,
    response_sha: str,
    record_sha: str,
    locator: str,
    *,
    policy_version: int = 1,
) -> None:
    """Record the evidence that a record object was extracted from a page at a locator.

    The observation trigger requires this row, so no test can accidentally assert against a
    locator that was never actually used to extract anything.
    """
    conn.execute(
        "INSERT OR IGNORE INTO object_derivation "
        "(response_object_sha, record_object_sha, record_locator, redaction_policy_version, derived_at_utc) "
        "VALUES (?,?,?,?,?)",
        (response_sha, record_sha, locator, policy_version, iso()),
    )


def add_boundary_rule(
    conn: sqlite3.Connection, *, canary: int = 1, adapter_sha: str = "a" * 40
) -> int:
    cur = conn.execute(
        "INSERT INTO filter_boundary_rules "
        "(rule_key, rule_version, source_key, family, endpoint, adapter_sha, boundary_kind, precision, "
        " source_timezone, offset_minutes, conversion_params, canary_verified, verified_at_utc) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "egp_home.tbmt",
            1,
            "egp_home",
            "TBMT",
            "/o/egp-portal-home/services/smart/search",
            adapter_sha,
            "CLOSED_LOCAL",
            "DAY",
            "Asia/Ho_Chi_Minh",
            420,
            json.dumps({"to_utc": "to_utc - 1ms -> closed_local_day"}),
            canary,
            iso() if canary else None,
        ),
    )
    return int(cur.lastrowid)


def add_request(
    conn: sqlite3.Connection, run_id: str, *, page_no: int = 0, family: str = "TBMT"
) -> int:
    fingerprint = hashlib.sha256(f"{run_id}:{page_no}:{family}".encode()).hexdigest()
    cur = conn.execute(
        "INSERT INTO fetch_requests "
        "(run_id, source_key, family, endpoint, interval_from_utc, interval_to_utc, page_no, request_fingerprint, created_at_utc) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            run_id,
            "egp_home",
            family,
            "smart/search",
            iso(0),
            iso(86400),
            page_no,
            fingerprint,
            iso(),
        ),
    )
    return int(cur.lastrowid)


def add_attempt(
    conn: sqlite3.Connection,
    request_id: int,
    *,
    attempt_no: int = 1,
    outcome: str = "OK",
    response_sha: str | None = None,
) -> int:
    if response_sha is None and outcome == "OK":
        response_sha = add_object(conn, {"page": {"content": []}})
    cur = conn.execute(
        "INSERT INTO fetch_attempts "
        "(request_id, attempt_no, started_at_utc, finished_at_utc, outcome, http_status, response_object_sha, byte_len, error_kind) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            request_id,
            attempt_no,
            iso(),
            iso(1),
            outcome,
            200 if outcome == "OK" else 504,
            response_sha,
            100,
            None,
        ),
    )
    return int(cur.lastrowid)


def add_record(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    attempt_id: int,
    response_sha: str,
    notify_no: str,
    version_state: str = "KNOWN",
    version_raw: str | None = "00",
    record_payload: dict | None = None,
    normalized: dict | None = None,
    locator: str = "/page/content/0",
    family: str = "TBMT",
    offset: int = 0,
) -> dict:
    """Insert the full chain for one source record and return the pieces the test needs.

    `normalized` is the caller's normalized view of the record. Deriving it here from
    record_payload would silently disagree with whatever the ingest path normalizes to,
    which is exactly the distinction content_hash is supposed to capture.
    """
    payload = record_payload or {"notifyNo": notify_no, "title": "synthetic"}
    rec_sha = add_object(conn, payload)
    effective = normalized if normalized is not None else payload
    chash = content_hash(effective)
    ikey = identity_key(family, notify_no, version_state, version_raw)

    derive(conn, response_sha, rec_sha, locator)
    conn.execute(
        "INSERT INTO observations "
        "(run_id, attempt_id, observation_key, family, response_object_sha, record_object_sha, record_locator, content_hash, status, observed_at_utc) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            run_id,
            attempt_id,
            f"SR:{ikey}",
            family,
            response_sha,
            rec_sha,
            locator,
            chash,
            "NEW",
            iso(offset),
        ),
    )
    cur = conn.execute(
        "INSERT INTO source_records "
        "(source_key, family, natural_key, version_state, version_raw, identity_key, first_seen_at_utc, last_seen_at_utc, "
        " current_content_version, current_content_hash, current_record_object_sha, integrity_status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "egp_home",
            family,
            notify_no,
            version_state,
            version_raw,
            ikey,
            iso(offset),
            iso(offset),
            1,
            chash,
            rec_sha,
            "OK",
        ),
    )
    record_id = int(cur.lastrowid)
    cur = conn.execute(
        "INSERT INTO source_record_versions "
        "(source_record_id, content_version_seq, content_hash, normalized_json, introducing_record_sha, introducing_response_sha, fetched_at_utc, change_kind, normalize_schema) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (record_id, 1, chash, json.dumps(effective), rec_sha, response_sha, iso(offset), "NEW", 1),
    )
    version_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO raw_revisions "
        "(source_record_id, revision_no, record_object_sha, content_version_id, reason, redaction_policy_version, "
        "first_observed_at_utc, run_id) VALUES (?,?,?,?,?,?,?,?)",
        (record_id, 1, rec_sha, version_id, "NEW_RECORD", 1, iso(offset), run_id),
    )
    return {
        "record_id": record_id,
        "version_id": version_id,
        "identity_key": ikey,
        "record_sha": rec_sha,
        "content_hash": chash,
        "notice_group": notice_group_key(family, notify_no),
    }


def add_complete_interval(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    frm: str,
    to: str,
    rule_id: int,
    adapter_sha: str = "a" * 40,
    status: str = "COMPLETE",
    granularity: str = "DAY",
    proof_version: int = 1,
) -> int:
    """Insert proof then interval, in the order §4.1 item 8 and the FK require."""
    cur = conn.execute(
        "INSERT INTO coverage_proofs "
        "(proof_version, source_key, family, interval_from_utc, interval_to_utc, granularity, boundary_rule_id, "
        " source_query_fingerprint, adapter_sha, request_count, attempt_count, attempt_error_count, "
        " unrecovered_request_count, malformed_count, distinct_ids, ids_union_hash, total_trusted, "
        " terminal_evidence_json, page_size, max_pages_hit, max_records_hit, verified_at_utc, verified_by_run_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            proof_version,
            "egp_home",
            "TBMT",
            frm,
            to,
            granularity,
            rule_id,
            "fp",
            adapter_sha,
            1,
            1,
            0,
            0,
            0,
            10,
            "h" * 64,
            1,
            json.dumps({"last_page_len": 10}),
            10,
            0,
            0,
            iso(),
            run_id,
        ),
    )
    proof_id = int(cur.lastrowid)
    if status == "COMPLETE" and proof_version == 0:
        cur = conn.execute(
            "INSERT INTO coverage_intervals "
            "(source_key, family, interval_from_utc, interval_to_utc, granularity, status, proof_id, run_id, created_at_utc) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("egp_home", "TBMT", frm, to, granularity, status, None, run_id, iso()),
        )
        return int(cur.lastrowid)
    cur = conn.execute(
        "INSERT INTO coverage_intervals "
        "(source_key, family, interval_from_utc, interval_to_utc, granularity, status, proof_id, run_id, created_at_utc) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("egp_home", "TBMT", frm, to, granularity, status, proof_id, run_id, iso()),
    )
    return int(cur.lastrowid)


def ingest_record(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    attempt_id: int,
    response_sha: str,
    notify_no: str,
    record_payload: dict,
    normalized: dict,
    locator: str = "/page/content/0",
    family: str = "TBMT",
    version_state: str = "KNOWN",
    version_raw: str | None = "00",
    observed_at: int = 0,
) -> dict:
    """Apply the §3.5 decision table: content version and raw revision are separate axes.

    This is deliberately a faithful transcription of the table, not a shortcut. If the
    table cannot be implemented against the schema, that is a G0-B finding worth failing on.
    """
    rec_sha = add_object(conn, record_payload)
    chash = content_hash(normalized)
    ikey = identity_key(family, notify_no, version_state, version_raw)

    existing = conn.execute(
        "SELECT id, current_content_hash, current_record_object_sha, current_content_version "
        "FROM source_records WHERE source_key='egp_home' AND family=? AND identity_key=?",
        (family, ikey),
    ).fetchone()

    db_obs_status = "UNCHANGED"
    if existing is None:
        created = add_record(
            conn,
            run_id=run_id,
            attempt_id=attempt_id,
            response_sha=response_sha,
            notify_no=notify_no,
            version_state=version_state,
            version_raw=version_raw,
            record_payload=record_payload,
            normalized=normalized,
            locator=locator,
            family=family,
            offset=observed_at,
        )
        conn.execute(
            "UPDATE observations SET status='NEW' WHERE attempt_id=? AND record_object_sha=?",
            (attempt_id, rec_sha),
        )
        return {**created, "outcome": "NEW"}

    record_id = int(existing["id"])
    content_changed = existing["current_content_hash"] != chash
    raw_changed = existing["current_record_object_sha"] != rec_sha

    if not content_changed and not raw_changed:
        outcome, db_obs_status = "UNCHANGED", "UNCHANGED"
    elif content_changed:
        outcome, db_obs_status = "UPDATED", "UPDATED"
    elif raw_changed:
        outcome, db_obs_status = "RAW_ONLY", "UPDATED_RAW"
    else:  # pragma: no cover - unreachable, kept so the branch table is explicit
        raise AssertionError("decision table gap")

    version_id: int | None = None
    if content_changed:
        next_seq = int(existing["current_content_version"]) + 1
        cur = conn.execute(
            "INSERT INTO source_record_versions (source_record_id, content_version_seq, content_hash,"
            " normalized_json, introducing_record_sha, introducing_response_sha, fetched_at_utc, change_kind, normalize_schema)"
            " VALUES (?,?,?,?,?,?,?,?,1)",
            (record_id, next_seq, chash, json.dumps(normalized), rec_sha, response_sha, iso(observed_at), "UPDATED"),
        )
        version_id = int(cur.lastrowid)

    if content_changed or raw_changed:
        next_rev = (
            int(
                scalar(
                    conn, "SELECT COUNT(*) FROM raw_revisions WHERE source_record_id=?", record_id
                )
            )
            + 1
        )
        reason = "SEMANTIC_CHANGE" if content_changed else "RAW_ONLY_CHANGE"
        conn.execute(
            "INSERT INTO raw_revisions (source_record_id, revision_no, record_object_sha, content_version_id,"
            " reason, redaction_policy_version, first_observed_at_utc, run_id) VALUES (?,?,?,?,?,?,?,?)",
            (record_id, next_rev, rec_sha, version_id, reason, 1, iso(observed_at), run_id),
        )
        new_version_seq = next_seq if content_changed else int(existing["current_content_version"])
        conn.execute(
            "UPDATE source_records SET current_record_object_sha=?, last_seen_at_utc=?, current_content_hash=?,"
            " current_content_version=? WHERE id=?",
            (rec_sha, iso(observed_at), chash, new_version_seq, record_id),
        )

    derive(conn, response_sha, rec_sha, locator)
    conn.execute(
        "INSERT INTO observations (run_id, attempt_id, observation_key, family, response_object_sha,"
        " record_object_sha, record_locator, content_hash, status, observed_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            run_id,
            attempt_id,
            f"SR:{ikey}",
            family,
            response_sha,
            rec_sha,
            locator,
            chash,
            db_obs_status,
            iso(observed_at),
        ),
    )
    return {
        "record_id": record_id,
        "identity_key": ikey,
        "record_sha": rec_sha,
        "content_hash": chash,
        "outcome": outcome,
    }


def extract_by_locator(payload: object, locator: str) -> object:
    """Resolve a record_locator against a page payload — RFC 6901 pointer or `row:<n>`.

    The contract claims every RECORD object was extracted from a RESPONSE at a stated
    locator. That claim is only falsifiable if something actually performs the extraction,
    so the test suite does it rather than trusting the recorded string.
    """
    if locator.startswith("row:"):
        ordinal = int(locator.removeprefix("row:"))
        if not isinstance(payload, list):
            raise ValueError(f"row locator against non-array payload: {type(payload).__name__}")
        return payload[ordinal]
    if not locator.startswith("/"):
        raise ValueError(f"unrecognised locator form: {locator!r}")
    node = payload
    for raw_token in locator.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            try:
                index = int(token)
            except ValueError as exc:
                raise KeyError(f"locator {locator}: {token!r} is not an array index") from exc
            if not 0 <= index < len(node):
                raise KeyError(
                    f"locator {locator} does not resolve: index {index} out of range (len {len(node)})"
                )
            node = node[index]
        elif isinstance(node, dict):
            if token not in node:
                raise KeyError(f"locator {locator} does not resolve: missing {token!r}")
            node = node[token]
        else:
            raise TypeError(f"locator {locator} cannot descend into {type(node).__name__}")
    return node


def scalar(conn: sqlite3.Connection, sql: str, *args: object) -> object:
    return conn.execute(sql, args).fetchone()[0]
