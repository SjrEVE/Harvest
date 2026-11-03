"""Owner-gated, one-object deep probe. Never grants a live lane or COMPLETE coverage.

Manifest: {family,lane,endpoint,request_body,spec:{DeepLaneSpec fields}}.
plan validates the manifest without opening a socket. probe additionally requires explicit
--execute and SANTHAU_CANARY_APPROVED; session endpoints stay blocked until B7 is satisfied.
"""
import argparse
import json
import os
from contextlib import closing
from pathlib import Path

from santhau_studio.collectors.budget import RequestBudget
from santhau_studio.collectors.deep import DeepLaneSpec, parse_detail
from santhau_studio.collectors.http_client import FetchLane
from santhau_studio.collectors.source_registry import FAMILY_ORDER
from santhau_studio.domain.redaction import redact_payload
from santhau_studio.storage.warehouse import WarehouseWriter


def prepare(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('family') not in FAMILY_ORDER or value.get('lane') not in ('DETAIL','HSMT','REOFFER','KQLCNT_FULL','BOQ'):
        raise ValueError('Unsupported family/lane')
    FetchLane._check_url(value['endpoint'])
    if redact_payload(value) != value:
        raise ValueError('Manifest must not contain credentials or credential URLs')
    spec = DeepLaneSpec(family=value['family'],lane=value['lane'],**value['spec'])
    if spec.requires_session:
        raise ValueError('BLOCKED: B7_SESSION_HEADER_CONTRACT_UNVERIFIED')
    if not isinstance(value.get('request_body'),(dict,list)):
        raise ValueError('Explicit request_body is required')
    if len(json.dumps(value)) > 65536:
        raise ValueError('Manifest exceeds 64 KiB')
    return value,spec


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('plan','probe'))
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--data-root',type=Path)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args(argv)
    try:
        value,spec=prepare(args.manifest)
        plan={'family':value['family'],'lane':value['lane'],'endpoint':value['endpoint'],
              'state':'PROBE_UNVERIFIED','max_requests':3,'max_seconds':90,'max_records':1,
              'promotes_rule':False,'promotes_collection':False}
        if args.command=='plan':
            print(json.dumps(plan))
            return 0
        if not args.execute or not os.environ.get('SANTHAU_CANARY_APPROVED') or args.data_root is None:
            raise ValueError('BLOCKED: explicit owner approval, --execute and --data-root required')
        with FetchLane(url=value['endpoint'],source_key='egp_home',budget=RequestBudget(3,90)) as lane:
            result=lane.post(value['request_body'])
        parsed=parse_detail(spec,result.payload)
        with closing(WarehouseWriter.open(args.data_root,create_schema=True,role='deep-canary')) as writer:
            raw=writer.store_response(result.payload)
            writer.register_object(raw)
            receipt={**plan,'response_sha256':raw.sha256,'shape_problems':list(parsed.problems),
                     'note':'Owner review of endpoint, selectors and completeness is still required'}
            writer.conn.execute("INSERT OR REPLACE INTO settings(key,value_json,updated_at_utc) VALUES(?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
                ('deep-probe:'+value['family']+':'+value['lane'],json.dumps(receipt)))
        print(json.dumps(receipt))
        return 0
    except (ValueError,OSError,RuntimeError) as exc:
        from santhau_studio.collectors.network_policy import scrub_exception
        print(json.dumps({'status':'BLOCKED','reason':scrub_exception(exc)}))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
