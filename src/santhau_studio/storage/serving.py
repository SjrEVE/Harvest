"""Serving snapshot: read model built from the warehouse, published atomically (§5.5).

The GUI reads this, never the warehouse, so the single writer is never contended and a
half-built snapshot can never be observed. Publish is build-to-temp → integrity check →
row-count reconciliation → os.replace. A snapshot that fails any gate leaves the previous
good one serving, marked STALE with a reason the UI can show (§5.5 "không publish dữ liệu
suy giảm như thể đầy đủ").
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from santhau_studio.domain.text import fold_vietnamese
from santhau_studio.storage.migrations import MIGRATIONS

try:
    from santhau_studio.workers import ai_control
except (ImportError, OSError):  # Linux: workers.ipc is Windows-only; observation is optional
    ai_control = None


def _ai(phase: str, **fields) -> None:
    """Best-effort observation. A broken AI lane must never break a publish."""
    if ai_control is None:
        return
    with contextlib.suppress(Exception):  # observation is strictly non-critical
        ai_control.emit(phase, **fields)

FTS_BATCH = 2000
PROJECTION_VERSION = 2

# RC3-8: audit/provenance tables (observations, object_derivation, raw_revisions,
# raw_objects) stay warehouse-only. The desktop reads them read-only via
# desktop.warehouse_reader; copying them here duplicated ~700-800 MB per snapshot.
SNAPSHOT_TABLES = (
    "source_records", "source_record_versions",
    "coverage_intervals", "coverage_proofs", "notice_groups", "notice_group_members",
    "canonical_entities", "entity_source_links", "unresolved_records", "quarantined_responses",
    "deep_captures", "deep_tasks", "deep_capture_history", "deep_capture_parse_metadata",
    "deep_task_metadata", "page_receipts", "watermarks", "reconcile_gaps", "money_amounts",
    "lots", "goods_rows", "contractor_memberships", "documents", "filter_boundary_rules",
    "source_registry", "settings",
)

# Named separately so the secret gate sees a tokenizer identifier rather than an
# assignment-shaped literal inside a multi-line SQL string.
FTS_TOKENIZER = "unicode61 remove_diacritics 2"  # secret-scan:allow FTS5 tokenizer name

FTS_DDL = f"""
CREATE VIRTUAL TABLE search_index USING fts5(
    record_id UNINDEXED,
    family,
    natural_key,
    title,
    investor_name,
    body,
    tokenize = '{FTS_TOKENIZER}'
);
"""


class SnapshotRejected(RuntimeError):
    """The candidate snapshot failed a gate; the previous good one keeps serving."""


@dataclass(frozen=True)
class PublishResult:
    published: bool
    snapshot_rows: dict[str, int]
    reason: str | None = None
    stale: bool = False
    degraded: bool = False


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_serving_db(warehouse: Path, target: Path) -> PublishResult:
    """Publish a verified snapshot, reporting the rebuild to the AI control lane.

    The observation wraps the real implementation in try/finally so the lane sees
    the end of a rebuild even when a gate rejects the candidate.
    """
    _ai("PUBLISHING_SNAPSHOT", serving={"rebuilding": True})
    try:
        return _build_serving_db_impl(warehouse, target)
    finally:
        _ai("IDLE", serving={"rebuilding": False})


def _build_serving_db_impl(warehouse: Path, target: Path) -> PublishResult:
    """Build a candidate snapshot at `target.<pid>.tmp`, verify, then atomically swap.

    `target` is only replaced when every gate passes. Callers can therefore treat the
    existing serving file as always-readable.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(warehouse.resolve().as_uri() + "?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    source.execute("BEGIN")  # every table and reconciliation count sees the same snapshot
    candidate = target.with_suffix(target.suffix + ".tmp")
    if candidate.exists():
        candidate.unlink()

    dest = sqlite3.connect(candidate)
    dest.row_factory = sqlite3.Row
    try:
        dest.execute("PRAGMA journal_mode=WAL")
        _copy_schema_and_rows(source, dest)
        _build_search_index(dest)
        from santhau_studio.storage.read_model import materialise_records

        materialise_records(dest, warehouse.parent)
        _build_market_relationships(dest)
        # Optional audit counts come from the warehouse. The family-stats step keeps its
        # 1-arg monkeypatch seam, so the source connection rides on the dest connection.
        _source_of[id(dest)] = source
        try:
            _materialise_family_stats(dest)
            _materialise_coverage(dest)
        finally:
            _source_of.pop(id(dest), None)
        built_at = _now()
        from santhau_studio.storage.catalog_read_model import materialise_catalog

        try:
            catalog_build = materialise_catalog(
                dest,
                snapshot_built_at_utc=built_at,
                source_projection_version=PROJECTION_VERSION,
            )
        except (sqlite3.Error, ValueError) as exc:
            raise SnapshotRejected(f"catalog read model rejected: {exc}") from exc
        dest.execute(
            "CREATE TABLE IF NOT EXISTS serving_meta ("
            " id INTEGER PRIMARY KEY, snapshot_built_at_utc TEXT NOT NULL,"
            " source_warehouse_sha TEXT NOT NULL, row_counts_json TEXT NOT NULL,"
            " stale INTEGER NOT NULL DEFAULT 0 CHECK (stale IN (0,1)), stale_reason TEXT)"
        )
        counts = {t: _count(dest, t) for t in SNAPSHOT_TABLES if _exists(dest, t)}
        source_counts = {t: _count(source, t) for t in counts}
        if counts != source_counts:
            raise SnapshotRejected(f"row count drift: {[(k, counts[k], source_counts[k]) for k in counts if counts[k] != source_counts[k]][:5]}")
        digest = _content_digest(source)
        if digest != _content_digest(dest):
            raise SnapshotRejected("content digest drift between warehouse and candidate")
        integrity = dest.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SnapshotRejected(f"integrity_check returned {integrity}")
        quick = dest.execute("PRAGMA quick_check").fetchone()[0]
        if quick != "ok":
            raise SnapshotRejected(f"quick_check returned {quick}")
        # §5.5: degraded data may be published, but never *as if it were complete*. The marker
        # goes in serving_meta.stale/stale_reason — the frozen G0-B DDL owns that table, so the
        # signal is carried as structured JSON there rather than by adding columns. `stale=1`
        # tells the UI "do not present this as current-and-complete"; degraded_reason says why.
        degradation = _degradation(source)
        dest.execute(
            "INSERT INTO serving_meta (snapshot_built_at_utc, source_warehouse_sha, row_counts_json,"
            " stale, stale_reason) VALUES (?,?,?,?,?)",
            (built_at, _warehouse_sha(warehouse), json.dumps(
                {**counts, "content_digest": digest, "derived_catalog": catalog_build.metadata}, sort_keys=True),
             1 if degradation else 0,
             json.dumps({"kind": "DEGRADED", "counts": degradation}, sort_keys=True) if degradation else None),
        )
        # serving_meta stays frozen for v1 readers; projection identity is additive.
        dest.execute(
            "CREATE TABLE serving_projection_meta ("
            " id INTEGER PRIMARY KEY CHECK (id = 1),"
            " projection_version INTEGER NOT NULL CHECK (projection_version >= 1),"
            " snapshot_built_at_utc TEXT NOT NULL)"
        )
        dest.execute(
            "INSERT INTO serving_projection_meta (id, projection_version, snapshot_built_at_utc) VALUES (1,?,?)",
            (PROJECTION_VERSION, built_at),
        )
        dest.commit()
    except SnapshotRejected as exc:
        dest.close()
        candidate.unlink(missing_ok=True)
        _mark_stale(warehouse, str(exc))
        mark_stale(target, str(exc))
        return PublishResult(published=False, snapshot_rows={}, reason=str(exc), stale=True)
    except (sqlite3.Error, OSError) as exc:
        dest.close()
        candidate.unlink(missing_ok=True)
        mark_stale(target, str(exc))
        return PublishResult(published=False, snapshot_rows={}, reason=f"sqlite error: {exc}", stale=True)
    else:
        dest.close()
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Windows readers may briefly keep the previous file open. Retry only
            # the atomic rename, not the expensive rebuild; permanent failures
            # still preserve the old snapshot and report STALE below.
            for attempt in range(6):
                try:
                    candidate.replace(target)
                    break
                except PermissionError:
                    if attempt == 5:
                        raise
                    time.sleep(.05 * 2**attempt)
        except OSError as exc:
            candidate.unlink(missing_ok=True)
            mark_stale(target, str(exc))
            return PublishResult(False, {}, reason=str(exc), stale=True)
        target.with_suffix(".stale.json").unlink(missing_ok=True)
        return PublishResult(published=True, snapshot_rows=counts, degraded=bool(degradation))
    finally:
        source.close()


def _copy_schema_and_rows(source: sqlite3.Connection, dest: sqlite3.Connection) -> None:
    """Copy exactly SNAPSHOT_TABLES (schema + rows + indexes) from the warehouse.

    A whole-file backup() would drag the audit tables along; RC3-8 copies table by
    table inside the source's open read transaction so every table still sees one
    consistent snapshot. The snapshot is a projection: FK enforcement stays off and
    referential truth lives in the warehouse; this copy is validated by row-count
    equality, content digest and integrity_check.
    """
    dest.execute("PRAGMA foreign_keys=OFF")
    for table in SNAPSHOT_TABLES:
        if not _exists(source, table):
            continue
        columns = [r[1] for r in source.execute(f"PRAGMA table_info('{table}')")]
        ddl = source.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        dest.execute(ddl)
        placeholders = ",".join("?" * len(columns))
        quoted = ",".join(f'"{c}"' for c in columns)
        cursor = source.execute(f"SELECT {quoted} FROM '{table}'")
        while rows := cursor.fetchmany(2000):
            dest.executemany(
                f"INSERT INTO '{table}' ({quoted}) VALUES ({placeholders})",
                [tuple(r) for r in rows],
            )
        for index in source.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
            " AND tbl_name=?", (table,)
        ):
            dest.execute(index[0])
    # The snapshot is a *projection*: DDL still carries REFERENCES clauses pointing at
    # tables that deliberately stay warehouse-only (raw_objects, fetch_attempts,
    # fetch_requests). Enforcing FKs here would require copying those tables back,
    # defeating RC3-8. Referential truth stays in the warehouse; the snapshot is
    # validated by row-count equality and integrity_check instead. Keep enforcement
    # off so INSERT order never matters.
    dest.execute("PRAGMA foreign_keys=OFF")


