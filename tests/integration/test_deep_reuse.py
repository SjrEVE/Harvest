from datetime import UTC, datetime, timedelta

from test_deep_run import seed_records

from santhau_studio.collectors.cgttrg import public_spec
from santhau_studio.workers.deep_run import run_deep_batch


def test_detail_and_reoffer_share_one_request_and_preserve_both_lanes(tmp_path):
    writer=seed_records(tmp_path)
    calls=[]
    def fetch(_family,key):
        calls.append(key)
        return {'notifyNo':key,'notifyVersion':'00','bidForm':'CGTTRG',
                'contractorsResult':[{'contractorCode':'one','contractorName':'A'}]}
    def collect(lane,force=False):
        return run_deep_batch(writer,run_id='reuse',family='TBMT',lane=lane,
            scope={'record_ids':[1],'force_refresh':force},fetcher=fetch,spec_provider=public_spec)
    try:
        assert collect('DETAIL').status=='COMPLETED'
        second=collect('REOFFER')
        assert second.status=='COMPLETED' and second.detail['reused']==1
        assert len(calls)==1
        assert writer.conn.execute('SELECT COUNT(*) FROM deep_captures').fetchone()[0]==2
        assert writer.conn.execute('SELECT COUNT(*) FROM contractor_memberships').fetchone()[0]==1
        assert collect('DETAIL').detail['skipped']==1
        assert collect('REOFFER').detail['skipped']==1
        assert len(calls)==1
        assert collect('REOFFER',True).status=='COMPLETED'
        assert len(calls)==2
    finally:
        writer.close()


def test_expired_checkpoint_is_not_implicitly_refetched(tmp_path):
    writer=seed_records(tmp_path)
    calls=[]
    def fetch(_family,key):
        calls.append(key)
        return {'notifyNo':key,'notifyVersion':'00','bidForm':'CGTTRG'}
    def collect():
        return run_deep_batch(writer,run_id='reuse',family='TBMT',lane='DETAIL',scope={'record_ids':[1]},fetcher=fetch,spec_provider=public_spec)
    try:
        collect()
        due=(datetime.now(UTC)-timedelta(days=1)).isoformat().replace('+00:00','Z')
        writer.conn.execute('UPDATE deep_captures SET refresh_due_at_utc=?',(due,))
        assert collect().detail['skipped']==1 and len(calls)==1
    finally:
        writer.close()


def test_parser_upgrade_uses_saved_response_without_network(tmp_path):
    writer=seed_records(tmp_path)
    def fetch(_family,key):return {'notifyNo':key,'notifyVersion':'00','bidForm':'CGTTRG'}
    try:
        run_deep_batch(writer,run_id='first',family='TBMT',lane='DETAIL',scope={'record_ids':[1]},fetcher=fetch,spec_provider=public_spec)
        writer.conn.execute('UPDATE deep_capture_parse_metadata SET parser_version=1')
        writer.conn.execute('UPDATE deep_task_metadata SET parser_version=1')
        def no_network(*_):raise AssertionError('parser upgrade must use raw')
        result=run_deep_batch(writer,run_id='upgrade',family='TBMT',lane='DETAIL',scope={'record_ids':[1]},fetcher=no_network,spec_provider=public_spec)
        assert result.status=='COMPLETED' and result.detail['reused']==1
        from santhau_studio.collectors.cgttrg import PARSER_VERSION
        assert writer.conn.execute('SELECT MAX(parser_version) FROM deep_capture_parse_metadata').fetchone()[0]==PARSER_VERSION
    finally:
        writer.close()
