import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from test_runner_modes import DAY, FakePortal, make_runner, page_of, writer  # noqa: F401

from santhau_studio.storage.migrations import migrate, status


def test_incremental_default_does_not_refetch_proven_day(writer):  # noqa: F811
    portal=FakePortal({"DAY":[page_of(0,2)]})
    runner=make_runner(writer,portal)
    runner.run_interval(DAY,"DAY","initial")
    writer.conn.execute("INSERT INTO watermarks(source_key,family,watermark_to_utc,updated_at_utc) VALUES('egp_home','TBMT',?,'2026-11-02T00:00:00Z')",(DAY.to_utc.isoformat().replace("+00:00","Z"),))
    portal.calls.clear()
    runner.incremental("rerun",now=DAY.to_utc)
    assert not any(span==DAY for _,span,_ in portal.calls)


def test_newer_database_is_rejected_before_any_migration(tmp_path):
    with closing(sqlite3.connect(tmp_path/'future.sqlite')) as conn:
        conn.execute("CREATE TABLE software_migrations(version INTEGER PRIMARY KEY,name TEXT,applied_at_utc TEXT,checksum TEXT)")
        conn.execute("INSERT INTO software_migrations VALUES(999,'future','2026-01-01','x')")
        conn.commit()
        before=list(conn.iterdump())
        with pytest.raises(RuntimeError,match="NEWER_SCHEMA"):
            migrate(conn,Path(__file__).parents[2])
        assert list(conn.iterdump()) == before
        assert not status(conn).up_to_date


def test_v5_upgrade_preserves_data_and_records_previous_refresh_policy(tmp_path):
    import santhau_studio.storage.migrations as migration
    from santhau_studio.storage.warehouse import connect
    original=migration.MIGRATIONS
    migration.MIGRATIONS=original[:5]
    try:
        conn=connect(tmp_path/'v5.sqlite',create_schema=True)
    finally:
        migration.MIGRATIONS=original
    try:
        scope={'date_from':'2022-01-01','date_to':'2026-11-06','sources':[], 'policy':{'repair_enabled':True,'reconcile_enabled':True}}
        conn.execute("INSERT INTO settings VALUES('collection_scope_v1',?,'2026-11-06T00:00:00Z')",(json.dumps(scope),))
        conn.execute("INSERT INTO settings VALUES('automatic_public_deep',?,'2026-11-06T00:00:00Z')",(json.dumps({'enabled':True,'batch_requests':30}),))
        assert migrate(conn,Path(__file__).parents[2]).current_version >= 6
        changed=json.loads(conn.execute("SELECT value_json FROM settings WHERE key='collection_scope_v1'").fetchone()[0])
        assert changed['date_from']==scope['date_from'] and changed['sources']==[]
        assert changed['policy']['repair_enabled'] is True
        assert changed['policy']['reconcile_enabled'] is False
        deep=json.loads(conn.execute("SELECT value_json FROM settings WHERE key='automatic_public_deep'").fetchone()[0])
        assert deep['enabled'] is True and deep['refresh_expired'] is False
        prior=json.loads(conn.execute("SELECT value_json FROM settings WHERE key='pre_missing_only_policy'").fetchone()[0])
        assert prior['collection_scope_v1']['policy']['reconcile_enabled'] is True
    finally:
        conn.close()
