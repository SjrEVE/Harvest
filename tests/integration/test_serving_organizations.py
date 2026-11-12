from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from test_serving_snapshot import commit_page

from santhau_studio.storage import serving

pytest_plugins = ('test_serving_snapshot',)


def test_object_winning_code_preserves_legacy_membership_and_source_provenance(env, tmp_path: Path):
    writer, warehouse, _, data_root = env
    commit_page(writer, [{"notifyNo": "SYN-ORG-OBJ", "publicDateKqlcnt": "2026-10-31T12:00:00Z", "notifyVersion": "00", "bidName": "Synthetic object winner",
                          "winningCode": {"member": "WIN", "duplicate": "WIN", "number": 7, "nested": ["NO"]}}], family="KQLCNT")
    target = tmp_path / 'serving.sqlite'
    result = serving.build_serving_db(data_root / 'warehouse.sqlite', target)
    assert result.published, result.reason
    with closing(sqlite3.connect(target)) as db:
        edge = db.execute("SELECT party_tax_id,content_hash,raw_hash,provenance_json FROM market_relation_edges WHERE basis='winningCode'").fetchall()
        assert len(edge) == 1, 'object text members accepted by legacy membership must be indexed'
        assert edge[0][0] == 'WIN'
        source = db.execute('SELECT id,current_content_hash,current_record_object_sha FROM source_records').fetchone()
        assert edge[0][1:3] == source[1:3]
        assert json.loads(edge[0][3]) == {'basis': 'winningCode', 'party_tax_id': 'WIN', 'source_record_id': source[0], 'content_hash': source[1], 'raw_hash': source[2]}
        assert db.execute('SELECT winning_codes_json FROM record_fields').fetchone()[0] is None
        assert serving._content_digest(db) == serving._content_digest(warehouse)
        metadata = json.loads(db.execute('SELECT row_counts_json FROM serving_meta').fetchone()[0])['derived_organizations']
        assert metadata == {'organization_schema_version': 1, 'source_projection_version': 2, 'snapshot_built_at_utc': db.execute('SELECT snapshot_built_at_utc FROM serving_meta').fetchone()[0], 'winning_membership_count': 1}
    assert serving.snapshot_is_current(data_root / 'warehouse.sqlite', target)

@pytest.mark.parametrize(('winning', 'expected'), [
    (' WIN ', {'WIN', ' WIN '}), (['WIN', 'WIN', '', 7, ['NO']], {'WIN', ''}),
    ({'a': 'WIN', 'b': 'WIN', 'nested': {'a': 'NO'}, 'null': None}, {'WIN'}),
    (None, set()), ([], set()),
])
def test_normalized_membership_all_shapes_is_additive(env, tmp_path: Path, winning, expected):
    writer, _, _, data_root = env
    commit_page(writer, [{'notifyNo': 'SYN-SHAPE', 'notifyVersion': '00',
                          'publicDateKqlcnt': '2026-10-31T12:00:00Z', 'winningCode': winning}], family='KQLCNT')
    target = tmp_path / 'serving.sqlite'
    result = serving.build_serving_db(data_root / 'warehouse.sqlite', target)
    assert result.published, result.reason
    with closing(sqlite3.connect(target)) as db:
        assert {r[0] for r in db.execute("SELECT party_tax_id FROM market_relation_edges WHERE basis='winningCode'")} == expected
        assert db.execute("SELECT COUNT(*) FROM market_relation_edges WHERE basis='winningCode'").fetchone()[0] == len(expected)


def _object_snapshot(env, tmp_path):
    writer, _, _, data_root = env
    commit_page(writer, [{'notifyNo': 'SYN-FINALIZE', 'notifyVersion': '00',
                          'publicDateKqlcnt': '2026-10-31T12:00:00Z', 'winningCode': {'a': 'WIN'}}], family='KQLCNT')
    target = tmp_path / 'private-candidate.sqlite'
    assert serving.build_serving_db(data_root / 'warehouse.sqlite', target).published
    return target, data_root / 'warehouse.sqlite'


@pytest.mark.parametrize('damage', ['missing', 'count', 'stamp', 'schema', 'index', 'parity', 'manifest'])
def test_snapshot_current_rejects_invalid_organization_capability(env, tmp_path: Path, damage):
    target, warehouse = _object_snapshot(env, tmp_path)
    with closing(sqlite3.connect(target)) as db:
        if damage == 'missing':
            db.execute('DROP TABLE serving_organization_meta')
        elif damage == 'index':
            db.execute('DROP INDEX ix_mre_party_tax_id')
        elif damage == 'parity':
            db.execute("UPDATE market_relation_edges SET join_key='OTHER',party_tax_id='OTHER' WHERE basis='winningCode'")
        elif damage == 'manifest':
            db.execute("UPDATE serving_meta SET row_counts_json=json_remove(row_counts_json,'$.derived_organizations')")
        else:
            db.execute('PRAGMA ignore_check_constraints=ON')
            column, value = {'count': ('winning_membership_count', 999), 'stamp': ('snapshot_built_at_utc', 'WRONG'), 'schema': ('organization_schema_version', 9)}[damage]
            db.execute(f'UPDATE serving_organization_meta SET {column}=?', (value,))
        db.commit()
    assert not serving.snapshot_is_current(warehouse, target)


