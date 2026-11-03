"""Audited manual identity links. Source records, hashes and history are never merged away."""
import json
import uuid
from datetime import UTC, datetime

from santhau_studio.domain.redaction import redact_payload
from santhau_studio.platform_win.process import current_process_sid


def _links(conn, entity):
    return [dict(r) for r in conn.execute('SELECT * FROM entity_source_links WHERE entity_id=? ORDER BY source_record_id,relation', (entity,))]


def apply_action(conn, request):
    action = request.get('action')
    reason = str(request.get('reason') or '').strip()
    evidence = request.get('evidence')
    if not reason or not isinstance(evidence, dict) or not evidence:
        raise ValueError('Reason and explicit evidence are required')
    sid = current_process_sid()
    if not sid:
        raise ValueError('Cannot identify the Windows actor SID')
    stamp = datetime.now(UTC).isoformat(timespec='seconds').replace('+00:00','Z')
    undo_id = None
    conn.execute('SAVEPOINT identity_action')
    try:
        if action == 'UNDO':
            undo_id = int(request.get('action_id',0))
            old = conn.execute('SELECT * FROM identity_audit_actions WHERE id=?', (undo_id,)).fetchone()
            if old is None or old['action'] == 'UNDO':
                raise ValueError('Unknown or unsupported undo target')
            if conn.execute('SELECT 1 FROM identity_audit_actions WHERE undo_action_id=?', (undo_id,)).fetchone():
                raise ValueError('Action already undone')
            saved = json.loads(old['evidence_json'])
            entity = saved['entity_id']
            before = _links(conn,entity)
            if before != saved['after']:
                raise ValueError('Identity has newer edits; undo those first')
            after = saved['before']
        elif action in ('MERGE','SPLIT','CONFIRM_LINK','REJECT_LINK'):
            ids = sorted({int(i) for i in request.get('record_ids',[])})
            if not ids or len(ids) > 100 or (action == 'MERGE' and len(ids) < 2):
                raise ValueError('Select 1..100 records (merge requires at least two)')
            if conn.execute('SELECT COUNT(*) FROM source_records WHERE id IN ('+','.join('?'*len(ids))+')', ids).fetchone()[0] != len(ids):
                raise ValueError('Some source records no longer exist')
            entity = str(request.get('entity_id') or '')
            if not entity:
                if action in ('SPLIT','REJECT_LINK'):
                    raise ValueError('Select the entity to split')
                entity = 'manual-' + uuid.uuid4().hex
                conn.execute("INSERT INTO canonical_entities VALUES(?,'TENDER_PACKAGE',?,'LINKED',?)", (entity, reason[:200], stamp))
            elif not conn.execute('SELECT 1 FROM canonical_entities WHERE id=?',(entity,)).fetchone():
                raise ValueError('Unknown entity')
            before = _links(conn, entity)
            after = [row for row in before if row['source_record_id'] not in ids or row['relation'] != 'SAME_AS']
            if action in ('MERGE','CONFIRM_LINK'):
                after.extend({'entity_id':entity,'source_record_id':record_id,'relation':'SAME_AS','method':'MANUAL',
                    'evidence_json':json.dumps(redact_payload(evidence),ensure_ascii=False,sort_keys=True),'verified_at_utc':stamp} for record_id in ids)
            after.sort(key=lambda row:(row['source_record_id'],row['relation']))
        else:
            raise ValueError('Unsupported identity action')
        receipt = {'entity_id':entity,'before':before,'after':after,'owner_evidence':redact_payload(evidence)}
        cursor = conn.execute('INSERT INTO identity_audit_actions(action,target_kind,target_ref,actor_sid,reason,evidence_json,undo_action_id,acted_at_utc) VALUES(?,?,?,?,?,?,?,?)',
            (action,'canonical_entity',entity,sid,redact_payload(reason),json.dumps(receipt,ensure_ascii=False),undo_id,stamp))
        conn.execute('DELETE FROM entity_source_links WHERE entity_id=?',(entity,))
        conn.executemany('INSERT INTO entity_source_links VALUES(:entity_id,:source_record_id,:relation,:method,:evidence_json,:verified_at_utc)',after)
        conn.execute('RELEASE identity_action')
        return {'ok':True,'action_id':cursor.lastrowid,'entity_id':entity,'action':action,'actor_sid':sid}
    except BaseException:
        conn.execute('ROLLBACK TO identity_action')
        conn.execute('RELEASE identity_action')
        raise
