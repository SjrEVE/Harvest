"""UI read path (Milestone F): every number a screen shows is read from the serving snapshot.

Two boundaries this module keeps:

* **The GUI never opens the warehouse for reading and never writes warehouse state.** Data comes
  from `serving.sqlite` through `connect_read_only`; job requests are the one document the GUI
  publishes, by writing a file into `jobs/queued/` that the worker claims by atomic rename (§2.3).
* **Worker truth comes from the worker.** Liveness is the data-root lock holder plus a pipe
  ping; progress is the worker's own `GET_STATUS` answer, not a re-derivation from files.

`degraded` never renders as complete: the serving snapshot carries its own stale/degraded marker
and every payload surfaces it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from santhau_studio.domain.text import fold_vietnamese
from santhau_studio.platform_win.os_lock import LOCK_SUFFIX, DataRootBusy, DataRootLock, holder_info
from santhau_studio.storage.serving import serving_state
from santhau_studio.storage.warehouse import connect_read_only
from santhau_studio.workers.ipc import (
    PipeClient,
    PipeClosedError,
    PipeTransportError,
    worker_pipe_name,
)
from santhau_studio.workers.jobs import JOB_KINDS, JobError, JobRequest, enqueue_job

DISK_SCAN_LIMIT = 50_000
SEARCH_LIMIT_MAX = 200
APP_VERSION = "0.0.1"

_TITLE_KEYS = ("title", "bidName", "planName", "projectName", "contractName", "name")
_TITLE_SQL = "COALESCE(" + ", ".join(
    f"json_extract(v.normalized_json, '$.{key}')" for key in _TITLE_KEYS
) + ", '')"


def default_data_root() -> Path:
    override = os.environ.get("SANTHAU_DATA_ROOT")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "SanThauDataStudio" / "data"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


class StudioBackend:
    """Plain-Python view model: dicts in, dicts out, so it is testable without Qt."""

    def __init__(self, data_root: Path | str | None = None) -> None:
        self.data_root = Path(data_root) if data_root else default_data_root()

    # ------------------------------------------------------------------ plumbing

    @property
    def serving_path(self) -> Path:
        return self.data_root / "serving.sqlite"

    def _serving(self) -> sqlite3.Connection | None:
        try:
            return connect_read_only(self.serving_path)
        except sqlite3.Error:
            return None

    def _jobs_dir(self) -> Path:
        return self.data_root / "jobs"

    # ------------------------------------------------------------------ UI-01 onboarding

    def onboarding(self) -> dict[str, Any]:
        root = self.data_root
        payload: dict[str, Any] = {
            "data_root": str(root),
            "exists": root.exists(),
            "warehouse_present": (root / "warehouse.sqlite").exists(),
            "serving_present": self.serving_path.exists(),
            "queued_jobs": self._count_files(self._jobs_dir() / "queued"),
            "active_jobs": self._count_files(self._jobs_dir() / "active"),
            "worker": self.worker_status(),
            "snapshot": serving_state(self.serving_path),
            "app_version": APP_VERSION,
            "policy_note": "Read-only UI: dữ liệu hiển thị từ serving snapshot, không phải warehouse.",
        }
        try:
            usage = shutil.disk_usage(root if root.exists() else root.parent)
            payload["free_bytes"] = usage.free
            payload["total_bytes"] = usage.total
        except OSError as exc:
            payload["disk_error"] = str(exc)
        return payload

    def validate_data_root(self, path_text: str) -> dict[str, Any]:
        """UI-01: a data root is usable when we can write a probe and the volume reports size."""
        path = Path(path_text).expanduser()
        result: dict[str, Any] = {"path": str(path), "ok": False, "writable": False}
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            usage = shutil.disk_usage(path)
            result.update(
                ok=True,
                writable=True,
                free_bytes=usage.free,
                total_bytes=usage.total,
                warehouse_present=(path / "warehouse.sqlite").exists(),
                serving_present=(path / "serving.sqlite").exists(),
            )
        except OSError as exc:
            result.update(error=f"{type(exc).__name__}: {exc}")
        return result

    # ------------------------------------------------------------------ worker + jobs

    def worker_status(self) -> dict[str, Any]:
        """Liveness is the data-root lock, not a leftover pid in an inert file.

        A holder record alone proves nothing: the process may have exited, or the pid may now
        belong to the GUI itself. A non-blocking probe acquire answers the real question — is a
        writer holding this root right now — and only then is the record read to name it.
        """
        held = self._lock_held()
        holder = holder_info(self.data_root / f"writer{LOCK_SUFFIX}") if held else None
        payload: dict[str, Any] = {
            "running": held,
            "pid": holder.get("pid") if holder else None,
            "role": holder.get("role") if holder else None,
            "acquired_at": holder.get("acquired_at") if holder else None,
            "pipe_ok": False,
        }
        if held:
            try:
                client = PipeClient.connect(worker_pipe_name(), timeout_seconds=0.5)
                try:
                    payload["pipe_ok"] = bool(client.request({"op": "ping"}).get("ok"))
                finally:
                    client.close()
            except (PipeTransportError, PipeClosedError, OSError, json.JSONDecodeError) as exc:
                payload["note"] = type(exc).__name__
        return payload

    def _lock_held(self) -> bool:
        lock = DataRootLock(self.data_root, role="probe")
        try:
            lock.acquire()
        except DataRootBusy:
            return True
        else:
            lock.release()
            return False

    def jobs(self) -> dict[str, Any]:
        """UI-04: the queue as documents on disk, plus the worker's answer for the active job."""
        payload: dict[str, Any] = {"worker": self.worker_status(), "queued": [], "active": [], "done": []}
        active_job = None
        for state in ("queued", "active", "done"):
            for path in sorted((self._jobs_dir() / state).glob("*.json")):
                try:
                    document = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    payload[state].append({"file": path.name, "unreadable": True})
                    continue
                entry = {
                    "job_id": document.get("job_id"),
                    "job_kind": document.get("job_kind"),
                    "created_at": document.get("created_at"),
                    "mode": document.get("mode"),
                    "families": [s.get("family") for s in document.get("sources", []) if isinstance(s, dict)],
                    "scope": document.get("scope"),
                }
                payload[state].append(entry)
                if state == "active":
                    active_job = entry
        if active_job and payload["worker"].get("pipe_ok"):
            payload["active_status"] = self.job_status(str(active_job["job_id"]))
        return payload

    def job_status(self, job_id: str) -> dict[str, Any]:
        try:
            client = PipeClient.connect(worker_pipe_name(), timeout_seconds=1.0)
        except (PipeTransportError, OSError) as exc:
            return {"ok": False, "error": f"worker unreachable: {type(exc).__name__}"}
        try:
            return client.request({"op": "status", "job_id": job_id})
        except (PipeClosedError, OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            client.close()

    def enqueue_collection(self, payload: dict[str, Any]) -> dict[str, Any]:
        """UI-04: publish the immutable job request; the worker claims it by rename."""
        try:
            kind = str(payload.get("job_kind", "BACKFILL"))
            if kind not in JOB_KINDS:
                return {"ok": False, "error": f"unknown job_kind {kind}"}
            family = str(payload["family"])
            scope = {"from": str(payload["from"]), "to": str(payload["to"])}
            rule = self._verified_rule(str(payload.get("source_key") or "egp_home"), family)
            if rule is None:
                # §4.2 fail-closed: without a canary-verified boundary rule the run would be a
                # refused job anyway; say so at submission instead of queueing a guaranteed block.
                return {"ok": False, "error": "BOUNDARY_RULE_UNVERIFIED: chưa có rule canary-verified"}
            request = JobRequest(
                job_id=f"job-{uuid.uuid4().hex[:12]}",
                job_kind=kind,
                requested_by=str(payload.get("requested_by") or "gui"),
                sources=(
                    {
                        "source_key": rule["source_key"],
                        "family": family,
                        "endpoint": rule["endpoint"],
                        "adapter_sha": rule["adapter_sha"],
                    },
                ),
                scope=scope,
                mode=str(payload.get("mode") or "interactive"),
                created_at=_now(),
            )
            path = enqueue_job(self.data_root, request)
        except (JobError, KeyError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "job_id": request.job_id, "queued_as": path.name}

    def _verified_rule(self, source_key: str, family: str) -> dict[str, Any] | None:
        conn = self._serving()
        if conn is None:
            return None
        try:
            with closing(conn):
                row = conn.execute(
                    "SELECT source_key, endpoint, adapter_sha FROM filter_boundary_rules"
                    " WHERE source_key=? AND family=? AND canary_verified=1"
                    " ORDER BY rule_version DESC, verified_at_utc DESC LIMIT 1",
                    (source_key, family),
                ).fetchone()
        except sqlite3.Error:
            return None
        return dict(row) if row else None

    def worker_command(self, command: str, job_id: str | None = None) -> dict[str, Any]:
        try:
            client = PipeClient.connect(worker_pipe_name(), timeout_seconds=1.0)
        except (PipeTransportError, OSError) as exc:
            return {"ok": False, "error": f"worker unreachable: {type(exc).__name__}"}
        try:
            payload = {"op": "command", "command": command}
            if job_id:
                payload["job_id"] = job_id
            return client.request(payload)
        except (PipeClosedError, OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            client.close()

    # ------------------------------------------------------------------ UI-02 control center

    def control_center(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "snapshot": serving_state(self.serving_path),
            "worker": self.worker_status(),
            "data_root": str(self.data_root),
            "disk": self._disk_usage(),
            "families": [],
        }
        conn = self._serving()
        if conn is None:
            return payload
        with closing(conn):
            try:
                counts = _family_counts(conn)
            except sqlite3.Error as exc:
                payload["error"] = f"serving unreadable: {exc}"
                return payload
        for family, entry in sorted(counts.items()):
            entry["family"] = family
            payload["families"].append(entry)
        return payload

    def _disk_usage(self) -> dict[str, Any]:
        total = 0
        files = 0
        truncated = False
        stack = [self.data_root]
        while stack:
            directory = stack.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    else:
                        total += entry.stat(follow_symlinks=False).st_size
                        files += 1
                except OSError:
                    continue
                if files >= DISK_SCAN_LIMIT:
                    truncated = True
                    stack.clear()
                    break
        return {"bytes": total, "files": files, "sampled": truncated, "limit": DISK_SCAN_LIMIT}

    # ------------------------------------------------------------------ UI-03 sources

    def sources(self) -> dict[str, Any]:
        conn = self._serving()
        if conn is None:
            return {"present": False, "registry": [], "boundary_rules": []}
        with closing(conn):
            try:
                registry = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT source_key, display_name, endpoint_family, access_state, verified_at_utc, notes"
                        " FROM source_registry ORDER BY source_key"
                    )
                ]
                rules = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT rule_key, rule_version, source_key, family, boundary_kind, precision,"
                        " canary_verified, verified_at_utc, substr(adapter_sha, 1, 12) AS adapter_sha12"
                        " FROM filter_boundary_rules ORDER BY source_key, family, rule_version"
                    )
                ]
            except sqlite3.Error as exc:
                return {"present": True, "error": str(exc), "registry": [], "boundary_rules": []}
        return {"present": True, "registry": registry, "boundary_rules": rules}

    # ------------------------------------------------------------------ UI-05 coverage

    def coverage(self, family: str | None = None) -> dict[str, Any]:
        conn = self._serving()
        if conn is None:
            return {"present": False, "intervals": [], "summary": [], "watermarks": []}
        with closing(conn):
            try:
                rows = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT ci.id, ci.family, ci.interval_from_utc, ci.interval_to_utc, ci.granularity,"
                        "       ci.status, ci.proof_id,"
                        "       cp.distinct_ids, cp.total_first, cp.total_last, cp.request_count,"
                        "       cp.granularity AS proof_granularity, cp.verified_at_utc"
                        " FROM coverage_intervals ci LEFT JOIN coverage_proofs cp ON cp.id = ci.proof_id"
                        " WHERE (?1 IS NULL OR ci.family = ?1)"
                        " ORDER BY ci.interval_from_utc DESC, ci.id DESC LIMIT 500",
                        (family,),
                    )
                ]
                summary = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT ci.family, COUNT(*) AS claimed,"
                        "  SUM(CASE WHEN ci.status='COMPLETE' AND ci.proof_id IS NOT NULL THEN 1 ELSE 0 END) AS walked,"
                        "  SUM(CASE WHEN ci.status='COMPLETE' AND ci.proof_id IS NULL THEN 1 ELSE 0 END) AS complete_without_proof,"
                        "  SUM(CASE WHEN ci.status='PARTIAL' THEN 1 ELSE 0 END) AS partial,"
                        "  SUM(CASE WHEN ci.status='BLOCKED' THEN 1 ELSE 0 END) AS blocked,"
                        "  SUM(CASE WHEN ci.status IN ('FAILED','INTERRUPTED') THEN 1 ELSE 0 END) AS failed,"
                        "  MIN(ci.interval_from_utc) AS earliest, MAX(ci.interval_to_utc) AS latest"
                        " FROM coverage_intervals ci"
                        " WHERE (?1 IS NULL OR ci.family = ?1) GROUP BY ci.family ORDER BY ci.family",
                        (family,),
                    )
                ]
                watermarks = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT family, watermark_from_utc, watermark_to_utc, updated_at_utc FROM watermarks"
                        " WHERE (?1 IS NULL OR family = ?1) ORDER BY family",
                        (family,),
                    )
                ]
            except sqlite3.Error as exc:
                return {"present": True, "error": str(exc), "intervals": [], "summary": [], "watermarks": []}
        # §4.1: a COMPLETE row without a proof is a contract violation the UI must shout about,
        # never smooth over — the DDL CHECK should make it impossible, and this makes it visible.
        for entry in rows:
            if entry["status"] == "COMPLETE" and entry["proof_id"] is None:
                entry["reason"] = "COMPLETE_WITHOUT_PROOF"
            elif entry["status"] == "COMPLETE":
                entry["reason"] = None
            else:
                entry["reason"] = "NO_PROOF" if entry["proof_id"] is None else "VERDICT"
        return {"present": True, "intervals": rows, "summary": summary, "watermarks": watermarks}

    # ------------------------------------------------------------------ UI-06 explorer

    def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text") or "").strip()
        family = str(payload.get("family") or "").strip() or None
        try:
            limit = min(max(int(payload.get("limit", 50)), 1), SEARCH_LIMIT_MAX)
        except (TypeError, ValueError):
            limit = 50
        fts = _fts_query(text)
        if not fts:
            return {"query": text, "hits": [], "note": "EMPTY_QUERY"}
        conn = self._serving()
        if conn is None:
            return {"query": text, "hits": [], "note": "NO_SNAPSHOT"}
        with closing(conn):
            try:
                hits = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT s.record_id, COALESCE(sr.family, s.family) AS family, s.natural_key,"
                        f"       {_TITLE_SQL} AS title_display,"
                        "        COALESCE(json_extract(v.normalized_json, '$.investorName'), '') AS investor_display,"
                        "        sr.version_state, sr.version_raw, sr.last_seen_at_utc"
                        " FROM search_index s"
                        " LEFT JOIN source_records sr ON sr.id = s.record_id"
                        " LEFT JOIN source_record_versions v ON v.id = ("
                        "   SELECT id FROM source_record_versions WHERE source_record_id = s.record_id"
                        "   ORDER BY content_version_seq DESC LIMIT 1)"
                        " WHERE search_index MATCH ?1 AND (?2 IS NULL OR s.family = ?2)"
                        " ORDER BY rank LIMIT ?3",
                        (fts, fold_vietnamese(family) if family else None, limit),
                    )
                ]
            except sqlite3.Error as exc:
                return {"query": text, "hits": [], "error": f"{type(exc).__name__}: {exc}"}
        return {"query": text, "hits": hits, "note": None if hits else "NO_MATCH"}

    # ------------------------------------------------------------------ UI-07 record detail

    def record_detail(self, record_id: int) -> dict[str, Any]:
        conn = self._serving()
        if conn is None:
            return {"present": False}
        with closing(conn):
            try:
                record = conn.execute(
                    "SELECT id, source_key, family, natural_key, version_state, version_raw, identity_key,"
                    "       first_seen_at_utc, last_seen_at_utc, current_content_version, current_content_hash,"
                    "       integrity_status FROM source_records WHERE id=?",
                    (record_id,),
                ).fetchone()
                if record is None:
                    return {"present": False}
                key = record["identity_key"]
                detail = {
                    "present": True,
                    "record": dict(record),
                    "versions": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT content_version_seq, content_hash, change_kind, fetched_at_utc,"
                            "       normalize_schema, normalized_json"
                            " FROM source_record_versions WHERE source_record_id=?"
                            " ORDER BY content_version_seq",
                            (record_id,),
                        )
                    ],
                    "raw_revisions": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT revision_no, record_object_sha, reason, first_observed_at_utc, run_id"
                            " FROM raw_revisions WHERE source_record_id=? ORDER BY revision_no",
                            (record_id,),
                        )
                    ],
                    "observations": conn.execute(
                        "SELECT COUNT(*) FROM observations WHERE observation_key=?",
                        (f"SR:{key}",),
                    ).fetchone()[0],
                    "deep_captures": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT lane, endpoint_kind, completeness, stale, stale_reason,"
                            "       captured_at_utc, refresh_due_at_utc, latest_capture_seq"
                            " FROM deep_captures WHERE source_record_id=? ORDER BY lane, endpoint_kind",
                            (record_id,),
                        )
                    ],
                    "lots": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT lot_no_raw, lot_label, lot_version, goods_count FROM lots"
                            " WHERE source_record_id=? ORDER BY lot_no_raw",
                            (record_id,),
                        )
                    ],
                    "goods_rows": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT row_ordinal, name, specification, unit, quantity_presence, quantity,"
                            "       quantity_raw FROM goods_rows WHERE source_record_id=? ORDER BY row_ordinal",
                            (record_id,),
                        )
                    ],
                    "documents": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT document_kind, name, media_type, byte_len FROM documents"
                            " WHERE source_record_id=? ORDER BY document_kind, name",
                            (record_id,),
                        )
                    ],
                    "contractor_memberships": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT party_name, role, party_tax_id_presence, party_tax_id,"
                            "       membership_share_presence, membership_share, ranking"
                            " FROM contractor_memberships WHERE source_record_id=?"
                            " ORDER BY COALESCE(ranking, 999), party_name",
                            (record_id,),
                        )
                    ],
                    "notice_groups": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT g.id AS group_id, g.group_kind, m.publication_label"
                            " FROM notice_group_members m JOIN notice_groups g ON g.id = m.group_id"
                            " WHERE m.source_record_id=?",
                            (record_id,),
                        )
                    ],
                    "entity_links": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT entity_id, relation, method FROM entity_source_links"
                            " WHERE source_record_id=? ORDER BY relation",
                            (record_id,),
                        )
                    ],
                }
            except sqlite3.Error as exc:
                return {"present": True, "error": f"{type(exc).__name__}: {exc}"}
        return detail

    # ------------------------------------------------------------------ UI-08 deep queue

    def deep_queue(self) -> dict[str, Any]:
        conn = self._serving()
        if conn is None:
            return {"present": False, "rows": [], "summary": {}}
        with closing(conn):
            try:
                rows = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT dc.source_record_id, sr.family, sr.natural_key, dc.lane, dc.endpoint_kind,"
                        "       dc.completeness, dc.stale, dc.stale_reason, dc.captured_at_utc,"
                        "       dc.refresh_due_at_utc, dc.lane_status"
                        " FROM deep_captures dc JOIN source_records sr ON sr.id = dc.source_record_id"
                        " ORDER BY dc.stale DESC, dc.refresh_due_at_utc, dc.source_record_id LIMIT 500"
                    )
                ]
                summary = {
                    "FULL": conn.execute("SELECT COUNT(*) FROM deep_captures WHERE completeness='FULL'").fetchone()[0],
                    "PARTIAL": conn.execute("SELECT COUNT(*) FROM deep_captures WHERE completeness='PARTIAL'").fetchone()[0],
                    "NOT_COLLECTED": conn.execute(
                        "SELECT COUNT(*) FROM deep_captures WHERE completeness='NOT_COLLECTED'"
                    ).fetchone()[0],
                    "stale": conn.execute("SELECT COUNT(*) FROM deep_captures WHERE stale=1").fetchone()[0],
                }
            except sqlite3.Error as exc:
                return {"present": True, "error": str(exc), "rows": [], "summary": {}}
        now = _now()
        for row in rows:
            due = row["refresh_due_at_utc"]
            row["needs_refresh"] = bool(row["stale"]) or row["completeness"] != "FULL" or (due is not None and due <= now)
        return {"present": True, "rows": rows, "summary": summary}

    # ------------------------------------------------------------------ UI-09 integrity

    def integrity(self) -> dict[str, Any]:
        conn = self._serving()
        if conn is None:
            return {"present": False, "raw_objects": {}, "quarantine": [], "unresolved": {}, "gaps": [], "markers": []}
        with closing(conn):
            try:
                raw = {
                    row["integrity_status"]: row["count"]
                    for row in conn.execute(
                        "SELECT integrity_status, COUNT(*) AS count FROM raw_objects GROUP BY integrity_status"
                    )
                }
                quarantine = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT failure, substr(response_object_sha, 1, 12) AS sha12, quarantined_at_utc"
                        " FROM quarantined_responses ORDER BY quarantined_at_utc DESC LIMIT 50"
                    )
                ]
                unresolved = {
                    "open": conn.execute(
                        "SELECT COUNT(*) FROM unresolved_records WHERE resolved_at_utc IS NULL"
                    ).fetchone()[0],
                    "resolved": conn.execute(
                        "SELECT COUNT(*) FROM unresolved_records WHERE resolved_at_utc IS NOT NULL"
                    ).fetchone()[0],
                }
                gaps = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT kind, COUNT(*) AS count FROM reconcile_gaps"
                        " WHERE acknowledged_at_utc IS NULL GROUP BY kind ORDER BY kind"
                    )
                ]
                markers = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT key, value_json, updated_at_utc FROM settings"
                        " WHERE key IN ('serving_stale') ORDER BY key"
                    )
                ]
            except sqlite3.Error as exc:
                return {"present": True, "error": str(exc), "raw_objects": {}, "quarantine": [],
                        "unresolved": {}, "gaps": [], "markers": []}
        return {"present": True, "raw_objects": raw, "quarantine": quarantine,
                "unresolved": unresolved, "gaps": gaps, "markers": markers,
                "quarantine_total": len(quarantine)}

    # ------------------------------------------------------------------ UI-10 settings

    def settings(self) -> dict[str, Any]:
        conn = self._serving()
        payload: dict[str, Any] = {
            "snapshot": serving_state(self.serving_path),
            "backups": self._backups(),
            "app_version": APP_VERSION,
            "data_root": str(self.data_root),
            "worker_pipe": worker_pipe_name(),
            "settings": [],
        }
        if conn is None:
            return payload
        with closing(conn):
            try:
                payload["settings"] = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT key, value_json, updated_at_utc FROM settings ORDER BY key"
                    )
                ]
            except sqlite3.Error as exc:
                payload["error"] = str(exc)
        return payload

    def _backups(self) -> list[dict[str, Any]]:
        directory = self.data_root / "backups"
        entries = []
        if directory.is_dir():
            for path in sorted(directory.glob("*")):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                entries.append({"name": path.name, "bytes": stat.st_size,
                                "modified_at": _iso(datetime.fromtimestamp(stat.st_mtime, tz=UTC))})
        return entries

    def _count_files(self, directory: Path) -> int:
        try:
            return sum(1 for _ in directory.glob("*.json"))
        except OSError:
            return 0