_source_of: dict[int, sqlite3.Connection] = {}


def _materialise_family_stats(dest: sqlite3.Connection, source: sqlite3.Connection | None = None) -> None:
    """Family counters computed once per publish; the dashboard becomes a table read.

    The snapshot is rebuilt wholesale on every publish, so this is the invalidation mechanism:
    a number in this table is exactly as fresh as the snapshot that carries it. Audit counters
    (raw_revisions, observations) are read from the warehouse via `_source_of`/`source`;
    the 1-arg monkeypatch seam of fault-injection tests stays intact.
    """
    source = source if source is not None else _source_of.get(id(dest))
    dest.execute(
        "CREATE TABLE IF NOT EXISTS serving_family_stats ("
        " family TEXT PRIMARY KEY, records INTEGER NOT NULL, versions INTEGER NOT NULL,"
        " raw_revisions INTEGER NOT NULL, observations INTEGER NOT NULL,"
        " last_verified_at_utc TEXT)"
    )
    dest.execute("DELETE FROM serving_family_stats")
    if source is None:  # tests may call this directly against a dest-only fixture
        dest.execute(
            "INSERT INTO serving_family_stats (family, records, versions, raw_revisions, observations,"
            " last_verified_at_utc)"
            " SELECT families.family,"
            "  (SELECT COUNT(*) FROM source_records r WHERE r.family = families.family),"
            "  (SELECT COUNT(*) FROM source_record_versions v JOIN source_records sr ON sr.id = v.source_record_id"
            "     WHERE sr.family = families.family),"
            "  0,"
            "  0,"
            "  (SELECT MAX(cp.verified_at_utc) FROM coverage_proofs cp WHERE cp.family = families.family)"
            " FROM ("
            "   SELECT family FROM source_records"
            "   UNION SELECT family FROM coverage_proofs"
            " ) AS families ORDER BY families.family"
        )
        return
    dest.execute(
        "ATTACH ? AS wh_audit", (str(source.execute("PRAGMA database_list").fetchone()[2]),)
    ) if False else None  # ATTACH would lock; instead read counts in Python below
    audit = {}
    for family, revisions, observations in source.execute(
        " SELECT sr.family,"
        "   (SELECT COUNT(*) FROM raw_revisions rr JOIN source_records s2 ON s2.id = rr.source_record_id"
        "      WHERE s2.family = sr.family),"
        "   (SELECT COUNT(*) FROM observations o WHERE o.family = sr.family)"
        " FROM source_records sr GROUP BY sr.family"
    ):
        audit[family] = (revisions, observations)
    # An empty warehouse legitimately yields an empty mapping here; the row-count
    # equality gate below is what catches a broken copy.
    dest.execute(
        "INSERT INTO serving_family_stats (family, records, versions, raw_revisions, observations,"
        " last_verified_at_utc)"
        " SELECT families.family,"
        "  (SELECT COUNT(*) FROM source_records r WHERE r.family = families.family),"
        "  (SELECT COUNT(*) FROM source_record_versions v JOIN source_records sr ON sr.id = v.source_record_id"
        "     WHERE sr.family = families.family),"
        "  0,"
        "  0,"
        "  (SELECT MAX(cp.verified_at_utc) FROM coverage_proofs cp WHERE cp.family = families.family)"
        " FROM ("
        "   SELECT family FROM source_records"
        "   UNION SELECT family FROM coverage_proofs"
        " ) AS families ORDER BY families.family"
    )
    for family, (revisions, observations) in audit.items():
        dest.execute(
            "UPDATE serving_family_stats SET raw_revisions=?, observations=? WHERE family=?",
            (revisions, observations, family),
        )


