"""Explicit synthetic native-app acceptance profile. No network client is constructed."""
import hashlib
import json
import time
from contextlib import closing
from pathlib import Path

from santhau_studio.collectors.deep import DeepLaneSpec
from santhau_studio.collectors.egp_home import Page
from santhau_studio.collectors.source_registry import FAMILY_ORDER, SOURCES
from santhau_studio.storage.serving import build_serving_db
from santhau_studio.storage.warehouse import WarehouseWriter

PROFILE = 'RC3_SYNTHETIC_OFFLINE'
ADAPTER = hashlib.sha256(PROFILE.encode()).hexdigest()
MARKER = 'offline-acceptance.json'


def enabled(root):
    try:
        return json.loads((Path(root) / MARKER).read_text()) == {'profile': PROFILE, 'root': str(Path(root).resolve())}
    except (OSError, ValueError):
        return False


def initialize(root):
    root = Path(root).resolve()
    if (root / 'warehouse.sqlite').exists() and not enabled(root):
        raise ValueError('Acceptance profile requires a new isolated Data Root')
    root.mkdir(parents=True, exist_ok=True)
    (root / MARKER).write_text(json.dumps({'profile': PROFILE, 'root': str(root)}))
    with closing(WarehouseWriter.open(root, create_schema=True)) as writer:
        writer.conn.execute("INSERT OR IGNORE INTO source_registry(source_key,display_name,endpoint_family,access_state,notes) VALUES('egp_home','SYNTHETIC OFFLINE','egp','ALLOWED_AND_VERIFIED',?)", (PROFILE,))
        writer.conn.execute("INSERT OR REPLACE INTO settings VALUES('evidence_class',?,'2026-11-04T00:00:00Z')", (json.dumps({'class': PROFILE, 'live_proof': False}),))
        for family in FAMILY_ORDER:
            writer.conn.execute("INSERT OR IGNORE INTO filter_boundary_rules(rule_key,rule_version,source_key,family,endpoint,adapter_sha,boundary_kind,precision,source_timezone,offset_minutes,conversion_params,canary_verified,verified_at_utc) VALUES(?,2,'egp_home',?,?,?,'CLOSED_LOCAL','DAY','Asia/Ho_Chi_Minh',420,?,1,'2026-11-04T00:00:00Z')",
                ('SYNTHETIC-' + family, family, SOURCES['egp_home'].endpoint, ADAPTER,
                 json.dumps({'to_minus':'1s','query_contract':'KQLCNT_RESULT_DATE_V2','date_field':'publicDateKqlcnt'})))
    return build_serving_db(root / 'warehouse.sqlite', root / 'serving.sqlite')


def builder(_writer, request):
    family = request.sources[0]['family']
    def pages(interval, _granularity, _window):
        from santhau_studio.collectors.source_registry import binding_for
        spec = binding_for(family).spec
        key_fields = {'TBMT':('notifyNo','notifyVersion'),'KQLCNT':('notifyNo','notifyVersion'),
            'HOP_DONG':('contractCode','contractVersion'),'KHLCNT':('planNo','planVersion'),'DU_AN':('pno','pversion'),'YCBG':('notifyNo','notifyVersion')}
        key, version = key_fields[family]
        for page in range(12):
            time.sleep(.18)
            records = [{key:f'SYNTH-{family}-{interval.from_utc:%Y%m%d}-{page:02d}-{i}',version:'00',
                'bidName':f'Gói thầu synthetic thiết bị {page}-{i}','name':f'Synthetic {page}-{i}',
                'pname':f'Synthetic {page}-{i}','ctName':f'Synthetic {page}-{i}',
                'investorName':'Đơn vị thử nghiệm offline','province':'Hà Nội','bidPrice':1000+i,'currency':'VND',
                'publicDate':'2026-11-01T02:00:00Z','fixture':PROFILE} for i in range(3)]
            if family == 'KQLCNT':
                for row in records:
                    row['publicDateKqlcnt'] = interval.from_utc.isoformat()
            assert spec.family == family
            yield Page(payload={'page':{'content':records,'totalElements':36}},page_no=page,rows=3,
                       has_more=page < 11,total_hint=36,total_hint_trusted=True,http_status=200,attempt_log=())
    return pages


def deep_spec(family, lane):
    return DeepLaneSpec(family=family,lane=lane,endpoint_kind='SYNTHETIC-OFFLINE',version_fields=('version',),
        lots_path='/lots',lot_label_fields=('name',),goods_path='/rows',goods_quantity_fields=('qty',),
        goods_unit_fields=('unit',),hsmt_columns=('name',))


def deep_fetch(_family, key):
    return {'version':'00','fixture':PROFILE,'name':key,'lots':[{'lotNo':'L1','name':'Synthetic lot',
        'rows':[{'name':'Thiết bị thử nghiệm','qty':2,'unit':'bộ'}]}]}
