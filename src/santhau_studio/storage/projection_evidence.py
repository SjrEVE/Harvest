"""Ownership of shared derived rows; immutable captures remain the source of truth."""

import json


def confirmations(value):
    if "confirmations" in value:
        return value["confirmations"]
    return [value] if value else []


def merge_evidence(previous, current):
    prior = json.loads(previous) if previous else {}
    evidence = [p for p in confirmations(prior) if p.get("capture_id") != current["capture_id"]]
    return json.dumps(
        {**current, "confirmations": [*evidence, current]},
        ensure_ascii=False,
        sort_keys=True,
    )


def retire_evidence(conn, record_id, capture_id):
    """Remove only this capture's assertions. Unowned/manual assertions survive."""
    for table, column, predicate, target in (
        ("contractor_memberships", "provenance_json", "source_record_id=?", record_id),
        ("documents", "provenance_json", "source_record_id=?", record_id),
        ("relation_edges", "evidence_json", "to_entity=?", f"source-record:{record_id}"),
    ):
        for row in conn.execute(f"SELECT * FROM {table} WHERE {predicate}", (target,)).fetchall():
            original = json.loads(row[column])
            before = confirmations(original)
            remaining = [p for p in before if p.get("capture_id") != capture_id]
            if len(remaining) == len(before):
                continue
            if remaining:
                value = {**remaining[-1], "confirmations": remaining}
            elif table == "documents" and row["object_sha"]:
                # A detached reference never authorizes deleting a downloaded file.
                value = {**original, "confirmations": [], "reference_current": False}
            else:
                conn.execute(f"DELETE FROM {table} WHERE id=?", (row["id"],))
                continue
            conn.execute(
                f"UPDATE {table} SET {column}=? WHERE id=?",
                (json.dumps(value, ensure_ascii=False, sort_keys=True), row["id"]),
            )
