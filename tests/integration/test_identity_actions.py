from contextlib import closing

import pytest
from test_search_filters import database

from santhau_studio.storage.warehouse import WarehouseWriter


def test_manual_merge_split_undo_is_audited_and_keeps_records(tmp_path):
    from santhau_studio.storage.identity_actions import apply_action
    backend = database(tmp_path)
    with closing(WarehouseWriter.open(backend.data_root)) as writer:
        original = writer.conn.execute('SELECT COUNT(*) FROM source_records').fetchone()[0]
        merge = apply_action(writer.conn, {'action':'MERGE','record_ids':[1,2], 'reason':'owner confirmed', 'evidence':{'note':'synthetic identity review'}})
        assert writer.conn.execute('SELECT COUNT(*) FROM entity_source_links').fetchone()[0] == 2
        split = apply_action(writer.conn, {'action':'SPLIT','record_ids':[2], 'entity_id':merge['entity_id'], 'reason':'split review','evidence':{'note':'review'}})
        assert writer.conn.execute('SELECT COUNT(*) FROM entity_source_links').fetchone()[0] == 1
        apply_action(writer.conn, {'action':'UNDO','action_id':split['action_id'],'reason':'undo split','evidence':{'note':'review'}})
        assert writer.conn.execute('SELECT COUNT(*) FROM entity_source_links').fetchone()[0] == 2
        apply_action(writer.conn, {'action':'UNDO','action_id':merge['action_id'],'reason':'undo merge','evidence':{'note':'review'}})
        assert writer.conn.execute('SELECT COUNT(*) FROM entity_source_links').fetchone()[0] == 0
        assert writer.conn.execute('SELECT COUNT(*) FROM source_records').fetchone()[0] == original
        assert all(r[0].startswith('S-1-') for r in writer.conn.execute('SELECT actor_sid FROM identity_audit_actions'))
        with pytest.raises(ValueError, match='already'):
            apply_action(writer.conn, {'action':'UNDO','action_id':merge['action_id'],'reason':'again','evidence':{'note':'review'}})
