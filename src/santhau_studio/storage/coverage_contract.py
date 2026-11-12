"""Semantic query/boundary identity for coverage and durable maintenance tasks."""

import hashlib
import json

from santhau_studio.collectors.source_registry import query_contract_matches

FIELDS = (
    "source_key",
    "family",
    "endpoint",
    "adapter_sha",
    "boundary_kind",
    "precision",
    "source_timezone",
    "offset_minutes",
    "conversion_params",
)


def signature(row):
    value = {key: row[key] for key in FIELDS}
    value["conversion_params"] = json.loads(value["conversion_params"] or "{}")
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def current(conn, source_key, family, *, endpoint=None, adapter_sha=None):
    rows = conn.execute(
        "SELECT * FROM filter_boundary_rules WHERE source_key=? AND family=? AND canary_verified=1 ORDER BY rule_version DESC,verified_at_utc DESC,id DESC",
        (source_key, family),
    )
    for row in rows:
        if endpoint is not None and row["endpoint"] != endpoint:
            continue
        if adapter_sha is not None and row["adapter_sha"] != adapter_sha:
            continue
        if query_contract_matches(family, row["conversion_params"]):
            result = dict(row)
            result["signature"] = signature(row)
            return result
    return None


def compatible(conn, rule_id, context):
    if context is None or rule_id is None:
        return False
    row = conn.execute("SELECT * FROM filter_boundary_rules WHERE id=?", (rule_id,)).fetchone()
    if row is None or not row["canary_verified"]:
        return False
    try:
        return signature(row) == context["signature"]
    except (ValueError, TypeError, KeyError):
        return False