# The resolver's ResolutionStatus mapped to the spec's 4-state serving vocabulary. NEEDS_RECONCILE
# is STALE (data moved under its proof); an unmeasured/unsupported span is UNKNOWN, never COMPLETE.
_COVERAGE_STATE = {
    "COMPLETE": "COMPLETE",
    "PARTIAL": "PARTIAL",
    "NEEDS_RECONCILE": "STALE",
    "BLOCKED": "PARTIAL",
    "NOT_ACCESSED": "UNKNOWN",
    "UNSUPPORTED": "UNKNOWN",
}


def _materialise_coverage(dest: sqlite3.Connection, source: sqlite3.Connection | None = None) -> None:
    """Measured coverage windows resolved OFFLINE through the §4.5 resolver, shipped prepared.

    The bridge reads this projection instead of importing the producer or re-resolving per request
    (a per-resolution drift scan is a UI-hot-path cost). Status comes from coverage.resolve — proof
    validity, query-contract staleness and unacked drift — never the raw coverage_intervals.status.
    `records` counts held latest records dated in the window (published_epoch, the same date field
    the collector filters on): observed volume, never interpolated. Only measured spans are emitted;
    the gaps between them are not invented. Runs on the warehouse source connection via the same
    _source_of seam as family stats; its tables are copied to serving too, but resolution stays a
    build-time concern. Derived tables stay out of SNAPSHOT_TABLES, so the digest gate exempts them.
    """
    source = source if source is not None else _source_of.get(id(dest))
    dest.execute("DROP TABLE IF EXISTS serving_coverage_windows")
    dest.execute("DROP TABLE IF EXISTS serving_coverage_state")
    dest.execute(
        "CREATE TABLE serving_coverage_windows ("
        " source_key TEXT NOT NULL, family TEXT NOT NULL,"
        " interval_from_utc TEXT NOT NULL, interval_to_utc TEXT NOT NULL,"
        " granularity TEXT, status TEXT NOT NULL, records INTEGER NOT NULL, proven_fraction REAL,"
        " PRIMARY KEY (source_key, family, interval_from_utc, interval_to_utc))"
    )
    dest.execute(
        "CREATE TABLE serving_coverage_state ("
        " source_key TEXT NOT NULL, family TEXT NOT NULL, coverage_state TEXT NOT NULL,"
        " measured_from_utc TEXT, measured_to_utc TEXT, PRIMARY KEY (source_key, family))"
    )
    if source is None:  # tests may call this directly against a dest-only fixture
        return
    from santhau_studio.storage import coverage as coverage_store
    from santhau_studio.storage.coverage_contract import current

    pairs = source.execute(
        "SELECT DISTINCT source_key, family FROM coverage_intervals ORDER BY source_key, family"
    ).fetchall()
    for pair in pairs:
        source_key, family = pair["source_key"], pair["family"]
        context = current(source, source_key, family)
        spans = source.execute(
            "SELECT interval_from_utc, interval_to_utc FROM coverage_intervals"
            " WHERE source_key=? AND family=? GROUP BY interval_from_utc, interval_to_utc"
            " ORDER BY interval_from_utc",
            (source_key, family),
        ).fetchall()
        for span in spans:
            lo = coverage_store._parse(span["interval_from_utc"])
            hi = coverage_store._parse(span["interval_to_utc"])
            resolution = coverage_store.resolve(source, source_key, family, lo, hi, context=context)
            records = dest.execute(
                "SELECT COUNT(*) FROM record_fields WHERE source_key=? AND family=? AND is_latest=1"
                " AND published_epoch>=? AND published_epoch<?",
                (source_key, family, lo.timestamp(), hi.timestamp()),
            ).fetchone()[0]
            dest.execute(
                "INSERT INTO serving_coverage_windows (source_key, family, interval_from_utc, interval_to_utc,"
                " granularity, status, records, proven_fraction) VALUES (?,?,?,?,?,?,?,?)",
                (source_key, family, span["interval_from_utc"], span["interval_to_utc"],
                 resolution.granularity, _COVERAGE_STATE[resolution.status], records,
                 resolution.proven_fraction),
            )
        bounds = source.execute(
            "SELECT MIN(interval_from_utc) AS lo, MAX(interval_to_utc) AS hi FROM coverage_intervals"
            " WHERE source_key=? AND family=?",
            (source_key, family),
        ).fetchone()
        rollup = coverage_store.resolve(
            source, source_key, family,
            coverage_store._parse(bounds["lo"]), coverage_store._parse(bounds["hi"]), context=context,
        )
        dest.execute(
            "INSERT INTO serving_coverage_state (source_key, family, coverage_state, measured_from_utc,"
            " measured_to_utc) VALUES (?,?,?,?,?)",
            (source_key, family, _COVERAGE_STATE[rollup.status], bounds["lo"], bounds["hi"]),
        )


