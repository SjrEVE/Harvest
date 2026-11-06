"""Lane-owned projections and checkpoints for verified public captures."""

import json
from datetime import UTC, datetime

from santhau_studio.domain.identity import canonical_bytes
from santhau_studio.storage.projection_evidence import merge_evidence, retire_evidence


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def task_state(
    conn,
    record_id,
    lane,
    state,
    *,
    listing_sha=None,
    response_sha=None,
    capture_id=None,
    error=None,
    provenance=None,
):
    conn.execute(
        "INSERT INTO deep_tasks VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(source_record_id,lane) DO UPDATE SET state=excluded.state,"
        "listing_object_sha=excluded.listing_object_sha,response_object_sha=excluded.response_object_sha,"
        "capture_id=COALESCE(excluded.capture_id,deep_tasks.capture_id),updated_at_utc=excluded.updated_at_utc,"
        "error=excluded.error,provenance_json=excluded.provenance_json",
        (
            record_id,
            lane,
            state,
            listing_sha,
            response_sha,
            capture_id,
            datetime.now(UTC).isoformat(),
            error,
            dumps(provenance or {}),
        ),
    )


def write_documents(conn, record_id, documents, evidence):
    for doc in documents:
        name = doc["name"]
        old = conn.execute(
            "SELECT object_sha,provenance_json,json_extract(provenance_json,'$.source_file_id') file_id FROM documents WHERE source_record_id=? AND document_kind=? AND name=?",
            (record_id, doc["document_kind"], name),
        ).fetchone()
        if old and old["file_id"] and old["file_id"] != doc.get("source_file_id"):
            name = name + " [" + str(doc["source_file_id"]) + "]"
            old = conn.execute("SELECT object_sha,provenance_json FROM documents WHERE source_record_id=? AND document_kind=? AND name=?",
                (record_id, doc["document_kind"], name)).fetchone()
        conn.execute(
            "INSERT INTO documents(source_record_id,document_kind,name,provenance_json) VALUES(?,?,?,?) "
            "ON CONFLICT(source_record_id,document_kind,name) DO UPDATE SET provenance_json=excluded.provenance_json",
            (record_id, doc["document_kind"], name,
             merge_evidence(old["provenance_json"] if old else None, json.loads(evidence({**doc, "reference_current": True})))),
        )