def _family_counts(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    families: dict[str, dict[str, Any]] = {}

    def bump(family: str, field: str, value: int) -> None:
        families.setdefault(family, {"records": 0, "versions": 0, "raw_revisions": 0,
                                     "observations": 0, "last_verified_at_utc": None})
        families[family][field] = value

    for row in conn.execute("SELECT family, COUNT(*) FROM source_records GROUP BY family"):
        bump(row[0], "records", row[1])
    for row in conn.execute(
        "SELECT sr.family, COUNT(*) FROM source_record_versions v JOIN source_records sr ON sr.id = v.source_record_id"
        " GROUP BY sr.family"
    ):
        bump(row[0], "versions", row[1])
    for row in conn.execute(
        "SELECT sr.family, COUNT(*) FROM raw_revisions r JOIN source_records sr ON sr.id = r.source_record_id"
        " GROUP BY sr.family"
    ):
        bump(row[0], "raw_revisions", row[1])
    for row in conn.execute("SELECT family, COUNT(*) FROM observations GROUP BY family"):
        bump(row[0], "observations", row[1])
    for row in conn.execute(
        "SELECT family, MAX(verified_at_utc) FROM coverage_proofs GROUP BY family"
    ):
        families.setdefault(row[0], {"records": 0, "versions": 0, "raw_revisions": 0,
                                     "observations": 0, "last_verified_at_utc": None})
        families[row[0]]["last_verified_at_utc"] = row[1]
    return families


def _fts_query(text: str) -> str:
    """Folded prefix-AND query; FTS5 syntax never receives raw user text."""
    tokens = re.findall(r"[0-9a-z]+", fold_vietnamese(text).lower())
    return " AND ".join(f"{token}*" for token in tokens)