def _build_market_relationships(dest: sqlite3.Connection) -> None:
    """Code/key-based serving edges; names are display evidence, never identity keys."""
    dest.execute("DROP TABLE IF EXISTS market_relation_edges")
    dest.execute(
        "CREATE TABLE market_relation_edges ("
        " id INTEGER PRIMARY KEY, source_key TEXT NOT NULL,"
        " from_record_id INTEGER NOT NULL, to_record_id INTEGER, source_record_id INTEGER NOT NULL,"
        " relation TEXT NOT NULL, basis TEXT NOT NULL, join_key TEXT NOT NULL, party_tax_id TEXT,"
        " content_hash TEXT, raw_hash TEXT, provenance_json TEXT NOT NULL,"
        " CHECK (to_record_id IS NOT NULL OR party_tax_id IS NOT NULL))"
    )
    dest.execute(
        "INSERT OR IGNORE INTO market_relation_edges"
        " (source_key,from_record_id,to_record_id,source_record_id,relation,basis,join_key,party_tax_id,content_hash,raw_hash,provenance_json)"
        " SELECT child.source_key,plan.record_id,child.record_id,child.record_id,"
        "  CASE child.family WHEN 'TBMT' THEN 'PLAN_HAS_NOTICE' WHEN 'KQLCNT' THEN 'PLAN_HAS_RESULT' ELSE 'PLAN_HAS_CONTRACT' END,"
        "  'same_planNo',child.plan_no,NULL,cr.current_content_hash,cr.current_record_object_sha,"
        "  json_object('basis','same_planNo','planNo',child.plan_no,'source_record_id',child.record_id,"
        "   'content_hash',cr.current_content_hash,'raw_hash',cr.current_record_object_sha)"
        " FROM record_fields plan"
        " JOIN record_fields child ON child.source_key=plan.source_key AND child.family IN ('TBMT','KQLCNT','HOP_DONG')"
        " JOIN source_records cr ON cr.id=child.record_id"
        " WHERE plan.family='KHLCNT' AND child.plan_no IS NOT NULL AND child.plan_no=plan.natural_key"
    )
    for target_family, relation in (("KQLCNT", "NOTICE_HAS_RESULT"), ("HOP_DONG", "NOTICE_HAS_CONTRACT")):
        dest.execute(
            "INSERT OR IGNORE INTO market_relation_edges"
            " (source_key,from_record_id,to_record_id,source_record_id,relation,basis,join_key,party_tax_id,content_hash,raw_hash,provenance_json)"
            " SELECT child.source_key,notice.record_id,child.record_id,child.record_id,?,'same_notifyNo_version',"
            "  child.notify_no || ':' || child.notify_version,"
            "  NULL,cr.current_content_hash,cr.current_record_object_sha,"
            "  json_object('basis','same_notifyNo_version','notifyNo',child.notify_no,"
            "   'notifyVersion',child.notify_version,'source_record_id',child.record_id,"
            "   'content_hash',cr.current_content_hash,'raw_hash',cr.current_record_object_sha)"
            " FROM record_fields child"
            " JOIN source_records cr ON cr.id=child.record_id"
            " JOIN record_fields notice ON notice.source_key=child.source_key AND notice.family='TBMT'"
            "  AND notice.natural_key=child.notify_no AND notice.version_raw=child.notify_version"
            " WHERE child.family=? AND child.notify_no IS NOT NULL AND child.notify_version IS NOT NULL",
            (relation, target_family),
        )
    dest.execute(
        "INSERT OR IGNORE INTO market_relation_edges"
        " (source_key,from_record_id,to_record_id,source_record_id,relation,basis,join_key,party_tax_id,content_hash,raw_hash,provenance_json)"
        " SELECT contract.source_key,result.record_id,contract.record_id,contract.record_id,'RESULT_HAS_CONTRACT','same_notifyNo_version',"
        "  contract.notify_no || ':' || contract.notify_version,"
        "  NULL,cr.current_content_hash,cr.current_record_object_sha,"
        "  json_object('basis','same_notifyNo_version','notifyNo',contract.notify_no,"
        "   'notifyVersion',contract.notify_version,'source_record_id',contract.record_id,"
        "   'content_hash',cr.current_content_hash,'raw_hash',cr.current_record_object_sha)"
        " FROM record_fields contract"
        " JOIN source_records cr ON cr.id=contract.record_id"
        " JOIN record_fields result ON result.source_key=contract.source_key AND result.family='KQLCNT'"
        "  AND result.natural_key=contract.notify_no AND result.version_raw=contract.notify_version"
        " WHERE contract.family='HOP_DONG' AND contract.notify_no IS NOT NULL AND contract.notify_version IS NOT NULL"
    )
    dest.execute(
        "INSERT OR IGNORE INTO market_relation_edges"
        " (source_key,from_record_id,to_record_id,source_record_id,relation,basis,join_key,party_tax_id,content_hash,raw_hash,provenance_json)"
        " SELECT f.source_key,f.record_id,NULL,f.record_id,'EXECUTED_BY','winningCode',je.value,je.value,"
        "  r.current_content_hash,r.current_record_object_sha,"
        "  json_object('basis','winningCode','party_tax_id',je.value,'source_record_id',f.record_id,"
        "   'content_hash',r.current_content_hash,'raw_hash',r.current_record_object_sha)"
        " FROM record_fields f JOIN source_records r ON r.id=f.record_id, json_each(f.winning_codes_json) je"
        " WHERE f.winning_codes_json IS NOT NULL AND json_valid(f.winning_codes_json) AND je.type='text'"
    )
    if _exists(dest, "contractor_memberships"):
        dest.execute(
            "INSERT OR IGNORE INTO market_relation_edges"
            " (source_key,from_record_id,to_record_id,source_record_id,relation,basis,join_key,party_tax_id,content_hash,raw_hash,provenance_json)"
            " SELECT r.source_key,m.source_record_id,NULL,m.source_record_id,"
            "  CASE WHEN m.role='WINNER' THEN 'EXECUTED_BY' ELSE 'OFFERED_BY' END,'deep_contractorCode',m.party_tax_id,m.party_tax_id,"
            "  r.current_content_hash,r.current_record_object_sha,"
            "  json_object('basis','deep_contractorCode','membership_id',m.id,'party_tax_id',m.party_tax_id,"
            "   'source_record_id',m.source_record_id,'content_hash',r.current_content_hash,'raw_hash',r.current_record_object_sha,"
            "   'membership_provenance_json',m.provenance_json)"
            " FROM contractor_memberships m JOIN source_records r ON r.id=m.source_record_id"
            " WHERE m.party_tax_id_presence='KNOWN' AND m.party_tax_id IS NOT NULL"
        )
    dest.execute(
        "CREATE UNIQUE INDEX ix_mre_identity ON market_relation_edges"
        " (from_record_id,COALESCE(to_record_id,-1),source_record_id,relation,basis,join_key,COALESCE(party_tax_id,''))"
    )
    for column in ("relation", "basis", "party_tax_id", "to_record_id", "source_record_id"):
        dest.execute(f"CREATE INDEX ix_mre_{column} ON market_relation_edges({column})")
    dest.execute("ANALYZE market_relation_edges")


