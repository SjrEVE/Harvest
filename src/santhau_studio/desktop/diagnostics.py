"""Read-only evidence details; never upgrades a legacy or reachability claim."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from santhau_studio.collectors.canary import canary_state
from santhau_studio.collectors.source_registry import FAMILY_ORDER, SOURCE_EGP_HOME


def source_evidence(conn, warehouse_db=None):
    """`warehouse_db` is the warehouse.sqlite PATH (audit tables live there since RC3-8)."""
    access = conn.execute('SELECT access_state,notes FROM source_registry WHERE source_key=?',(SOURCE_EGP_HOME,)).fetchone()
    audit_conn = None
    if warehouse_db is not None:
        from santhau_studio.storage.warehouse import connect_read_only

        audit_conn = connect_read_only(Path(warehouse_db))
        audit_conn.row_factory = conn.row_factory
    lookup = audit_conn or conn
    try:
        return _source_evidence(lookup, access)
    finally:
        if audit_conn is not None:
            audit_conn.close()


def _source_evidence(lookup, access):
    families = []
    for family in FAMILY_ORDER:
        state = canary_state(lookup, SOURCE_EGP_HOME, family)
        state.update(family=family, source_key=SOURCE_EGP_HOME)
        state['blocked_reason'] = None if state['rule_verified'] else 'BOUNDARY_RULE_UNVERIFIED: cần owner duyệt canary và hợp đồng biên'
        if access and access[0] in ('BLOCKED','DISABLED','NEEDS_USER_SESSION'):
            state['blocked_reason'] = access[0] + ': ' + (access[1] or 'SOURCE_ACCESS_NOT_GRANTED')
        families.append(state)
    events = [dict(r) for r in lookup.execute('SELECT family,reason_kind,detail_scrubbed,observed_at_utc FROM source_access_events ORDER BY id DESC LIMIT 50')]
    return {'families': families, 'access_events': events}


def legacy_claims(conn, limit=50):
    return [json.loads(row[0]) for row in conn.execute(
        "SELECT value_json FROM settings WHERE key LIKE 'legacy:coverage:%' ORDER BY key LIMIT ?", (limit,))]


def coverage_heatmap(conn):
    from santhau_studio.domain.coverage import vn_calendar_day
    from santhau_studio.storage.coverage import resolve
    latest = conn.execute('SELECT MAX(interval_to_utc) FROM coverage_intervals').fetchone()[0]
    if not latest:
        return []
    last = (datetime.fromisoformat(latest.replace('Z','+00:00')) - timedelta(seconds=1)).astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).date()
    cells = []
    for family in FAMILY_ORDER:
        for offset in range(6,-1,-1):
            day = last - timedelta(days=offset)
            interval = vn_calendar_day(day)
            resolved = resolve(conn,SOURCE_EGP_HOME,family,interval.from_utc,interval.to_utc)
            cells.append({'family':family,'day':day.isoformat(),'status':resolved.status,
                          'granularity':resolved.granularity,'proof_ids':resolved.proof_ids,'reasons':resolved.reasons})
    return cells


def proof(conn, proof_id):
    row = conn.execute('SELECT * FROM coverage_proofs WHERE id=?', (int(proof_id),)).fetchone()
    if row is None:
        return {'error': 'Không có proof cho interval này.'}
    rule = conn.execute('SELECT * FROM filter_boundary_rules WHERE id=?', (row['boundary_rule_id'],)).fetchone()
    return {'proof': dict(row), 'boundary_rule': dict(rule) if rule else None}


def log_tail(root):
    from santhau_studio.collectors.network_policy import scrub_exception
    result = []
    for path in sorted((root / 'logs').glob('*.log'), key=lambda p:p.stat().st_mtime, reverse=True)[:5]:
        with path.open('rb') as f:
            f.seek(max(0, path.stat().st_size - 12000))
            text = f.read(12000).decode('utf-8', errors='replace')
        result.append({'file': path.name, 'tail': scrub_exception(Exception(text))})
    return result
