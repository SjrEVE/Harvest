"""Versioned schema migration (§2.1 "SQLite WAL + migration có version").

The source repo carried SCHEMA_VERSION=5 while README claimed "Schema version 1"
(BUG-13). Here the version is derived from the applied migrations table, so a document
can drift but the database cannot lie about itself.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

MIGRATIONS: tuple[tuple[int, str, Path], ...] = (
    # (version, name, sql file). v1 is the frozen G0-B contract; later entries are additive.
    (1, "g0_b_contract", Path("docs/ddl/g0_b_proposed.sql")),
    (2, "public_deep_checkpoints", Path("docs/ddl/002_public_deep.sql")),
    (3, "attempt_telemetry", Path("docs/ddl/003_attempt_telemetry.sql")),
    (4, "deep_parser_metadata", Path("docs/ddl/004_deep_parser_metadata.sql")),
    (5, "scoped_maintenance", Path("docs/ddl/005_scoped_maintenance.sql")),
    (6, "missing_only_policy", Path("docs/ddl/006_missing_only_policy.sql")),
    (7, "money_roles_parser_v4", Path("docs/ddl/007_money_roles_parser_v4.sql")),
    (8, "money_roles_m1", Path("docs/ddl/008_money_roles_m1.sql")),
    (9, "m1_lane_enums", Path("docs/ddl/009_m1_lane_enums.sql")),
)


@dataclass(frozen=True)
class MigrationState:
    current_version: int
    applied: tuple[tuple[int, str], ...]
    pending: tuple[tuple[int, str], ...]

    @property
    def up_to_date(self) -> bool:
        return not self.pending and self.current_version <= max(v for v, _, _ in MIGRATIONS)


def checksum(sql: str) -> str:
    return hashlib.sha256(sql.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='software_migrations'"
    ).fetchone()
    if not row:
        return set()
    return {int(r[0]) for r in conn.execute("SELECT version FROM software_migrations")}


def status(conn: sqlite3.Connection) -> MigrationState:
    done = applied_versions(conn)
    applied = tuple((v, n) for v, n, _ in MIGRATIONS if v in done)
    pending = tuple((v, n) for v, n, _ in MIGRATIONS if v not in done)
    return MigrationState(current_version=max(done) if done else 0, applied=applied, pending=pending)


def migrate(conn: sqlite3.Connection, repo_root: Path) -> MigrationState:
    """Apply pending migrations. Each runs in its own transaction and records a checksum.

    A checksum mismatch on an already-applied version is an error, not a re-run: it means
    the contract file changed under a database that was built from the old one.

    A migration whose SQL carries a `-- FK_OFF` line rebuilds a parent table
    (e.g. deep_captures, runs) and therefore needs foreign key enforcement OFF
    for its own transaction: with enforcement ON, DROP TABLE fails while child
    rows exist. The pragma is a no-op inside a transaction, so it is toggled
    outside it — OFF before the script runs, back ON after COMMIT — and the
    migration fails if PRAGMA foreign_key_check finds any violation after
    re-enabling. No other migration touches enforcement.
    """
    # The contract file declares software_migrations itself, so the harness must not
    # pre-create it: an IF NOT EXISTS here would make the migration's own CREATE fail.
    done = applied_versions(conn)
    supported = {v for v, _, _ in MIGRATIONS}
    if done - supported:
        raise RuntimeError(f"NEWER_SCHEMA: database versions {sorted(done - supported)} are not supported by this runtime; use a compatible newer build.")
    for version, name, rel in MIGRATIONS:
        path = repo_root / rel
        sql = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        digest = checksum(sql)
        if version in done:
            recorded = conn.execute(
                "SELECT checksum FROM software_migrations WHERE version=?", (version,)
            ).fetchone()
            if recorded and recorded[0] != digest:
                raise RuntimeError(
                    f"migration {version} ({name}) checksum changed: database was built from a "
                    f"different contract. Recorded {recorded[0][:12]}, file now {digest[:12]}."
                )
            continue
        # The transaction is opened *inside* the script because executescript commits any
        # pending transaction before it starts: `conn.execute("BEGIN")` followed by
        # executescript would apply the DDL outside any transaction at all. With the BEGIN in
        # the script, a failure mid-way rolls the DDL back too — verified against SQLite, the
        # transaction is still open after a successful executescript.
        # (`PRAGMA foreign_keys` inside this transaction is inert; connect() sets it outside.)
        fk_off = "-- FK_OFF" in sql.splitlines()
        if fk_off:
            conn.execute("PRAGMA foreign_keys=OFF")
        try:
            conn.executescript("BEGIN;\n" + sql)
            conn.execute(
                "INSERT INTO software_migrations (version, name, applied_at_utc, checksum) VALUES (?,?,?,?)",
                (version, name, _now(), digest),
            )
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            if fk_off:
                conn.execute("PRAGMA foreign_keys=ON")
            raise
        conn.execute("COMMIT")
        if fk_off:
            # Re-enable only after COMMIT: the pragma is a no-op inside a
            # transaction, so enabling before COMMIT would silently leave
            # enforcement off for the rest of the connection's life.
            conn.execute("PRAGMA foreign_keys=ON")
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(
                    f"migration {version} ({name}) left foreign key violations: {violations[:3]}"
                )
    return status(conn)


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
