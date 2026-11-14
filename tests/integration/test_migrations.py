"""Migration bookkeeping is wired into schema bootstrap, not available only as a library call.

BUG-13 was a version number that did not describe the database. Recording the applied version
and its checksum *during* `connect(create_schema=True)` is what makes that detectable; a
`migrate()` nobody calls would have been decoration.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from santhau_studio.storage import migrations
from santhau_studio.storage.migrations import MIGRATIONS, checksum, migrate, status
from santhau_studio.storage.warehouse import SCHEMA_PATH, WarehouseWriter, connect

REPO_ROOT = SCHEMA_PATH.parents[2]


def test_bootstrap_records_the_frozen_contract_as_version_one(tmp_path: Path) -> None:
    conn = connect(tmp_path / "warehouse.sqlite", create_schema=True)
    state = status(conn)
    assert state.current_version == 7
    assert state.applied == ((1, "g0_b_contract"), (2, "public_deep_checkpoints"), (3, "attempt_telemetry"), (4, "deep_parser_metadata"), (5, "scoped_maintenance"), (6, "missing_only_policy"), (7, "money_roles_parser_v4"))
    assert state.up_to_date
    recorded = conn.execute(
        "SELECT name, checksum FROM software_migrations WHERE version=1"
    ).fetchone()
    assert recorded["checksum"] == checksum(
        SCHEMA_PATH.read_text(encoding="utf-8").replace("\r\n", "\n")
    )
    conn.close()


def test_writer_open_gives_a_migrated_database(tmp_path: Path) -> None:
    writer = WarehouseWriter.open(tmp_path, create_schema=True, role="backfill")
    try:
        assert status(writer.conn).current_version == 7
    finally:
        writer.close()


def test_migrate_is_idempotent_on_an_applied_database(tmp_path: Path) -> None:
    conn = connect(tmp_path / "warehouse.sqlite", create_schema=True)
    before = conn.execute("SELECT COUNT(*) FROM software_migrations").fetchone()[0]
    state = migrate(conn, REPO_ROOT)
    assert state.up_to_date
    assert conn.execute("SELECT COUNT(*) FROM software_migrations").fetchone()[0] == before
    conn.close()


def test_changed_contract_file_is_refused_rather_than_rerun(tmp_path: Path, monkeypatch) -> None:
    """The realistic drift: someone edits the frozen DDL under a database built from the old one."""
    conn = connect(tmp_path / "warehouse.sqlite", create_schema=True)
    tampered = tmp_path / "contract.sql"
    tampered.write_text(
        SCHEMA_PATH.read_text(encoding="utf-8") + "\n-- added after the database was built\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(migrations, "MIGRATIONS", ((1, "g0_b_contract", tampered), *MIGRATIONS[1:]))
    with pytest.raises(RuntimeError, match="checksum changed"):
        migrate(conn, REPO_ROOT)
    conn.close()


def test_a_database_with_no_migration_row_reports_pending(tmp_path: Path) -> None:
    """An unversioned database must not be reported as up to date."""
    bare = sqlite3.connect(tmp_path / "bare.sqlite")
    bare.execute("CREATE TABLE unrelated (x)")
    state = status(bare)
    assert state.current_version == 0
    assert state.pending == ((1, "g0_b_contract"), (2, "public_deep_checkpoints"), (3, "attempt_telemetry"), (4, "deep_parser_metadata"), (5, "scoped_maintenance"), (6, "missing_only_policy"), (7, "money_roles_parser_v4"))
    assert not state.up_to_date
    bare.close()


def test_migration_list_is_the_frozen_contract_only() -> None:
    """A new entry means a new contract decision; it should never appear silently."""
    assert (
        (1, "g0_b_contract", Path("docs/ddl/g0_b_proposed.sql")),
        (2, "public_deep_checkpoints", Path("docs/ddl/002_public_deep.sql")),
        (3, "attempt_telemetry", Path("docs/ddl/003_attempt_telemetry.sql")),
        (4, "deep_parser_metadata", Path("docs/ddl/004_deep_parser_metadata.sql")),
        (5, "scoped_maintenance", Path("docs/ddl/005_scoped_maintenance.sql")),
        (6, "missing_only_policy", Path("docs/ddl/006_missing_only_policy.sql")),
        (7, "money_roles_parser_v4", Path("docs/ddl/007_money_roles_parser_v4.sql")),
    ) == MIGRATIONS


def test_public_deep_upgrade_preserves_existing_records(tmp_path, monkeypatch):
    from test_deep_run import seed_records

    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", MIGRATIONS[:1])
        writer = seed_records(tmp_path)
        before = [tuple(r) for r in writer.conn.execute("SELECT * FROM source_records ORDER BY id")]
        writer.close()
    writer = WarehouseWriter.open(tmp_path, create_schema=True)
    try:
        assert status(writer.conn).current_version == 7
        assert [
            tuple(r) for r in writer.conn.execute("SELECT * FROM source_records ORDER BY id")
        ] == before
        assert writer.conn.execute("SELECT COUNT(*) FROM deep_tasks").fetchone()[0] == 0
        assert writer.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        writer.close()


def test_every_migration_ddl_is_packaged_in_the_linux_build() -> None:
    """RC3-10 lesson: migration 007 was registered but missing from the .deb.

    tools/build_linux.py lists DDL data files by hand for Nuitka. A migration
    whose SQL is not packaged fails at runtime on the deployed machine, while
    every test (running from source) stays green. Every MIGRATIONS entry must
    appear as an --include-data-files line in the build script.
    """
    build_script = (REPO_ROOT / "tools" / "build_linux.py").read_text(encoding="utf-8")
    missing = [
        rel.as_posix()
        for _, _, rel in MIGRATIONS
        if f"--include-data-files={rel.as_posix()}=" not in build_script
    ]
    assert not missing, f"migration DDL not packaged in the Linux build: {missing}"


def test_migration_seven_widens_money_roles(tmp_path: Path) -> None:
    """RC3-10: migration 007 rebuilds money_amounts so parser-v4 roles insert.

    The v1 role CHECK only allowed PACKAGE_VALUE/BID_PRICE/WIN_PRICE/UNIT_PRICE/
    CONTRACT_VALUE/BUDGET/ADJUSTMENT. A parser-v4 role (REOFFER_PRICE) must insert
    after bootstrap; a v1 role must still insert too.
    """
    conn = connect(tmp_path / "warehouse.sqlite", create_schema=True)
    try:
        assert status(conn).current_version == 7
        for role in ("REOFFER_PRICE", "BID_PRICE"):
            conn.execute(
                "INSERT INTO money_amounts (owner_kind, owner_id, role, presence_state,"
                " value, value_raw, currency, currency_code, tax_basis,"
                " provenance_json, observed_at_utc)"
                " VALUES ('PACKAGE', 1, ?, 'KNOWN', 123, '123', 'KNOWN', 'VND',"
                " 'UNKNOWN', '{\"lane\":\"REOFFER\"}', '2026-11-08T00:00:00Z')",
                (role,),
            )
        rows = conn.execute(
            "SELECT role FROM money_amounts ORDER BY role"
        ).fetchall()
        assert [r[0] for r in rows] == ["BID_PRICE", "REOFFER_PRICE"]
    finally:
        conn.close()


def test_a_failed_migration_leaves_no_schema_and_no_version(tmp_path: Path, monkeypatch) -> None:
    """Half-applied DDL is the worst outcome: the database would look newer than it is."""
    broken = tmp_path / "broken.sql"
    broken.write_text("CREATE TABLE ok_first (x);\nCREATE TABLE broken ( ", encoding="utf-8")
    monkeypatch.setattr(migrations, "MIGRATIONS", ((7, "broken", broken),))
    conn = connect(tmp_path / "warehouse.sqlite")
    with pytest.raises(sqlite3.Error):
        migrate(conn, REPO_ROOT)
    assert not conn.in_transaction, "the failed migration must have rolled back"
    assert 7 not in migrations.applied_versions(conn)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ok_first" not in tables, f"DDL from the rolled-back migration survived: {tables}"
    conn.close()