def _remove_organization_capability(target):
    with closing(sqlite3.connect(target)) as db:
        db.execute('DROP TABLE serving_organization_meta')
        db.execute("DELETE FROM market_relation_edges WHERE basis='winningCode'")
        db.execute("UPDATE serving_meta SET row_counts_json=json_remove(row_counts_json,'$.derived_organizations')")
        db.commit()


def test_private_v2_finalizer_matches_full_builder_without_source_mutation(env, tmp_path: Path):
    target, warehouse = _object_snapshot(env, tmp_path)
    with closing(sqlite3.connect(target)) as db:
        original_meta = db.execute('SELECT * FROM serving_meta').fetchone()
        original_projection = db.execute('SELECT * FROM serving_projection_meta').fetchall()
        original_edges = db.execute('SELECT source_key,from_record_id,to_record_id,source_record_id,relation,basis,join_key,party_tax_id,content_hash,raw_hash,provenance_json FROM market_relation_edges').fetchall()
        original_sources = {t: db.execute(f'SELECT * FROM {t}').fetchall() for t in serving.SNAPSHOT_TABLES if serving._exists(db, t)}
    _remove_organization_capability(target)
    assert not serving.snapshot_is_current(warehouse, target)
    metadata = serving.upgrade_organization_snapshot(target)
    assert metadata['winning_membership_count'] == 1
    with closing(sqlite3.connect(target)) as db:
        assert db.execute('SELECT * FROM serving_meta').fetchone() == original_meta
        assert db.execute('SELECT * FROM serving_projection_meta').fetchall() == original_projection
        assert db.execute('SELECT source_key,from_record_id,to_record_id,source_record_id,relation,basis,join_key,party_tax_id,content_hash,raw_hash,provenance_json FROM market_relation_edges').fetchall() == original_edges
        assert {t: db.execute(f'SELECT * FROM {t}').fetchall() for t in original_sources} == original_sources
    assert serving.snapshot_is_current(warehouse, target)


def test_finalizer_failure_rolls_back_edges_and_marker(env, tmp_path: Path, monkeypatch):
    from santhau_studio.storage import organization_read_model
    target, _ = _object_snapshot(env, tmp_path)
    _remove_organization_capability(target)
    before = target.read_bytes()
    def reject(*_args):
        raise ValueError('injected organization parity failure')
    monkeypatch.setattr(organization_read_model, 'validate_organizations', reject)
    with pytest.raises(serving.SnapshotRejected, match='injected organization parity failure'):
        serving.upgrade_organization_snapshot(target)
    assert target.read_bytes() == before


@pytest.mark.parametrize('damage', ['projection', 'digest', 'count', 'listing', 'catalog'])
def test_finalizer_rejects_invalid_base_before_changes(env, tmp_path: Path, damage):
    target, _ = _object_snapshot(env, tmp_path)
    _remove_organization_capability(target)
    with closing(sqlite3.connect(target)) as db:
        sql = {'projection': 'UPDATE serving_projection_meta SET projection_version=1',
               'digest': "UPDATE serving_meta SET row_counts_json=json_set(row_counts_json,'$.content_digest','bad')",
               'count': "UPDATE serving_meta SET row_counts_json=json_set(row_counts_json,'$.source_records',999)",
               'listing': 'DROP INDEX ix_rf_listing_date',
               'catalog': 'DELETE FROM serving_catalog_family_summary'}[damage]
        db.execute(sql)
        db.commit()
    before = target.read_bytes()
    with pytest.raises(serving.SnapshotRejected):
        serving.upgrade_organization_snapshot(target)
    assert target.read_bytes() == before


def test_finalizer_preserves_raw_only_and_deep_membership_edges(env, tmp_path: Path):
    writer, warehouse, _, data_root = env
    commit_page(writer, [{'notifyNo': 'SYN-RAW-DEEP', 'notifyVersion': '00',
                          'publicDateKqlcnt': '2026-10-31T12:00:00Z', 'winningCode': ['RAW']}], family='KQLCNT')
    record_id, raw_hash = warehouse.execute('SELECT id,current_record_object_sha FROM source_records').fetchone()
    warehouse.execute("UPDATE source_record_versions SET normalized_json=json_remove(normalized_json,'$.winningCode')")
    warehouse.execute('INSERT INTO contractor_memberships(source_record_id,party_object_sha,party_name,party_tax_id_presence,'
                      'party_tax_id,role,membership_share_presence,membership_share,ranking,provenance_json) VALUES(?,?,?,?,?,?,?,?,?,?)',
                      (record_id, raw_hash, 'Synthetic deep member', 'KNOWN', 'DEEP', 'WINNER', 'MISSING', None, None, '{}'))
    warehouse.commit()
    target = tmp_path / 'private-candidate.sqlite'
    assert serving.build_serving_db(data_root / 'warehouse.sqlite', target).published
    with closing(sqlite3.connect(target)) as db:
        before_edges = db.execute('SELECT * FROM market_relation_edges').fetchall()
        assert {(r[6], r[8]) for r in before_edges} == {('winningCode', 'RAW'), ('deep_contractorCode', 'DEEP')}
        db.execute('DROP TABLE serving_organization_meta')
        db.execute("UPDATE serving_meta SET row_counts_json=json_remove(row_counts_json,'$.derived_organizations')")
        db.commit()
    metadata = serving.upgrade_organization_snapshot(target)
    assert metadata['winning_membership_count'] == 1
    with closing(sqlite3.connect(target)) as db:
        assert db.execute('SELECT * FROM market_relation_edges').fetchall() == before_edges
    assert serving.snapshot_is_current(data_root / 'warehouse.sqlite', target)