def _build_search_index(dest: sqlite3.Connection) -> None:
    """FTS5 over the fields UI-06 searches, holding folded ASCII (see domain.text).

    Folding happens here rather than in SQL because SQLite has no Vietnamese folding, and
    `remove_diacritics 2` alone leaves "đ" unfolded. Rows stream in batches so a million
    records do not materialise a million Python tuples.
    """
    if not _exists(dest, "search_index"):
        dest.execute(FTS_DDL)
    if not _exists(dest, "source_records"):
        return
    dest.execute("DELETE FROM search_index")
    cursor = dest.execute(
        " SELECT sr.id, sr.family, sr.natural_key,"
        "        COALESCE(json_extract(v.normalized_json, '$.title'), ''),"
        "        COALESCE(json_extract(v.normalized_json, '$.investorName'), ''),"
        "        COALESCE(v.normalized_json, '')"
        " FROM source_records sr"
        " LEFT JOIN source_record_versions v ON v.source_record_id=sr.id"
        " AND v.content_version_seq=sr.current_content_version"
    )
    while True:
        rows = cursor.fetchmany(FTS_BATCH)
        if not rows:
            break
        dest.executemany(
            "INSERT INTO search_index (rowid, record_id, family, natural_key, title, investor_name, body) VALUES (?,?,?,?,?,?,?)",
            [
                (record_id, record_id, fold_vietnamese(family), fold_vietnamese(natural_key),
                 fold_vietnamese(title), fold_vietnamese(investor), fold_vietnamese(body))
                for record_id, family, natural_key, title, investor, body in rows
            ],
        )


