"""Read-only provenance access for the desktop UI.

RC3-8 boundary change: the GUI no longer finds audit/provenance tables in
``serving.sqlite`` — the warehouse remains their only authority. This module is
the single sanctioned read path. It is strictly read-only; writing warehouse
state from the GUI stays forbidden.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from santhau_studio.storage.warehouse import connect_read_only

AUDIT_TABLES = ("observations", "object_derivation", "raw_revisions", "raw_objects")


class WarehouseReader:
    """Open the warehouse read-only and answer provenance questions."""

    def __init__(self, data_root: Path | str):
        """Accept either a data root directory or the warehouse.sqlite path itself."""
        given = Path(data_root)
        self.path = given if given.name == "warehouse.sqlite" else given / "warehouse.sqlite"

    def __enter__(self):
        self._conn = connect_read_only(self.path)
        self._conn.row_factory = sqlite3.Row
        return self

    def __exit__(self, *_exc):
        self.close()
        return False

    def close(self) -> None:
        conn = getattr(self, "_conn", None)
        if conn is not None:
            conn.close()
            self._conn = None

    def record_provenance(self, record_id: int, identity_key: str, source_key: str) -> dict:
        versions = [
            dict(row) for row in self._conn.execute(
                "SELECT content_version_seq, content_hash, change_kind, fetched_at_utc,"
                "       normalize_schema, normalized_json"
                " FROM source_record_versions WHERE source_record_id=?"
                " ORDER BY content_version_seq DESC LIMIT 50", (record_id,))
        ]
        raw_revisions = [
            dict(row) for row in self._conn.execute(
                "SELECT revision_no, record_object_sha, reason, first_observed_at_utc, run_id"
                " FROM raw_revisions WHERE source_record_id=? ORDER BY revision_no DESC LIMIT 50",
                (record_id,))
        ]
        observations = self._conn.execute(
            "SELECT COUNT(*) FROM observations o JOIN fetch_attempts a ON a.id=o.attempt_id"
            " JOIN fetch_requests q ON q.id=a.request_id WHERE o.observation_key=? AND q.source_key=?",
            (f"SR:{identity_key}", source_key),
        ).fetchone()[0]
        return {"versions": versions, "raw_revisions": raw_revisions, "observations": observations,
                "aliases": self.aliases(record_id), "identity_actions": self.identity_actions()}

    def history_counts(self, record_id: int, history: dict[str, tuple[str, str]]) -> dict[str, int]:
        return {
            name: self._conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE source_record_id=?", (record_id,)
            ).fetchone()[0]
            for name, (table, _sequence) in history.items()
        }

    def raw_object_status(self, sha256: str) -> dict | None:
        row = self._conn.execute(
            "SELECT byte_len,integrity_status FROM raw_objects WHERE sha256=?", (sha256,)
        ).fetchone()
        return None if row is None else {"byte_len": row[0], "integrity_status": row[1]}

    def raw_integrity_summary(self) -> tuple[list[dict], list[dict]]:
        stats = [dict(r) for r in self._conn.execute(
            "SELECT integrity_status, COUNT(*) AS count FROM raw_objects GROUP BY integrity_status")]
        failures = [dict(r) for r in self._conn.execute(
            "SELECT sha256,integrity_status FROM raw_objects WHERE integrity_status<>'OK' LIMIT 50")]
        return stats, failures

    def family_totals(self) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
        records = {r[0]: r[1] for r in self._conn.execute("SELECT family, COUNT(*) FROM source_records GROUP BY family")}
        revisions = {r[0]: r[1] for r in self._conn.execute(
            "SELECT sr.family, COUNT(*) FROM raw_revisions r JOIN source_records sr ON sr.id = r.source_record_id GROUP BY sr.family")}
        observations = {r[0]: r[1] for r in self._conn.execute("SELECT family, COUNT(*) FROM observations GROUP BY family")}
        return records, revisions, observations

    def all_object_hashes(self) -> list[str]:
        return [r[0] for r in self._conn.execute("SELECT sha256 FROM raw_objects")]

    def aliases(self, record_id: int) -> list[dict]:
        return [dict(row) for row in self._conn.execute(
            "SELECT alias_kind,alias_value,evidence_json FROM source_aliases"
            " WHERE source_record_id=? LIMIT 100", (record_id,))]

    def identity_actions(self) -> list[dict]:
        return [dict(row) for row in self._conn.execute(
            "SELECT id,action,target_ref,actor_sid,reason,undo_action_id,acted_at_utc"
            " FROM identity_audit_actions ORDER BY id DESC LIMIT 50")]

    def legacy_imports(self) -> list[dict]:
        return [dict(row) for row in self._conn.execute(
            "SELECT batch_key,rows_seen,rows_imported,rows_rejected,state FROM import_batches"
            " ORDER BY id DESC LIMIT 20")]

    def latest_run_states(self, job_ids: list[str]) -> list[dict]:
        if not job_ids:
            return []
        return [dict(row) for row in self._conn.execute(
            "SELECT job_id,lifecycle,status FROM runs"
            " WHERE job_id IN (SELECT value FROM json_each(?))"
            " ORDER BY started_at_utc DESC,rowid DESC", (json.dumps(job_ids),))]

    def relation_edges(self, record_id: int) -> list[dict]:
        return [dict(row) for row in self._conn.execute(
            "SELECT DISTINCT e.* FROM relation_edges e JOIN entity_source_links l "
            "ON l.entity_id=e.from_entity OR l.entity_id=e.to_entity WHERE l.source_record_id=?",
            (record_id,))]


def read_provenance(data_root: Path, record_id: int, identity_key: str, source_key: str) -> dict:
    """One-shot helper for callers that do not hold a reader open."""
    with WarehouseReader(data_root) as reader:
        return reader.record_provenance(record_id, identity_key, source_key)
