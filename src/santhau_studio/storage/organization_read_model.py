"""Producer-owned, additive organization membership capability for serving v2."""
from __future__ import annotations

import sqlite3

_MEMBERS = """FROM source_records r
    JOIN source_record_versions v ON v.source_record_id=r.id
      AND v.content_version_seq=r.current_content_version,
    json_each(v.normalized_json,'$.winningCode') member
    WHERE member.type='text'"""
_MISSING = """ AND NOT EXISTS (
    SELECT 1 FROM market_relation_edges e
    WHERE e.from_record_id=r.id AND e.to_record_id IS NULL AND e.source_record_id=r.id
      AND e.relation='EXECUTED_BY' AND e.basis='winningCode'
      AND e.join_key=member.value AND e.party_tax_id=member.value)"""
_COUNT = "SELECT COUNT(*) FROM market_relation_edges WHERE relation='EXECUTED_BY' AND basis='winningCode'"


def validate_organizations(conn: sqlite3.Connection, snapshot_built_at_utc: str) -> dict:
    """Accept the capability only with exact marker/count/index and membership parity."""
    marker = conn.execute("SELECT organization_schema_version,snapshot_built_at_utc,source_projection_version,"
                          "winning_membership_count FROM serving_organization_meta WHERE singleton_id=1").fetchone()
    count = conn.execute(_COUNT).fetchone()[0]
    if marker is None or tuple(marker) != (1, snapshot_built_at_utc, 2, count):
        raise ValueError('organization projection metadata is invalid')
    index = [(r[2], r[3]) for r in conn.execute('PRAGMA index_xinfo(ix_mre_party_tax_id)') if r[5]]
    if index != [('party_tax_id', 0)]:
        raise ValueError('organization membership index is invalid')
    if conn.execute('SELECT 1 ' + _MEMBERS + _MISSING + ' LIMIT 1').fetchone():
        raise ValueError('organization normalized membership parity failed')
    return {'organization_schema_version': 1, 'snapshot_built_at_utc': snapshot_built_at_utc,
            'source_projection_version': 2, 'winning_membership_count': count}


def materialise_organizations(conn: sqlite3.Connection, *, snapshot_built_at_utc: str,
                             source_projection_version: int = 2) -> dict:
    """Keep raw/deep edges and add the exact text members used by legacy membership."""
    if source_projection_version != 2:
        raise ValueError('organization projection requires source projection v2')
    conn.execute("INSERT OR IGNORE INTO market_relation_edges "
        "(source_key,from_record_id,to_record_id,source_record_id,relation,basis,join_key,party_tax_id,content_hash,raw_hash,provenance_json) "
        "SELECT DISTINCT r.source_key,r.id,NULL,r.id,'EXECUTED_BY','winningCode',member.value,member.value,"
        "r.current_content_hash,r.current_record_object_sha,"
        "json_object('basis','winningCode','party_tax_id',member.value,'source_record_id',r.id,"
        "'content_hash',r.current_content_hash,'raw_hash',r.current_record_object_sha) " + _MEMBERS + _MISSING)
    conn.execute('ANALYZE market_relation_edges')
    conn.execute('CREATE TABLE IF NOT EXISTS serving_organization_meta('
        'singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),'
        'organization_schema_version INTEGER NOT NULL CHECK(organization_schema_version=1),'
        'snapshot_built_at_utc TEXT NOT NULL,source_projection_version INTEGER NOT NULL CHECK(source_projection_version=2),'
        'winning_membership_count INTEGER NOT NULL CHECK(winning_membership_count>=0))')
    # Validate completeness before declaring it; a partial build never receives a marker.
    if conn.execute('SELECT 1 ' + _MEMBERS + _MISSING + ' LIMIT 1').fetchone():
        raise ValueError('organization normalized membership parity failed')
    count = conn.execute(_COUNT).fetchone()[0]
    conn.execute('INSERT OR REPLACE INTO serving_organization_meta VALUES(1,1,?,2,?)',
                 (snapshot_built_at_utc, count))
    return validate_organizations(conn, snapshot_built_at_utc)
