from contextlib import closing

from test_search_filters import database

from santhau_studio.storage.serving import build_serving_db
from santhau_studio.storage.warehouse import WarehouseWriter


def test_large_history_pages_and_raw_view(tmp_path):
    backend = database(tmp_path)
    with closing(WarehouseWriter.open(backend.data_root)) as writer:
        for seq in range(2, 1002):
            writer.conn.execute(
                "INSERT INTO source_record_versions(source_record_id,content_version_seq,content_hash,"
                "change_kind,fetched_at_utc,normalize_schema,normalized_json,introducing_record_sha)"
                " SELECT source_record_id,?,content_hash,change_kind,fetched_at_utc,normalize_schema,normalized_json,introducing_record_sha"
                " FROM source_record_versions WHERE source_record_id=1 AND content_version_seq=1", (seq,))
    assert build_serving_db(backend.data_root / 'warehouse.sqlite', backend.serving_path).published
    result = backend.record_detail(1)
    assert len(result['versions']) == 50
    assert result['history_counts']['versions'] == 1001
    page = backend.record_history({'record_id': 1, 'kind': 'versions', 'before': 952})
    assert len(page['rows']) == 50
    assert page['rows'][0]['content_version_seq'] == 951
    assert backend.record_history({'record_id': 1, 'kind': 'settings'})['error_code'] == 'INVALID_HISTORY'
    raw = backend.raw_object(result['raw_revisions'][0]['record_object_sha'])
    assert raw['verified'] and 'notifyNo' in raw['text']
    assert backend.raw_object('../../x')['error_code'] == 'INVALID_HASH'

