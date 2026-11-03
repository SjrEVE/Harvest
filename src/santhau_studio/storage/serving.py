"""Serving snapshot: read model built from the warehouse, published atomically (§5.5).

The GUI reads this, never the warehouse, so the single writer is never contended and a
half-built snapshot can never be observed. Publish is build-to-temp → integrity check →
row-count reconciliation → os.replace. A snapshot that fails any gate leaves the previous
good one serving, marked STALE with a reason the UI can show (§5.5 "không publish dữ liệu
suy giảm như thể đầy đủ").
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from santhau_studio.domain.text import fold_vietnamese
from santhau_studio.storage.migrations import MIGRATIONS

FTS_BATCH = 2000

SNAPSHOT_TABLES = (
    "source_records", "source_record_versions", "raw_revisions", "observations",
    "coverage_intervals", "coverage_proofs", "notice_groups", "notice_group_members",
    "canonical_entities", "entity_source_links", "unresolved_records", "quarantined_responses",
    "deep_captures", "page_receipts", "watermarks", "reconcile_gaps", "money_amounts",
    "lots", "goods_rows", "contractor_memberships", "documents", "filter_boundary_rules",
    "raw_objects", "source_registry", "settings",
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
        _materialise_family_stats(dest)
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
            (_now(), _warehouse_sha(warehouse), json.dumps(counts, sort_keys=True),
             1 if degradation else 0,
             json.dumps({"kind": "DEGRADED", "counts": degradation}, sort_keys=True) if degradation else None),
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
            candidate.replace(target)  # old snapshot never half-overwritten
        except OSError as exc:
            candidate.unlink(missing_ok=True)
            mark_stale(target, str(exc))
            return PublishResult(False, {}, reason=str(exc), stale=True)
        target.with_suffix(".stale.json").unlink(missing_ok=True)
        return PublishResult(published=True, snapshot_rows=counts, degraded=bool(degradation))
    finally:
        source.close()


def _copy_schema_and_rows(source: sqlite3.Connection, dest: sqlite3.Connection) -> None:
    for name, sql, kind in source.execute(
        "SELECT name, sql, type FROM sqlite_master WHERE sql IS NOT NULL AND type IN ('table','view') ORDER BY name"
    ):
        if name in ("software_migrations",):
            continue
        try:
            dest.execute(sql)
        except sqlite3.Error as exc:
            raise SnapshotRejected(f"could not recreate {kind} {name}: {exc}") from exc
    tables = [
        r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'software_migrations'")
    ]
    for table in tables:
        cursor = source.execute(f"SELECT * FROM '{table}'")
        while rows := cursor.fetchmany(FTS_BATCH):
            columns = rows[0].keys()
            placeholders = ",".join("?" * len(columns))
            dest.executemany(
                f"INSERT INTO '{table}' ({','.join(columns)}) VALUES ({placeholders})",
                [tuple(r) for r in rows],
            )
    # Indexes go last: they reference tables that only exist after the loop above.
    for name, sql in source.execute(
        "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL AND type='index'"
        " AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ):
        if sql is None:
            continue
        try:
            dest.execute(sql)
        except sqlite3.Error as exc:
            raise SnapshotRejected(f"could not recreate index {name}: {exc}") from exc


def _materialise_family_stats(dest: sqlite3.Connection) -> None:
    """Family counters computed once per publish; the dashboard becomes a table read.

    The snapshot is rebuilt wholesale on every publish, so this is the invalidation mechanism:
    a number in this table is exactly as fresh as the snapshot that carries it.
    """
    dest.execute(
        "CREATE TABLE IF NOT EXISTS serving_family_stats ("
        " family TEXT PRIMARY KEY, records INTEGER NOT NULL, versions INTEGER NOT NULL,"
        " raw_revisions INTEGER NOT NULL, observations INTEGER NOT NULL,"
        " last_verified_at_utc TEXT)"
    )
    dest.execute("DELETE FROM serving_family_stats")
    dest.execute(
        "INSERT INTO serving_family_stats (family, records, versions, raw_revisions, observations,"
        " last_verified_at_utc)"
        " SELECT families.family,"
        "  (SELECT COUNT(*) FROM source_records r WHERE r.family = families.family),"
        "  (SELECT COUNT(*) FROM source_record_versions v JOIN source_records sr ON sr.id = v.source_record_id"
        "     WHERE sr.family = families.family),"
        "  (SELECT COUNT(*) FROM raw_revisions rr JOIN source_records sr ON sr.id = rr.source_record_id"
        "     WHERE sr.family = families.family),"
        "  (SELECT COUNT(*) FROM observations o WHERE o.family = families.family),"
        "  (SELECT MAX(cp.verified_at_utc) FROM coverage_proofs cp WHERE cp.family = families.family)"
        " FROM ("
        "   SELECT family FROM source_records"
        "   UNION SELECT family FROM coverage_proofs"
        "   UNION SELECT family FROM observations"
        " ) AS families ORDER BY families.family"
    )


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
        " LEFT JOIN source_record_versions v ON v.id = ("
        "   SELECT id FROM source_record_versions WHERE source_record_id=sr.id"
        "   ORDER BY content_version_seq DESC LIMIT 1)"
    )
    while True:
        rows = cursor.fetchmany(FTS_BATCH)
        if not rows:
            break
        dest.executemany(
            "INSERT INTO search_index (record_id, family, natural_key, title, investor_name, body) VALUES (?,?,?,?,?,?)",
            [
                (record_id, fold_vietnamese(family), fold_vietnamese(natural_key),
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
        return {
            "present": True,
            "built_at": row[0],
            "row_counts": json.loads(row[1]),
            "stale": bool(row[2]),
            "degraded": marker.get("kind") == "DEGRADED",
            "degradation": marker.get("counts"),
            "reason": row[3],
            "schema_versions": [v for v, _, _ in MIGRATIONS],
        }
    finally:
        conn.close()
