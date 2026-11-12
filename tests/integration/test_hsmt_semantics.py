import json
from datetime import UTC, datetime

import pytest
from test_deep_run import seed_records

from santhau_studio.collectors.cgttrg import public_spec
from santhau_studio.collectors.deep import parse_detail
from santhau_studio.storage.deep import record_deep_capture
from santhau_studio.storage.public_deep import task_state
from santhau_studio.workers.deep_run import run_deep_batch


def form(code, value, notice="IB-0"):
    return {
        "notifyNo": notice,
        "notifyId": "synthetic-notify",
        "notifyVersion": "00",
        "bidForm": "CGTTRG",
        "formCode": "BD.DT.02." + code,
        "formValue": json.dumps(value),
        "formContent": "[]",
    }


def aux():
    return form(
        "2102",
        {
            "fileTech": {"fileId": "tech-1", "fileName": "technical.pdf"},
            "sharedFiles": ["opaque-reference"],
            "attachFiles": None,
        },
    )


@pytest.mark.parametrize("code", ["1957", "1960", "2034", "2035", "2036", "2037"])
@pytest.mark.parametrize("reversed_forms", [False, True])
def test_auxiliary_form_is_warning_and_does_not_hide_structured_goods(code, reversed_forms):
    forms = [form(code, {"Table": [{"name": "Synthetic item", "qty": 1, "uom": "unit"}]}), aux()]
    if reversed_forms:
        forms.reverse()
    parsed = parse_detail(public_spec("TBMT", "HSMT"), {"bidoInvBiddingDTO": forms})
    assert not parsed.problems
    assert len(parsed.unallotted_goods) == 1
    assert parsed.capability == "STRUCTURED_ITEMS" and parsed.warnings
    assert parsed.documents[0]["source_file_id"] == "tech-1"
    assert parsed.documents[0]["download_status"] == "REFERENCE_ONLY"


def test_document_only_form_has_references_without_claiming_goods_complete(tmp_path):
    payload = {
        "bidoInvBiddingDTO": [
            form(
                "3152",
                {
                    "fileTech": {"fileId": "technical", "fileName": "technical.pdf"},
                    "sharedFiles": [],
                },
            ),
            form(
                "3153",
                {"fileDoc": {"fileId": "dossier", "fileName": "dossier.pdf"}, "sharedFiles": []},
            ),
        ]
    }
    parsed = parse_detail(public_spec("TBMT", "HSMT"), payload)
    assert not parsed.problems and parsed.capability == "DOCUMENT_ONLY"
    assert not parsed.lots and not parsed.unallotted_goods and len(parsed.documents) == 2
    writer = seed_records(tmp_path)
    try:
        writer.register_run("file-only", "file-only", "HSMT")
        rid = writer.conn.execute(
            "SELECT id FROM source_records WHERE natural_key='IB-0'"
        ).fetchone()[0]
        result = run_deep_batch(
            writer,
            run_id="file-only",
            family="TBMT",
            lane="HSMT",
            scope={"record_ids": [rid]},
            fetcher=lambda _family, _key: payload,
            spec_provider=public_spec,
        )
        assert result.detail["source_empty"] == 0
        assert (
            writer.conn.execute("SELECT completeness FROM deep_captures").fetchone()[0] == "PARTIAL"
        )
        assert (
            writer.conn.execute(
                "SELECT capability FROM deep_task_metadata WHERE source_record_id=?", (rid,)
            ).fetchone()[0]
            == "DOCUMENT_ONLY"
        )
        assert (
            writer.conn.execute(
                "SELECT COUNT(*) FROM documents WHERE object_sha IS NULL"
            ).fetchone()[0]
            == 2
        )
        assert writer.conn.execute("SELECT COUNT(*) FROM goods_rows").fetchone()[0] == 0
    finally:
        writer.close()


@pytest.mark.parametrize("invalid", [{}, {"Table": {}}, {"Table": [{"qty": "bad"}]}])
def test_bad_structured_form_is_not_downgraded_to_auxiliary_warning(invalid):
    payload = {
        "bidoInvBiddingDTO": [
            form("2034", invalid),
            form("2035", {"Table": [{"name": "valid", "qty": 1}]}),
        ]
    }
    assert parse_detail(public_spec("TBMT", "HSMT"), payload).problems


def test_unverified_3150_table_does_not_become_nameless_goods():
    payload={"bidoInvBiddingDTO":[form("3150",{"Table":[{"description":"Synthetic work", "requirement":"Synthetic requirement", "qty":"2", "uom":"unit"}]}),
                                    form("3152",{"fileTech":{"fileId":"technical","fileName":"technical.pdf"}})]}
    parsed=parse_detail(public_spec("TBMT","HSMT"),payload)
    assert not parsed.unallotted_goods and not parsed.lots
    assert parsed.capability == "UNVERIFIED_FORM" and parsed.warnings
    assert len(parsed.documents)==1