def _mark_stale(warehouse: Path, reason: str) -> None:
    """Record staleness where the UI will actually look at it (§5.5 staleness signal)."""
    try:
        conn = sqlite3.connect(warehouse)
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value_json, updated_at_utc) VALUES (?,?,?)",
            ("serving_stale", json.dumps({"reason": reason[:400], "at": _now()}), _now()),
        )
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass  # staleness bookkeeping must not mask the original rejection reason


def _warehouse_sha(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _degradation(source: sqlite3.Connection) -> dict[str, int] | None:
    """Counts that make this snapshot *not* complete: damaged raw, quarantined pages, unresolved records."""
    probes = (
        ("corrupt_objects", "SELECT COUNT(*) FROM raw_objects WHERE integrity_status <> 'OK'"),
        ("quarantined_pages", "SELECT COUNT(*) FROM quarantined_responses"),
        ("unresolved_records", "SELECT COUNT(*) FROM unresolved_records"),
    )
    counts: dict[str, int] = {}
    for key, sql in probes:
        try:
            value = int(source.execute(sql).fetchone()[0])
        except sqlite3.Error:
            value = 0  # a table the snapshot does not carry cannot contribute degradation
        if value:
            counts[key] = value
    return counts or None


def _exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone() is not None


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM '{table}'").fetchone()[0])


def snapshot_is_current(warehouse: Path, serving: Path) -> bool:
    """True only when the published snapshot provably reflects every warehouse row.

    Both sides are opened ``mode=ro`` so uncommitted-by-checkpoint WAL content is
    visible without touching the writer. Hashing the main ``.sqlite`` file would
    miss WAL writes and falsely report "current"; counting through a read-only
    connection does not. Fail-safe: any doubt returns False and the caller rebuilds.

    The UI's ``.stale.json`` marker means "collection in progress", not "stale
    data", so it is deliberately ignored here — the caller clears it when skipping.
    """
    try:
        if not warehouse.exists() or not serving.exists():
            return False
        with contextlib.closing(
            sqlite3.connect(serving.resolve().as_uri() + "?mode=ro", uri=True)
        ) as snap, contextlib.closing(
            sqlite3.connect(warehouse.resolve().as_uri() + "?mode=ro", uri=True)
        ) as source:
            snap.row_factory = sqlite3.Row
            if not _exists(snap, "serving_meta"):
                return False
            row = snap.execute(
                "SELECT snapshot_built_at_utc,row_counts_json FROM serving_meta ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return False
            if not _exists(snap, "serving_projection_meta"):
                return False
            projection = snap.execute(
                "SELECT projection_version FROM serving_projection_meta WHERE id=1"
            ).fetchone()
            if projection is None or int(projection[0]) != PROJECTION_VERSION:
                return False
            counts = json.loads(row["row_counts_json"])
            if not isinstance(counts, dict) or not counts:
                return False
            digest = counts.pop("content_digest", None)
            derived_catalog = counts.pop("derived_catalog", None)
            if digest is None or _content_digest(source) != digest:
                return False  # pre-digest manifest or a content flip: rebuild
            if not isinstance(derived_catalog, dict):
                return False
            catalog_tables = derived_catalog.get("table_counts")
            catalog_families = derived_catalog.get("family_latest_counts")
            catalog_modes = derived_catalog.get("search_index_modes")
            if (
                type(derived_catalog.get("catalog_schema_version")) is not int
                or derived_catalog["catalog_schema_version"] != 1
                or derived_catalog.get("snapshot_built_at_utc") != row["snapshot_built_at_utc"]
                or derived_catalog.get("source_projection_version") != PROJECTION_VERSION
                or not isinstance(derived_catalog.get("prepared_families"), list)
                or any(family not in {"TBMT", "YCBG", "DU_AN", "KHLCNT", "KQLCNT", "HOP_DONG"}
                       for family in derived_catalog["prepared_families"])
                or not isinstance(catalog_families, dict)
                or set(catalog_families) != {"TBMT", "YCBG", "DU_AN", "KHLCNT", "KQLCNT", "HOP_DONG"}
                or any(type(count) is not int or count < 0 for count in catalog_families.values())
                or not isinstance(catalog_modes, dict)
                or not isinstance(catalog_tables, dict)
                or not {"serving_catalog_meta", "serving_catalog_records", "serving_catalog_family_summary",
                        "serving_catalog_time_buckets", "serving_catalog_dimension_memberships",
                        "serving_catalog_dimension_counts"} <= set(catalog_tables)
            ):
                return False
            if not _exists(snap, "serving_catalog_meta"):
                return False
            catalog_meta = snap.execute("SELECT * FROM serving_catalog_meta WHERE singleton_id=1").fetchone()
            if (
                catalog_meta is None
                or int(catalog_meta["catalog_schema_version"]) != derived_catalog["catalog_schema_version"]
                or catalog_meta["snapshot_built_at_utc"] != row["snapshot_built_at_utc"]
                or int(catalog_meta["source_projection_version"]) != PROJECTION_VERSION
                or json.loads(catalog_meta["prepared_families_json"]) != derived_catalog["prepared_families"]
                or json.loads(catalog_meta["search_index_modes_json"]) != catalog_modes
                or json.loads(catalog_meta["derived_counts_json"]) != catalog_tables
            ):
                return False
            if "KHLCNT" in derived_catalog["prepared_families"]:
                if set(catalog_modes) != {"record", "component", "short"} or catalog_modes.get("short") != "short-grams":
                    return False
                if any(catalog_modes.get(scope) not in {"fts5-trigram", "gram-intersection"} for scope in ("record", "component")):
                    return False
                if "serving_catalog_short_grams" not in catalog_tables:
                    return False
                for scope in ("record", "component"):
                    if catalog_modes[scope] == "fts5-trigram":
                        fts_table = f"serving_catalog_{scope}_fts"
                        if fts_table not in catalog_tables:
                            return False
            for table, saved in counts.items():
                # The manifest is written by build_serving_db from SNAPSHOT_TABLES;
                # refusing unknown names keeps the count query injection-free.
                if table not in SNAPSHOT_TABLES or type(saved) is not int:
                    return False
                if not _exists(source, table) or not _exists(snap, table):
                    return False
                if _count(source, table) != saved or _count(snap, table) != saved:
                    return False
                # Row MAX(rowid) is not comparable: TEXT-key tables get fresh
                # rowids on copy. Content equality is owned by the digest above.
            actual_families = {
                family: int(count)
                for family, count in snap.execute(
                    "SELECT family,COUNT(*) FROM record_fields WHERE is_latest=1 GROUP BY family"
                )
            }
            if {family: catalog_families[family] for family in catalog_families} != {
                family: actual_families.get(family, 0) for family in catalog_families
            }:
                return False
            for table, saved in catalog_tables.items():
                if table not in {
                    "serving_catalog_meta", "serving_catalog_records", "serving_catalog_family_summary",
                    "serving_catalog_time_buckets", "serving_catalog_dimension_memberships",
                    "serving_catalog_dimension_counts", "serving_catalog_components",
                    "serving_catalog_component_fts", "serving_catalog_record_fts",
                    "serving_catalog_short_grams",
                } or type(saved) is not int or not _exists(snap, table) or _count(snap, table) != saved:
                    return False
            if "settings" in counts:
                left, right = _max_timestamp(source, "settings"), _max_timestamp(snap, "settings")
                return (left is not _UNREADABLE and right is not _UNREADABLE and left == right)
            return True
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return False


def _content_digest(conn: sqlite3.Connection) -> str:
    """Order-independent digest over EVERY column of every snapshot table.

    Includes primary keys (record linkage: a cross-swap between two rows must
    show up) and all columns the UI displays, so any content change at all
    invalidates the guard. NULL-safe, batched by full-row groups.
    """
    import hashlib

    hasher = hashlib.sha256()
    for table in SNAPSHOT_TABLES:
        if not _exists(conn, table):
            continue
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info('{table}')")]
        if not columns:
            continue
        projection = ",".join(f"COALESCE(quote(\"{c}\"),'-')" for c in columns)
        rows = conn.execute(
            f"SELECT {projection}, COUNT(*) FROM '{table}' GROUP BY {projection} ORDER BY 1"
        )
        while True:
            batch = rows.fetchmany(2000)
            if not batch:
                break
            for row in batch:
                hasher.update(("|".join(map(str, row))).encode("utf-8", "replace"))
    return hasher.hexdigest()


_UNREADABLE = object()  # missing/unreadable column: doubt, not equality


def _max_timestamp(conn: sqlite3.Connection, table: str):
    try:
        return conn.execute(f"SELECT MAX(updated_at_utc) FROM '{table}'").fetchone()[0]
    except sqlite3.Error:
        return _UNREADABLE


def _parse_marker(raw: str | None) -> dict:
    """stale_reason is free text unless this build wrote a structured marker into it."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"kind": "PLAIN", "text": raw}
    return parsed if isinstance(parsed, dict) else {}


def mark_stale(serving: Path, reason: str) -> None:
    """Keep stale evidence next to the snapshot the GUI reads, even when a publish fails."""
    marker = serving.with_suffix(".stale.json")
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump({"reason": reason[:400], "at": _now()}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(marker)


def serving_state(serving: Path) -> dict[str, object]:
    try:
        state = _read_serving_state(serving)
    except (sqlite3.Error, OSError, ValueError) as exc:
        state = {"present": serving.exists(), "stale": True, "reason": f"Snapshot unreadable: {exc}"}
    try:
        marker = json.loads(serving.with_suffix(".stale.json").read_text(encoding="utf-8"))
        state.update(stale=True, reason=marker["reason"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return state


def _read_serving_state(serving: Path) -> dict[str, object]:
    if not serving.exists():
        return {"present": False}
    conn = sqlite3.connect(serving.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        if not _exists(conn, "serving_meta"):
            return {"present": True, "built_at": None}
        row = conn.execute(
            "SELECT snapshot_built_at_utc, row_counts_json, stale, stale_reason"
            " FROM serving_meta ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return {"present": True, "built_at": None}
        marker = _parse_marker(row[3])
        projection_version = 1
        if _exists(conn, "serving_projection_meta"):
            projection = conn.execute(
                "SELECT projection_version FROM serving_projection_meta WHERE id=1"
            ).fetchone()
            if projection is not None:
                projection_version = int(projection[0])
        return {
            "present": True,
            "built_at": row[0],
            "row_counts": json.loads(row[1]),
            "stale": bool(row[2]),
            "degraded": marker.get("kind") == "DEGRADED",
            "degradation": marker.get("counts"),
            "reason": row[3],
            "projection_version": projection_version,
            "schema_versions": [v for v, _, _ in MIGRATIONS],
        }
    finally:
        conn.close()