def write_projection(conn, writer, record_id, spec, parsed, capture_id, seq, response_sha):
    provenance = {
        "lane": spec.lane,
        "endpoint_kind": spec.endpoint_kind,
        "capture_id": capture_id,
        "capture_seq": seq,
        "response_object_sha": response_sha,
        "source_version_json": parsed.source_version_json,
        "problems": list(parsed.problems),
        "warnings": list(parsed.warnings), "capability": parsed.capability, "parser_version": spec.parser_version,
    }

    def evidence(extra=None):
        return dumps({**provenance, **(extra or {})})

    owner = (record_id, spec.lane, spec.endpoint_kind)
    clause = "source_record_id=? AND json_extract(provenance_json,'$.lane')=? AND json_extract(provenance_json,'$.endpoint_kind')=?"
    retire_evidence(conn, record_id, capture_id)
    write_documents(conn, record_id, parsed.documents, evidence)
    if spec.parser_kind == "HSMT":
        conn.execute("DELETE FROM goods_rows WHERE " + clause, owner)
        conn.execute("DELETE FROM lots WHERE " + clause, owner)
        ordinal = conn.execute(
            "SELECT COALESCE(MAX(row_ordinal),-1)+1 FROM goods_rows WHERE source_record_id=?",
            (record_id,),
        ).fetchone()[0]

        def goods(rows, lot_id):
            nonlocal ordinal
            for row in rows:
                conn.execute(
                    "INSERT INTO goods_rows(lot_id,source_record_id,row_ordinal,name,specification,unit,quantity_presence,quantity,quantity_raw,provenance_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        lot_id,
                        record_id,
                        ordinal,
                        row.name,
                        row.specification,
                        row.unit,
                        row.quantity_presence,
                        row.quantity,
                        row.quantity_raw,
                        evidence(
                            {
                                "raw_row": row.raw,
                                "source_ordinal": row.row_ordinal,
                                "form_schemas": parsed.hsmt.get("forms", []),
                            }
                        ),
                    ),
                )
                ordinal += 1

        for lot in parsed.lots:
            # Do not replace another lane's lot just because its external code matches.
            occupied = conn.execute(
                "SELECT id FROM lots WHERE source_record_id=? AND lot_no_raw=?",
                (record_id, lot.lot_no_raw),
            ).fetchone()
            if occupied:
                raise ValueError("LOT_OWNED_BY_ANOTHER_CAPTURE")
            lot_id = conn.execute(
                "INSERT INTO lots(source_record_id,lot_no_raw,lot_label,lot_version,goods_count,provenance_json) VALUES(?,?,?,?,?,?)",
                (
                    record_id,
                    lot.lot_no_raw,
                    lot.lot_label,
                    lot.lot_version,
                    len(lot.goods),
                    evidence({"raw_row": lot.raw}),
                ),
            ).lastrowid
            goods(lot.goods, lot_id)
        goods(parsed.unallotted_goods, None)
    else:
        for member in parsed.members:
            ref = writer.store.put(canonical_bytes(member.raw))
            writer.register_object(ref)
            # The same offeror can be observed by DETAIL and REOFFER; preserve lane ownership.
            exists = conn.execute(
                "SELECT id,provenance_json FROM contractor_memberships WHERE source_record_id=? AND party_object_sha=? AND role=?",
                (record_id, ref.sha256, member.role),
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO contractor_memberships(source_record_id,party_object_sha,party_name,party_tax_id_presence,party_tax_id,role,membership_share_presence,membership_share,ranking,provenance_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        record_id,
                        ref.sha256,
                        member.party_name,
                        member.party_tax_id_presence,
                        member.party_tax_id,
                        member.role,
                        member.membership_share_presence,
                        member.membership_share,
                        None,
                        evidence({"raw": member.raw}),
                    ),
                )
            else:
                conn.execute("UPDATE contractor_memberships SET provenance_json=? WHERE id=?",
                    (merge_evidence(exists["provenance_json"], {**provenance, "raw": member.raw}), exists["id"]))
        record = conn.execute(
            "SELECT source_key,family,natural_key FROM source_records WHERE id=?", (record_id,)
        ).fetchone()
        for relation in parsed.relations:
            candidates = conn.execute(
                "SELECT id,family,natural_key,version_raw FROM source_records WHERE source_key=? AND family=? AND natural_key=?",
                (record["source_key"], relation["family"], relation["key"]),
            ).fetchall()
            if relation.get("version") is not None:
                candidates = [r for r in candidates if r["version_raw"] == relation["version"]]
            if len(candidates) != 1:
                continue  # Missing/ambiguous targets remain in raw capture; no guessed edge.
            target = candidates[0]
            ids = [f"source-record:{target['id']}", f"source-record:{record_id}"]
            for entity, kind, label, rid in (
                (ids[0], "PLAN", target["natural_key"], target["id"]),
                (ids[1], "TENDER_PACKAGE", record["natural_key"], record_id),
            ):
                conn.execute(
                    "INSERT OR IGNORE INTO canonical_entities VALUES(?,?,?,'EXACT',?)",
                    (entity, kind, label, datetime.now(UTC).isoformat().replace("+00:00", "Z")),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO entity_source_links VALUES(?,?,'SAME_AS','EXACT_NATURAL_KEY',?,?)",
                    (
                        entity,
                        rid,
                        evidence(relation),
                        datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    ),
                )
            existing = conn.execute(
                "SELECT id,evidence_json FROM relation_edges WHERE from_entity=? AND to_entity=? AND relation=?",
                (*ids, relation["relation"]),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO relation_edges(from_entity,to_entity,relation,evidence_json,confidence) VALUES(?,?,?,?, 'EXACT')",
                    (*ids, relation["relation"], evidence(relation)),
                )
            else:
                conn.execute(
                    "UPDATE relation_edges SET evidence_json=? WHERE id=?",
                    (merge_evidence(existing["evidence_json"], {**provenance, **relation}), existing["id"]),
                )