def test_raw_replay_is_dry_by_default_and_versioned_without_network(tmp_path, monkeypatch):
    from santhau_studio.collectors import cgttrg
    from santhau_studio.workers.deep_replay import replay

    writer = seed_records(tmp_path)
    rid = writer.conn.execute("SELECT id FROM source_records WHERE natural_key='IB-0'").fetchone()[
        0
    ]
    payload = {"bidoInvBiddingDTO": [form("2034", {"Table": [{"name": "fixed", "qty": 1}]}), aux()]}
    old_time = datetime(2026, 11, 1, tzinfo=UTC)
    writer.register_run("old-parse", "old-parse", "HSMT")
    with monkeypatch.context() as patch:
        patch.setattr(cgttrg, "PARSER_VERSION", 1)
        capture = record_deep_capture(
            writer,
            run_id="old-parse",
            source_record_id=rid,
            spec=public_spec("TBMT", "HSMT"),
            completeness="PARTIAL",
            response_payload=payload,
            detail=payload,
            now=old_time,
            parse_error="FORM_1_TABLE_MISSING_OR_INVALID",
        )
        task_state(
            writer.conn,
            rid,
            "HSMT",
            "PARSE_ERROR",
            capture_id=capture.capture_id,
            response_sha=capture.response_object_sha,
            error="FORM_1_TABLE_MISSING_OR_INVALID",
        )
    writer.close()
    preview = replay(tmp_path)
    assert preview["dry_run"] and preview["eligible"] == 1 and preview["network_requests"] == 0
    writer = seed_reader(tmp_path)
    assert writer.execute("SELECT COUNT(*) FROM goods_rows").fetchone()[0] == 0
    writer.close()
    result = replay(tmp_path, execute=True)
    assert result["applied"] == 1 and result["network_requests"] == 0
    writer = seed_reader(tmp_path)
    assert writer.execute("SELECT COUNT(*) FROM goods_rows").fetchone()[0] == 1
    assert writer.execute("SELECT COUNT(*) FROM deep_capture_history").fetchone()[0] == 2
    assert (
        writer.execute("SELECT response_object_sha FROM deep_captures").fetchone()[0]
        == capture.response_object_sha
    )
    assert (
        writer.execute("SELECT captured_at_utc FROM deep_captures")
        .fetchone()[0]
        .startswith("2026-11-01")
    )
    writer.close()
    assert replay(tmp_path, execute=True)["applied"] == 0


def seed_reader(root):
    from santhau_studio.storage.warehouse import connect_read_only

    return connect_read_only(root / "warehouse.sqlite")


def test_replay_keeps_identity_mismatch_quarantined(tmp_path, monkeypatch):
    from santhau_studio.collectors import cgttrg
    from santhau_studio.workers.deep_replay import replay

    writer = seed_records(tmp_path)
    rid = writer.conn.execute("SELECT id FROM source_records WHERE natural_key='IB-0'").fetchone()[0]
    payload = {"bidoInvBiddingDTO": [form("2034", {"Table": [{"name": "wrong notice", "qty": 1}]}, notice="IB-OTHER")]}
    writer.register_run("wrong-old", "wrong-old", "HSMT")
    with monkeypatch.context() as patch:
        patch.setattr(cgttrg, "PARSER_VERSION", 1)
        capture = record_deep_capture(writer, run_id="wrong-old", source_record_id=rid, spec=public_spec("TBMT", "HSMT"),
            completeness="PARTIAL", response_payload=payload, detail=payload, now=datetime.now(UTC), parse_error="NOTICE_IDENTITY_MISMATCH")
        task_state(writer.conn, rid, "HSMT", "PARSE_ERROR", capture_id=capture.capture_id, response_sha=capture.response_object_sha,
                   error="NOTICE_IDENTITY_MISMATCH")
    writer.close()
    result = replay(tmp_path, execute=True)
    assert result["repaired"] == 0 and result["network_requests"] == 0
    conn = seed_reader(tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM goods_rows").fetchone()[0] == 0
    assert conn.execute("SELECT state FROM deep_tasks WHERE source_record_id=?", (rid,)).fetchone()[0] == "PARSE_ERROR"
    assert conn.execute("SELECT COUNT(*) FROM deep_capture_history").fetchone()[0] == 2
    conn.close()
    assert replay(tmp_path, execute=True)["applied"] == 0
