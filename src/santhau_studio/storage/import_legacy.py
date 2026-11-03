"""Idempotent legacy ingest, retaining source history without manufacturing modern proof.

Legacy channels are namespaced as legacy:<original source>. This preserves the originating
channel while preventing an old archive from replacing a newer live collector's current state.
Original versions and observations remain separate; importing a version does not invent an
observation. Every committed event carries an atomic receipt in run_tasks.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from santhau_studio.collectors.source_registry import binding_for
from santhau_studio.domain.identity import (
    canonical_bytes,
    content_hash,
    identity_key,
)
from santhau_studio.domain.records import NormalizedRecord
from santhau_studio.domain.redaction import redact_payload
from santhau_studio.storage.legacy_reader import LegacyReader, inspect_legacy
from santhau_studio.storage.warehouse import WarehouseWriter

__all__ = ["import_legacy", "inspect_legacy"]


def _utc(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("legacy timestamp has no timezone")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace(".000000", "").replace("+00:00", "Z")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _setting(conn, key: str, value: dict) -> None:
    conn.execute("INSERT OR REPLACE INTO settings VALUES(?,?,?)", (key, _json(value), _now()))


def _event(writer: WarehouseWriter, event) -> dict:
    metadata = json.loads(event["metadata"])
    if event["error"]:
        raise ValueError(event["error"])
    envelope = json.loads(event["payload"])
    payload = envelope["raw_payload"]
    source = str(metadata["source"])
    family = str(metadata["record_type"])
    spec = binding_for(family).spec
    state, raw_version = spec.version_of(payload)
    natural = spec.natural_key_of(payload)
    normalized = redact_payload(envelope.get("normalized") or spec.normalize(payload))
    stamp = _utc(str(metadata.get("fetched_at") or envelope["fetched_at"]))
    expected = metadata.get("raw_hash")
    # Legacy canonicalization uses the same JSON shape but not NFC folding. Check the original
    # redacted archive value before translating to the new canonical representation.
    legacy_hash = event["legacy_raw_hash"]
    if expected and expected != legacy_hash:
        raise ValueError("legacy raw_hash does not match the referenced record")
    event_digest = hashlib.sha256(canonical_bytes([metadata, envelope])).hexdigest()
    run_id = "legacy-" + event["event_key"]
    existing = writer.conn.execute("SELECT detail_json FROM run_tasks WHERE run_id=? AND task_kind='IMPORT'", (run_id,)).fetchone()
    if existing:
        receipt = json.loads(existing[0])
        if receipt["event_digest"] != event_digest:
            raise ValueError("legacy event identity changed its content; import refused")
        return {**receipt, "duplicate": True}

    writer.source_key = "legacy:" + source
    response = writer.store_response(envelope)
    raw = writer.store.put(canonical_bytes(payload))
    conn = writer.conn
    conn.execute("BEGIN IMMEDIATE")
    try:
        writer.register_run(run_id, run_id, "IMPORT", requested_by="legacy-import")
        conn.execute("INSERT OR IGNORE INTO source_registry(source_key,display_name,endpoint_family,access_state,notes) VALUES(?,?,?,'DISABLED',?)",
                     (writer.source_key, f"Legacy · {source}", family, "Imported archive; not a live access grant"))
        attempt = writer.ensure_attempt(run_id=run_id, family=family, endpoint="legacy://archive/" + event["kind"],
            interval_from=stamp, interval_to=_utc((datetime.fromisoformat(stamp.replace("Z", "+00:00")) + timedelta(seconds=1)).isoformat()),
            page_no=0, attempt_no=1, outcome="OK", response=response, http_status=None)
        writer.register_object(raw)
        conn.execute("INSERT OR IGNORE INTO object_derivation(response_object_sha,record_object_sha,record_locator,redaction_policy_version,derived_at_utc) VALUES(?,?,'/raw_payload',?,?)",
                     (response.sha256, raw.sha256, writer.store.redaction_policy_version, _now()))
        idkey = identity_key(family, natural, state, raw_version) if natural else None
        previous = conn.execute("SELECT * FROM source_records WHERE source_key=? AND family=? AND identity_key=?",
                                (writer.source_key, family, idkey)).fetchone() if idkey else None
        max_version = conn.execute("SELECT COALESCE(MAX(id),0) FROM source_record_versions").fetchone()[0]
        max_revision = conn.execute("SELECT COALESCE(MAX(id),0) FROM raw_revisions").fetchone()[0]
        if event["kind"] == "version" or previous is None:
            if event["kind"] == "version" and natural is None:
                raise ValueError("legacy version has no recoverable natural identity")
            _, observation_id = writer._ingest_record(
                run_id=run_id, attempt_id=attempt, response_sha=response.sha256, record_sha=raw.sha256,
                locator="/raw_payload", normalized=NormalizedRecord(family, natural, state, raw_version, normalized))
            if event["kind"] == "version":
                conn.execute("DELETE FROM observations WHERE id=?", (observation_id,))
                observation_id = None
        else:
            status = metadata.get("status", "UNCHANGED")
            if status not in ("NEW", "UPDATED", "UPDATED_RAW", "UNCHANGED", "DUPLICATE_IN_RUN", "UNRESOLVED"):
                status = "UNCHANGED"
            cursor = conn.execute("INSERT INTO observations(run_id,attempt_id,observation_key,family,response_object_sha,record_object_sha,record_locator,content_hash,status,observed_at_utc) VALUES(?,?,?,?,?,?,'/raw_payload',?,?,?)",
                (run_id, attempt, "SR:" + idkey, family, response.sha256, raw.sha256, content_hash(normalized), status, stamp))
            observation_id = cursor.lastrowid
        record = conn.execute("SELECT id FROM source_records WHERE source_key=? AND family=? AND identity_key=?",
                              (writer.source_key, family, idkey)).fetchone() if idkey else None
        if observation_id:
            conn.execute("UPDATE observations SET observed_at_utc=? WHERE id=?", (stamp, observation_id))
        conn.execute("UPDATE source_record_versions SET fetched_at_utc=? WHERE id>?", (stamp, max_version))
        conn.execute("UPDATE raw_revisions SET first_observed_at_utc=? WHERE id>?", (stamp, max_revision))
        if record:
            first = min(previous["first_seen_at_utc"], stamp) if previous else stamp
            last = max(previous["last_seen_at_utc"], stamp) if previous else stamp
            conn.execute("UPDATE source_records SET first_seen_at_utc=?,last_seen_at_utc=? WHERE id=?", (first, last, record[0]))
            conn.execute("INSERT OR IGNORE INTO source_aliases VALUES(?,?,?,?,?,?)",
                (writer.source_key, family, "legacy_source_record_id", str(metadata["source_record_id"]), record[0],
                 _json({"origin_source": source, "legacy_record_id": metadata["source_record_id"], "response_sha": response.sha256})))
        receipt = {"event_key": event["event_key"], "event_digest": event_digest, "kind": event["kind"],
                   "origin_source": source, "source_record_id": record[0] if record else None,
                   "observation_id": observation_id, "response_sha": response.sha256, "record_sha": raw.sha256,
                   "legacy_raw_sha256": legacy_hash, "legacy": metadata}
        writer.finish_run(run_id, "COMPLETED")
        conn.execute("INSERT INTO run_tasks(run_id,task_kind,status,detail_json) VALUES(?,'IMPORT','COMPLETED',?)", (run_id, _json(receipt)))
        conn.execute("COMMIT")
        return receipt
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def import_legacy(source: Path, target: Path, *, progress: Callable[[dict], None] | None = None,
                  stop: Callable[[], bool] | None = None) -> dict:
    info = inspect_legacy(Path(source))
    source = Path(info["root"])
    target = Path(target).resolve()
    if target == source or target.is_relative_to(source):
        raise ValueError("Data Root must be outside the source archive")
    with closing(WarehouseWriter.open(target, create_schema=True, role="legacy-import", raw_layout="segments")) as writer:
        directory = target / "imports"
        directory.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="legacy-", dir=directory) as temporary:
            reader = LegacyReader(source, Path(temporary))
            try:
                key = reader.key
                receipt_path = directory / f"{key}.json"
                existing = writer.conn.execute("SELECT state FROM import_batches WHERE batch_key=?", (key,)).fetchone()
                if existing and existing[0] == "IMPORTED" and receipt_path.exists():
                    saved = json.loads(receipt_path.read_text(encoding="utf-8"))
                    return {**saved, "unchanged": True,
                            "status": "ALREADY_IMPORTED" if saved["status"] == "IMPORTED" else saved["status"]}
                writer.conn.execute("INSERT OR IGNORE INTO import_batches(batch_key,source_path,manifest_sha,rows_seen,rows_imported,rows_rejected,state) VALUES(?,?,?,0,0,0,'PENDING')",
                                    (key, str(source), key))
                imported = duplicates = rejected = seen = 0
                errors = []
                interrupted = False
                for event in reader.events():
                    if stop and stop():
                        interrupted = True
                        break
                    seen += 1
                    try:
                        receipt = _event(writer, event)
                        duplicates += bool(receipt.get("duplicate"))
                        imported += not receipt.get("duplicate")
                    except (ValueError, KeyError, TypeError) as exc:
                        rejected += 1
                        if len(errors) < 50:
                            errors.append({"event": event["event_key"], "reason": str(exc)})
                    if progress:
                        progress({"seen": seen, "imported": imported, "duplicates": duplicates, "rejected": rejected})
                for row in reader.catalog("coverage_windows"):
                    classification = "LEGACY_COMPLETE_UNVERIFIED" if row.get("status") in ("COMPLETE", "COMPLETED") else "LEGACY_" + str(row.get("status", "UNKNOWN"))
                    _setting(writer.conn, "legacy:coverage:" + hashlib.sha256(canonical_bytes(row)).hexdigest(),
                             {"classification": classification, "claim": row, "modern_proof": False, "batch": key})
                archive_path = directory / f"{key}.catalog.jsonl.gz"
                catalog_shas = []
                block = []
                with gzip.open(archive_path, "wt", encoding="utf-8") as archive:
                    for row in reader.cache.execute("SELECT kind,payload FROM catalog"):
                        line = _json({"table": row[0], "row": json.loads(row[1])}) + "\n"
                        archive.write(line)
                        block.append(line)
                        if len(block) >= 1000:
                            ref = writer.store.put("".join(block).encode("utf-8"))
                            writer.register_object(ref)
                            catalog_shas.append(ref.sha256)
                            block.clear()
                if block:
                    ref = writer.store.put("".join(block).encode("utf-8"))
                    writer.register_object(ref)
                    catalog_shas.append(ref.sha256)
                _setting(writer.conn, "legacy:catalog:" + key, {"chunks": catalog_shas, "format": "jsonl", "schema": 1})
                status = "INTERRUPTED" if interrupted else ("PARTIAL" if rejected else "IMPORTED")
                summary = {"status": status, "batch": key, "source": str(source), "seen": seen,
                    "imported": imported, "duplicates": duplicates, "rejected": rejected, "errors": errors,
                    "receipt": str(receipt_path), "catalog": str(archive_path), "legacy_proof_promoted": False}
                partial = receipt_path.with_suffix(".tmp")
                with partial.open("w", encoding="utf-8") as handle:
                    handle.write(_json(summary))
                    handle.flush()
                    os.fsync(handle.fileno())
                partial.replace(receipt_path)
                writer.conn.execute("UPDATE import_batches SET state=?,rows_seen=?,rows_imported=?,rows_rejected=?,imported_at_utc=? WHERE batch_key=?",
                    ("VALIDATED" if interrupted else "IMPORTED", seen, imported + duplicates, rejected, _now(), key))
                return summary
            finally:
                reader.close()
