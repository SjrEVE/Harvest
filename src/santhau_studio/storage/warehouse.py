"""Warehouse connection, schema bootstrap and the page-commit writer.

One transaction per page, in the order §5.2 fixes: objects durable on disk first, then
raw_objects rows, attempts, observations, versions, revisions, record update and the page
receipt — all inside a single BEGIN/COMMIT. A crash anywhere in that block leaves nothing
half-written, which is the only invariant §5.3 actually promises.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from santhau_studio.collectors.extraction import ExtractedRecord
from santhau_studio.domain.identity import (
    canonical_bytes,
    content_hash,
    identity_key,
    notice_group_key,
)
from santhau_studio.domain.records import IntegrityMismatchError, NormalizedRecord
from santhau_studio.platform_win.os_lock import DataRootLock
from santhau_studio.storage.migrations import migrate
from santhau_studio.storage.rawstore import LAYOUT_OBJECTS, ObjectRef, RawObjectStore


def find_schema(start: Path) -> Path | None:
    """Locate the frozen DDL by walking up from a module directory.

    The package sits under `<repo>/src` in a source tree but directly under the deployment
    folder in a packaged build, so the depth differs by one; a fixed `parents[n]` silently
    points at the wrong directory after packaging.
    """
    for parent in (start, *start.parents):
        candidate = parent / "docs" / "ddl" / "g0_b_proposed.sql"
        if candidate.is_file():
            return candidate
    return None


SCHEMA_PATH = find_schema(Path(__file__).resolve().parent) or (
    Path(__file__).resolve().parents[3] / "docs" / "ddl" / "g0_b_proposed.sql"
)

NORMALIZE_SCHEMA = 1
WAL_CHECKPOINT_BYTES = 64 * 1024 * 1024  # §3.10 pragmas: checkpoint by size, starting point 64 MB


def connect(path: Path | str, *, create_schema: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        # busy_timeout must be set before the mode pragma: switching/confirming WAL takes a
        # brief lock, and a reader opening mid-commit would otherwise fail with SQLITE_BUSY
        # because no busy handler exists yet.
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")  # §3.10/§5.2: set explicitly, not inherited
        conn.execute("PRAGMA foreign_keys=ON")  # meaningful only because FKs are declared for real
        if create_schema:
            # Bootstrap through the migration table rather than a bare executescript: a
            # database with no recorded version can never notice that the contract file
            # changed underneath it, which is the drift BUG-13 was about.
            migrate(conn, SCHEMA_PATH.parents[2])
    except BaseException:
        # On Windows a leaked handle on a database that failed to open blocks every later
        # rename, including the ones backup/restore needs.
        conn.close()
        raise
    return conn


def connect_read_only(path: Path | str, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """A reader's connection: no mode switches, no writes, no file creation.

    `connect()` runs `PRAGMA journal_mode=WAL`, which takes a lock and can collide with the
    writer's migration on a fresh database. A reader must never need that: WAL is already the
    database's persisted mode, so `mode=ro` + `query_only` is all a UI/status reader should do.
    """
    reference = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(reference, uri=True, isolation_level=None, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA query_only=ON")
    return conn


@dataclass(frozen=True)
class PageOutcome:
    records_new: int
    records_updated: int
    records_raw_only: int
    records_unchanged: int
    records_unresolved: int
    quarantined: int
    observation_ids: tuple[int, ...]

    @property
    def records_seen(self) -> int:
        return self.records_new + self.records_updated + self.records_raw_only + self.records_unchanged


class WarehouseWriter:
    """Writes pages into the warehouse. Single-writer by design (§2.2)."""

    def __init__(self, conn: sqlite3.Connection, store: RawObjectStore, *, source_key: str = "egp_home") -> None:
        self.conn = conn
        self.store = store
        self.source_key = source_key
        self._lock: DataRootLock | None = None

    @classmethod
    def open(
        cls,
        data_root: Path,
        *,
        create_schema: bool = False,
        role: str = "worker",
        source_key: str = "egp_home",
        redaction_policy_version: int = 1,
        raw_layout: str | None = None,
    ) -> WarehouseWriter:
        """The entry point for any process that intends to write (§5.2 single writer).

        Taking the data-root lock here, rather than in each caller, is what makes FAULT-06
        enforced by construction: a second worker on the same root cannot get a writer at all.

        `raw_layout` picks where *new* bytes land: `objects` (one file per object) or `segments`
        (append-only, measured ~128x faster to read back at 5k pages). Reads are dual-layout in
        both cases, so switching affects only future writes and never orphans old data.
        """
        layout = raw_layout or os.environ.get("SANTHAU_RAW_LAYOUT") or LAYOUT_OBJECTS
        data_root = Path(data_root)
        # A liveness probe (the GUI's lock check) holds this lock for microseconds; a worker
        # starting in exactly that window must retry rather than die with DataRootBusy. A real
        # second writer still gets refused — after ~1s instead of instantly.
        lock = DataRootLock(data_root, role=role).acquire(retries=5, retry_delay_seconds=0.2)
        try:
            store = RawObjectStore(
                data_root, redaction_policy_version=redaction_policy_version, layout=layout
            )
            conn = connect(data_root / "warehouse.sqlite", create_schema=create_schema)
        except BaseException:
            lock.release()
            raise
        writer = cls(conn, store, source_key=source_key)
        writer._lock = lock
        return writer

    def close(self) -> None:
        """Releases the data root for the next process; ordering matters, lock outlasts conn."""
        try:
            self.store.close()
        finally:
            self.conn.close()
            if self._lock is not None:
                self._lock.release()
                self._lock = None

    # ------------------------------------------------------------------ helpers

    def register_run(self, run_id: str, job_id: str, job_kind: str, *, policy_version: int = 1, requested_by: str = "local") -> None:
        self.conn.execute(
            "INSERT INTO runs (run_id, job_id, job_kind, lifecycle, started_at_utc, policy_version, requested_by)"
            " VALUES (?,?,?,'RUNNING',?,?,?)",
            (run_id, job_id, job_kind, _now(), policy_version, requested_by),
        )

    def finish_run(self, run_id: str, outcome: str) -> None:
        """§2.3: a run only reaches COMPLETED when it actually finished; death is INTERRUPTED."""
        lifecycle = "FINISHED" if outcome == "COMPLETED" else "TERMINATED"
        self.conn.execute(
            "UPDATE runs SET lifecycle=?, status=?, finished_at_utc=? WHERE run_id=?",
            (lifecycle, outcome, _now(), run_id),
        )

    def store_response(self, payload: Any) -> ObjectRef:
        return self.store.put(canonical_bytes(payload))

    def ensure_attempt(
        self, *, run_id: str, family: str, endpoint: str, interval_from: str, interval_to: str,
        page_no: int, attempt_no: int, outcome: str, response: ObjectRef | None, http_status: int = 200,
        error_kind: str | None = None,
    ) -> int:
        """Retry creates an attempt, never a new request (§3.3 rule 3)."""
        fingerprint = _request_fingerprint(family, endpoint, interval_from, interval_to, page_no)
        self.conn.execute(
            "INSERT OR IGNORE INTO fetch_requests"
            " (run_id, source_key, family, endpoint, interval_from_utc, interval_to_utc, page_no, request_fingerprint, created_at_utc)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, self.source_key, family, endpoint, interval_from, interval_to, page_no, fingerprint, _now()),
        )
        request_id = int(
            self.conn.execute(
                "SELECT id FROM fetch_requests WHERE run_id=? AND request_fingerprint=?", (run_id, fingerprint)
            ).fetchone()[0]
        )
        if response is not None:
            self._register_object(response)
        cur = self.conn.execute(
            "INSERT INTO fetch_attempts (request_id, attempt_no, started_at_utc, finished_at_utc, outcome,"
            " http_status, response_object_sha, byte_len, error_kind) VALUES (?,?,?,?,?,?,?,?,?)",
            (request_id, attempt_no, _now(), _now(), outcome, http_status, response.sha256 if response else None,
             response.byte_len if response else None, error_kind),
        )
        return int(cur.lastrowid)

    def _register_object(self, ref: ObjectRef) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO raw_objects (sha256, byte_len, codec, storage_rel_path, integrity_status,"
            " redaction_policy_version, created_at_utc) VALUES (?,?,?,?,?,?,?)",
            (ref.sha256, ref.byte_len, ref.codec, ref.storage_rel_path, "OK", self.store.redaction_policy_version, _now()),
        )

    def register_object(self, ref: ObjectRef) -> None:
        """Public seam for module writers that persist objects inside their own transaction."""
        self._register_object(ref)

    # ------------------------------------------------------------------ the page commit

    def commit_page(
        self,
        *,
        run_id: str,
        attempt_id: int,
        response_ref: ObjectRef,
        records: Iterable[ExtractedRecord],
    ) -> PageOutcome:
        """Persist one page atomically.

        `records` comes straight from `collectors.extraction.extract_page`, so the locator,
        the payload and the recorded hash were produced by one real extraction — this is what
        closes V-2. The writer re-hashes the payload it is handed and refuses a record whose
        hash disagrees, so a caller cannot substitute a fabricated derivation.
        """
        new = updated = raw_only = unchanged = unresolved = 0
        observation_ids: list[int] = []

        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self._register_object(response_ref)
            for extracted in records:
                locator = extracted.locator
                normalized = extracted.normalized
                record_ref = self.store.put(canonical_bytes(extracted.payload))
                if record_ref.sha256 != extracted.record_sha:
                    raise IntegrityMismatchError(
                        f"{locator}: payload hashes to {record_ref.sha256[:16]} but extractor recorded "
                        f"{extracted.record_sha[:16]}"
                    )
                self._register_object(record_ref)
                self.conn.execute(
                    "INSERT OR IGNORE INTO object_derivation"
                    " (response_object_sha, record_object_sha, record_locator, redaction_policy_version, derived_at_utc)"
                    " VALUES (?,?,?,?,?)",
                    (response_ref.sha256, record_ref.sha256, locator, self.store.redaction_policy_version, _now()),
                )
                outcome, obs_id = self._ingest_record(
                    run_id=run_id, attempt_id=attempt_id, response_sha=response_ref.sha256,
                    record_sha=record_ref.sha256, locator=locator, normalized=normalized,
                )
                observation_ids.append(obs_id)
                new += outcome == "NEW"
                updated += outcome == "UPDATED"
                raw_only += outcome == "RAW_ONLY"
                unchanged += outcome == "UNCHANGED"
                unresolved += outcome == "UNRESOLVED"

            seen = new + updated + raw_only + unchanged + unresolved
            # One receipt per logical request (§2.3): a retry is a new attempt in
            # fetch_attempts, and the receipt advances to record which attempt committed.
            # Counters accumulate because they describe work done for this request.
            request_id = int(
                self.conn.execute("SELECT request_id FROM fetch_attempts WHERE id=?", (attempt_id,)).fetchone()[0]
            )
            self.conn.execute(
                "INSERT INTO page_receipts AS pr"
                " (run_id, request_id, attempt_id, response_object_sha, page_no,"
                "  records_seen, records_new, records_updated, records_raw_only, records_unresolved,"
                "  response_received, page_committed, proof_verified, snapshot_published, committed_at_utc)"
                " VALUES (?,?,?,?,?, ?,?,?,?,?, 1,1,0,0, ?)"
                " ON CONFLICT(run_id, request_id) DO UPDATE SET"
                "   attempt_id=excluded.attempt_id,"
                "   response_object_sha=excluded.response_object_sha,"
                "   records_seen=excluded.records_seen,"
                "   records_new=pr.records_new+excluded.records_new,"
                "   records_updated=pr.records_updated+excluded.records_updated,"
                "   records_raw_only=pr.records_raw_only+excluded.records_raw_only,"
                "   records_unresolved=pr.records_unresolved+excluded.records_unresolved,"
                "   response_received=1, page_committed=1, committed_at_utc=excluded.committed_at_utc",
                (
                    run_id,
                    request_id,
                    attempt_id,
                    response_ref.sha256,
                    _page_no_of(self.conn, attempt_id),
                    seen,
                    new,
                    updated,
                    raw_only,
                    unresolved,
                    _now(),
                ),
            )
            self.conn.execute("COMMIT")
        except BaseException:
            # An OSError (disk full, permissions, damaged store) has to unwind the transaction
            # just like an SQLite error does. Leaving the connection mid-transaction absorbs the
            # *next* page into a half-written one, and a crash then loses both.
            self._rollback()
            raise

        return PageOutcome(new, updated, raw_only, unchanged, unresolved, 0, tuple(observation_ids))

    def _ingest_record(
        self, *, run_id: str, attempt_id: int, response_sha: str, record_sha: str,
        locator: str, normalized: NormalizedRecord,
    ) -> tuple[str, int]:
        """§3.5 decision table, executed against the real schema."""
        chash = content_hash(normalized.fields)
        if normalized.natural_key is None:
            obs_status = "UNRESOLVED"
            obs_key = f"UNRESOLVED:{record_sha[:16]}"
            cur = self.conn.execute(
                "INSERT INTO observations (run_id, attempt_id, observation_key, family, response_object_sha,"
                " record_object_sha, record_locator, content_hash, status, observed_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, attempt_id, obs_key, normalized.family, response_sha, record_sha, locator, chash, obs_status, _now()),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO unresolved_records (run_id, attempt_id, observation_id, family,"
                " record_object_sha, response_object_sha, record_locator, reason, raised_at_utc)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, attempt_id, int(cur.lastrowid), normalized.family, record_sha, response_sha, locator,
                 "MISSING_NATURAL_KEY", _now()),
            )
            return "UNRESOLVED", int(cur.lastrowid)

        idkey = identity_key(normalized.family, normalized.natural_key, normalized.version_state, normalized.version_raw)
        existing = self.conn.execute(
            "SELECT id, current_content_hash, current_record_object_sha, current_content_version,"
            " redaction_policy_version FROM source_records"
            " WHERE source_key=? AND family=? AND identity_key=?",
            (self.source_key, normalized.family, idkey),
        ).fetchone()

        if existing is None:
            return self._create_record(run_id, attempt_id, response_sha, record_sha, locator, normalized, idkey, chash)

        record_id = int(existing["id"])
        content_changed = existing["current_content_hash"] != chash
        raw_changed = existing["current_record_object_sha"] != record_sha
        policy_changed = int(existing["redaction_policy_version"]) != self.store.redaction_policy_version

        if content_changed:
            status, outcome = "UPDATED", "UPDATED"
        elif raw_changed:
            status, outcome = "UPDATED_RAW", "RAW_ONLY"
        elif policy_changed:
            # Re-derivation under a new redaction policy is evidence about US, not the source.
            status, outcome = "UNCHANGED", "REPLAY"
        else:
            status, outcome = "UNCHANGED", "UNCHANGED"

        self.conn.execute(
            "INSERT INTO observations (run_id, attempt_id, observation_key, family, response_object_sha,"
            " record_object_sha, record_locator, content_hash, status, observed_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run_id, attempt_id, f"SR:{idkey}", normalized.family, response_sha, record_sha, locator, chash, status, _now()),
        )
        observation_id = int(self.conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        if outcome == "UNCHANGED":
            self.conn.execute("UPDATE source_records SET last_seen_at_utc=? WHERE id=?", (_now(), record_id))
            return "UNCHANGED", observation_id

        version_id: int | None = None
        next_seq: int | None = None
        if content_changed:
            next_seq = int(existing["current_content_version"]) + 1
            cur = self.conn.execute(
                "INSERT INTO source_record_versions (source_record_id, content_version_seq, content_hash,"
                " normalized_json, introducing_record_sha, introducing_response_sha, fetched_at_utc,"
                " change_kind, normalize_schema) VALUES (?,?,?,?,?,?,?,?,?)",
                (record_id, next_seq, chash, dumps(normalized.fields), record_sha, response_sha, _now(),
                 "UPDATED", NORMALIZE_SCHEMA),
            )
            version_id = int(cur.lastrowid)

        revision_no = int(self.conn.execute(
            "SELECT COUNT(*) FROM raw_revisions WHERE source_record_id=?", (record_id,)
        ).fetchone()[0]) + 1
        reason = {"UPDATED": "SEMANTIC_CHANGE", "RAW_ONLY": "RAW_ONLY_CHANGE", "REPLAY": "REPLAY"}[outcome]
        self.conn.execute(
            "INSERT INTO raw_revisions (source_record_id, revision_no, record_object_sha, content_version_id,"
            " reason, redaction_policy_version, first_observed_at_utc, run_id) VALUES (?,?,?,?,?,?,?,?)",
            (record_id, revision_no, record_sha, version_id, reason, self.store.redaction_policy_version, _now(), run_id),
        )

        if outcome == "REPLAY":
            # Only the policy stamp moves. Counters and current hashes stay exactly where they were.
            self.conn.execute(
                "UPDATE source_records SET redaction_policy_version=?, last_seen_at_utc=? WHERE id=?",
                (self.store.redaction_policy_version, _now(), record_id),
            )
            return "UNCHANGED", observation_id

        self.conn.execute(
            "UPDATE source_records SET current_record_object_sha=?, current_content_hash=?,"
            " current_content_version=?, last_seen_at_utc=?,"
            " redaction_policy_version=? WHERE id=?",
            # next_seq, not version_id: the column is a sequence counter, and writing the
            # version row's primary key here made current_content_version jump to 4 for a
            # record that had only two content versions.
            (
                record_sha,
                chash,
                next_seq if next_seq is not None else int(existing["current_content_version"]),
                _now(),
                self.store.redaction_policy_version,
                record_id,
            ),
        )
        return outcome, observation_id

    def _create_record(
        self, run_id: str, attempt_id: int, response_sha: str, record_sha: str, locator: str,
        normalized: NormalizedRecord, idkey: str, chash: str,
    ) -> tuple[str, int]:
        stamp = _now()
        cur = self.conn.execute(
            "INSERT INTO source_records (source_key, family, natural_key, version_state, version_raw,"
            " identity_key, first_seen_at_utc, last_seen_at_utc, current_content_version, current_content_hash,"
            " current_record_object_sha, integrity_status, redaction_policy_version)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.source_key, normalized.family, normalized.natural_key, normalized.version_state,
             normalized.version_raw, idkey, stamp, stamp, 1, chash, record_sha, "OK",
             self.store.redaction_policy_version),
        )
        record_id = int(cur.lastrowid)
        vcur = self.conn.execute(
            "INSERT INTO source_record_versions (source_record_id, content_version_seq, content_hash,"
            " normalized_json, introducing_record_sha, introducing_response_sha, fetched_at_utc,"
            " change_kind, normalize_schema) VALUES (?,1,?,?,?,?,?, 'NEW', ?)",
            (record_id, chash, dumps(normalized.fields), record_sha, response_sha, stamp, NORMALIZE_SCHEMA),
        )
        self.conn.execute(
            "INSERT INTO raw_revisions (source_record_id, revision_no, record_object_sha, content_version_id,"
            " reason, redaction_policy_version, first_observed_at_utc, run_id) VALUES (?,1,?,?, 'NEW_RECORD', ?,?,?)",
            (record_id, record_sha, int(vcur.lastrowid), self.store.redaction_policy_version, stamp, run_id),
        )
        self.conn.execute(
            "INSERT INTO observations (run_id, attempt_id, observation_key, family, response_object_sha,"
            " record_object_sha, record_locator, content_hash, status, observed_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run_id, attempt_id, f"SR:{idkey}", normalized.family, response_sha, record_sha, locator, chash, "NEW", stamp),
        )
        observation_id = int(self.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        return "NEW", observation_id

    # ------------------------------------------------------------------ quarantine

    def quarantine_response(self, *, run_id: str, attempt_id: int, response_ref: ObjectRef, failure: str, detail: str) -> None:
        """§5.5: unreadable page is quarantined at page level. No record, no observation."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self._register_object(response_ref)
            self.conn.execute(
                "INSERT INTO quarantined_responses (run_id, attempt_id, response_object_sha, failure, detail, quarantined_at_utc)"
                " VALUES (?,?,?,?,?,?)",
                (run_id, attempt_id, response_ref.sha256, failure, detail[:400], _now()),
            )
            self.conn.execute("COMMIT")
        except BaseException:
            self._rollback()
            raise
        self.store.quarantine_page(response_ref.sha256)

    def _rollback(self) -> None:
        # Nothing open to unwind is not a second failure; the original error is the one worth raising.
        with contextlib.suppress(sqlite3.Error):
            self.conn.execute("ROLLBACK")

    def checkpoint_wal(self) -> None:
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def notice_group_for(self, family: str, natural_key: str) -> str:
        return notice_group_key(family, natural_key)


def _request_fingerprint(family: str, endpoint: str, frm: str, to: str, page_no: int) -> str:
    """Full sha256 over the logical request coordinates.

    Truncating the *hex of the JSON* (as an earlier draft did) keeps only the leading
    bytes, which are family + endpoint + the start of the interval. page_no sits at the
    end and was cut entirely, so page 0 and page 1 of the same window collapsed into one
    fetch_requests row and the second page silently overwrote the first receipt.
    """
    return hashlib.sha256(canonical_bytes([family, endpoint, frm, to, page_no])).hexdigest()


def _page_no_of(conn: sqlite3.Connection, attempt_id: int) -> int:
    row = conn.execute(
        "SELECT r.page_no FROM fetch_attempts a JOIN fetch_requests r ON r.id=a.request_id WHERE a.id=?",
        (attempt_id,),
    ).fetchone()
    return int(row["page_no"]) if row else 0


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
