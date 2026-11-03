"""Milestone F: every screen's numbers, proven against a real seeded warehouse and snapshot.

The seed goes through the production writer and runner (records, versions, proof), then a few
tables the runner does not write (deep capture, lots, quarantine, gaps) are filled directly, and
`build_serving_db` publishes the read model the UI actually opens. From there each backend method
is asserted to surface the same facts — no fixture shortcuts inside the backend itself.
"""

from __future__ import annotations

import contextlib
import hashlib
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from santhau_studio.collectors.egp_home import Page
from santhau_studio.collectors.source_registry import SOURCE_EGP_HOME, SOURCES
from santhau_studio.domain.coverage import Interval, vn_calendar_day
from santhau_studio.storage import serving
from santhau_studio.storage.warehouse import WarehouseWriter
from santhau_studio.workers.runner import Runner

ENDPOINT = SOURCES[SOURCE_EGP_HOME].endpoint
ADAPTER_SHA = hashlib.sha256(b"ui-fixture").hexdigest()
DAY = vn_calendar_day(date(2026, 11, 1))
PAGES = 2
PER_PAGE = 5


def records(page_no: int) -> list[dict]:
    return [
        {"notifyNo": f"P{page_no}-{i}", "notifyVersion": "00", "bidName": f"Gói thầu mẫu {i}"}
        for i in range(PER_PAGE)
    ]


def portal(_interval: Interval, _granularity: str, _window) -> Iterator[Page]:
    for page_no in range(PAGES):
        content = records(page_no)
        yield Page(
            payload={"page": {"content": content, "totalElements": PAGES * PER_PAGE}},
            page_no=page_no,
            rows=len(content),
            has_more=page_no + 1 < PAGES,
            total_hint=PAGES * PER_PAGE,
            total_hint_trusted=True,
            http_status=200,
            attempt_log=(),
        )


@pytest.fixture()
def seeded(tmp_path: Path):
    """A data root with collected records, a proof, and the side tables the UI reads."""
    root = tmp_path / "data"
    writer = WarehouseWriter.open(root, create_schema=True, role="seed")
    try:
        writer.conn.execute(
            "INSERT INTO filter_boundary_rules (rule_key, rule_version, source_key, family, endpoint,"
            " adapter_sha, boundary_kind, precision, source_timezone, offset_minutes, conversion_params,"
            " canary_verified, verified_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("ui-tbmt", 1, SOURCE_EGP_HOME, "TBMT", ENDPOINT, ADAPTER_SHA,
             "CLOSED_LOCAL", "DAY", "Asia/Ho_Chi_Minh", 420, '{"to_minus":"1s"}', 1,
             "2026-10-31T00:00:00Z"),
        )
        writer.conn.execute(
            "INSERT INTO source_registry (source_key, display_name, endpoint_family, access_state)"
            " VALUES (?,?,?,?)",
            (SOURCE_EGP_HOME, "e-GP quốc gia", "egp", "ALLOWED_AND_VERIFIED"),
        )
        writer.conn.commit()

        writer.register_run("run-ui", "job-ui", "BACKFILL")
        runner = Runner(
            writer=writer, source_key=SOURCE_EGP_HOME, family="TBMT", endpoint=ENDPOINT,
            adapter_sha=ADAPTER_SHA, page_source=portal,
        )
        outcomes = runner.backfill("run-ui", DAY)
        assert all(outcome.status == "COMPLETE" for outcome in outcomes)

        writer.conn.execute(
            "INSERT INTO watermarks (source_key, family, watermark_to_utc, updated_at_utc)"
            " VALUES (?,?,?,?)",
            (SOURCE_EGP_HOME, "TBMT", "2026-11-02T00:00:00Z", "2026-11-02T00:00:00Z"),
        )
        writer.conn.commit()

        record = writer.conn.execute(
            "SELECT id, natural_key, identity_key FROM source_records ORDER BY id LIMIT 1"
        ).fetchone()
        record_id = int(record["id"])

        deep_response = writer.store_response({"detail": "v1"})
        deep_record = writer.store_response({"row": "v1"})
        writer.register_object(deep_response)
        writer.register_object(deep_record)
        writer.conn.execute(
            "INSERT INTO deep_captures (source_record_id, lane, endpoint_kind, completeness, captured_at_utc,"
            " refresh_due_at_utc, stale, stale_reason, source_version_json, response_object_sha,"
            " record_object_sha, content_hash, lane_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record_id, "DETAIL", "detail-v2", "PARTIAL", "2026-11-02T00:00:00Z", "2026-11-01T00:00:00Z",
             1, "SOURCE_VERSION_CHANGED", '{"v":"1"}', deep_response.sha256, deep_record.sha256,
             "ch1:" + "b" * 64, "PARTIAL"),
        )
        lot = writer.conn.execute(
            "INSERT INTO lots (source_record_id, lot_no_raw, lot_label, goods_count, provenance_json)"
            " VALUES (?,?,?,?,?)",
            (record_id, "L1", "Lô 1", 1, '{"source":"fixture"}'),
        ).lastrowid
        writer.conn.execute(
            "INSERT INTO goods_rows (lot_id, source_record_id, row_ordinal, name, unit, quantity_presence,"
            " quantity_raw, provenance_json) VALUES (?,?,?,?,?,?,?,?)",
            (lot, record_id, 0, "Máy tính", "bộ", "UNPARSABLE", "một bộ", '{"row":0}'),
        )
        party = writer.store_response({"party": "Công ty A"})
        writer.register_object(party)
        writer.conn.execute(
            "INSERT INTO contractor_memberships (source_record_id, party_object_sha, party_name,"
            " party_tax_id_presence, role, membership_share_presence, provenance_json)"
            " VALUES (?,?,?,?,?,?,?)",
            (record_id, party.sha256, "Công ty A", "MISSING", "WINNER", "MISSING", '{}'),
        )
        writer.conn.execute(
            "INSERT INTO documents (source_record_id, document_kind, name, provenance_json)"
            " VALUES (?,?,?,?)",
            (record_id, "HSMT", "hsmt.pdf", '{}'),
        )
        writer.conn.execute(
            "INSERT INTO notice_groups (id, family, group_kind, evidence_json, created_at_utc)"
            " VALUES (?,?,?,?,?)",
            ("g1", "TBMT", "PUBLICATION_VERSIONS", '{}', "2026-11-02T00:00:00Z"),
        )
        writer.conn.execute(
            "INSERT INTO notice_group_members (group_id, source_record_id, publication_label) VALUES (?,?,?)",
            ("g1", record_id, "01"),
        )
        writer.conn.execute(
            "INSERT INTO canonical_entities (id, entity_kind, label, resolution_state, created_at_utc)"
            " VALUES (?,?,?,?,?)",
            ("e1", "TENDER_PACKAGE", "Gói mẫu", "EXACT", "2026-11-02T00:00:00Z"),
        )
        writer.conn.execute(
            "INSERT INTO entity_source_links (entity_id, source_record_id, relation, method, evidence_json)"
            " VALUES (?,?,?,?,?)",
            ("e1", record_id, "SAME_AS", "MANUAL", '{}'),
        )

        page_obj = writer.store_response({"page": "payload"})
        writer.register_object(page_obj)
        attempt_id = writer.ensure_attempt(
            run_id="run-ui", family="TBMT", endpoint=ENDPOINT,
            interval_from="2026-11-01T00:00:00Z", interval_to="2026-11-02T00:00:00Z",
            page_no=0, attempt_no=1, outcome="OK", response=page_obj,
        )
        writer.conn.execute(
            "INSERT INTO quarantined_responses (run_id, attempt_id, response_object_sha, failure, detail,"
            " quarantined_at_utc) VALUES (?,?,?,?,?,?)",
            ("run-ui", attempt_id, page_obj.sha256, "EMPTY_BODY", "fixture", "2026-11-02T00:00:00Z"),
        )
        writer.conn.execute(
            "INSERT INTO unresolved_records (run_id, attempt_id, family, record_object_sha, record_locator,"
            " reason, raised_at_utc) VALUES (?,?,?,?,?,?,?)",
            ("run-ui", attempt_id, "TBMT", page_obj.sha256, "/0", "MISSING_NATURAL_KEY",
             "2026-11-02T00:00:00Z"),
        )
        writer.conn.execute(
            "INSERT INTO reconcile_gaps (run_id, source_key, family, interval_from_utc, interval_to_utc,"
            " kind, evidence_json, detected_at_utc) VALUES (?,?,?,?,?,?,?,?)",
            ("run-ui", SOURCE_EGP_HOME, "TBMT", "2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z",
             "HASH_DRIFT", '{"n":0}', "2026-11-02T00:00:00Z"),
        )
        writer.conn.execute(
            "INSERT INTO raw_objects (sha256, byte_len, codec, storage_rel_path, integrity_status,"
            " redaction_policy_version, created_at_utc) VALUES (?,?,?,?,?,?,?)",
            ("f" * 64, 1, "gzip", "raw/ff/bad.bin.gz", "CORRUPT", 1, "2026-11-02T00:00:00Z"),
        )
        writer.conn.execute(
            "INSERT OR REPLACE INTO settings (key, value_json, updated_at_utc) VALUES (?,?,?)",
            ("serving_stale", '{"reason":"fixture"}', "2026-11-02T00:00:00Z"),
        )
        writer.conn.commit()
        record_row = {"record_id": record_id, "natural_key": record["natural_key"]}
    finally:
        writer.close()  # the seed writer must release the root: the UI reads idleness as truthful

    result = serving.build_serving_db(root / "warehouse.sqlite", root / "serving.sqlite")
    assert result.published, result.reason
    yield {"root": root, **record_row}


def test_onboarding_reads_real_state(seeded) -> None:
    from santhau_studio.desktop.backend import StudioBackend

    backend = StudioBackend(seeded["root"])
    state = backend.onboarding()
    assert state["warehouse_present"] and state["serving_present"]
    assert state["worker"]["running"] is False
    assert state["snapshot"]["present"] is True
    assert state["free_bytes"] > 0


def test_validate_data_root_accepts_a_writable_directory(seeded, tmp_path: Path) -> None:
    from santhau_studio.desktop.backend import StudioBackend

    backend = StudioBackend(seeded["root"])
    result = backend.validate_data_root(str(tmp_path / "fresh-root"))
    assert result["ok"] and result["writable"]
    assert (tmp_path / "fresh-root").is_dir()


def test_control_center_counts_match_the_warehouse(seeded) -> None:
    from santhau_studio.desktop.backend import StudioBackend

    payload = StudioBackend(seeded["root"]).control_center()
    assert payload["snapshot"]["present"] is True
    family = next(entry for entry in payload["families"] if entry["family"] == "TBMT")
    assert family["records"] == PAGES * PER_PAGE
    assert family["versions"] == PAGES * PER_PAGE
    assert family["raw_revisions"] == PAGES * PER_PAGE
    assert family["last_verified_at_utc"] is not None


def test_sources_show_verified_rule_and_access_state(seeded) -> None:
    from santhau_studio.desktop.backend import StudioBackend

    payload = StudioBackend(seeded["root"]).sources()
    assert payload["registry"][0]["access_state"] == "ALLOWED_AND_VERIFIED"
    rule = payload["boundary_rules"][0]
    assert rule["canary_verified"] == 1 and rule["precision"] == "DAY"
    assert rule["boundary_kind"] == "CLOSED_LOCAL"


def test_coverage_reports_claimed_vs_walked(seeded) -> None:
    from santhau_studio.desktop.backend import StudioBackend

    payload = StudioBackend(seeded["root"]).coverage(None)
    summary = payload["summary"][0]
    assert summary["claimed"] == summary["walked"] == 1
    assert summary["complete_without_proof"] == 0
    interval = payload["intervals"][0]
    assert interval["status"] == "COMPLETE" and interval["proof_id"] is not None
    assert interval["distinct_ids"] == PAGES * PER_PAGE
    assert payload["watermarks"][0]["watermark_to_utc"] == "2026-11-02T00:00:00Z"


def test_search_folds_vietnamese_and_respects_family(seeded) -> None:
    from santhau_studio.desktop.backend import StudioBackend

    backend = StudioBackend(seeded["root"])
    hit = backend.search({"text": "gói thầu", "family": "TBMT"})
    assert hit["hits"], hit
    assert hit["hits"][0]["record_id"] > 0
    assert "Gói thầu" in hit["hits"][0]["title_display"]
    assert backend.search({"text": "gói", "family": "KHLCNT"})["hits"] == []
    assert backend.search({"text": ""})["note"] == "EMPTY_QUERY"


def test_record_detail_carries_versions_and_deep_rows(seeded) -> None:
    from santhau_studio.desktop.backend import StudioBackend

    detail = StudioBackend(seeded["root"]).record_detail(seeded["record_id"])
    assert detail["present"] is True
    assert detail["record"]["family"] == "TBMT"
    assert len(detail["versions"]) == 1
    assert len(detail["raw_revisions"]) == 1
    capture = detail["deep_captures"][0]
    assert capture["completeness"] == "PARTIAL" and capture["stale"] == 1
    assert detail["lots"][0]["lot_no_raw"] == "L1"
    goods = detail["goods_rows"][0]
    assert goods["quantity_presence"] == "UNPARSABLE" and goods["quantity"] is None
    assert detail["documents"][0]["name"] == "hsmt.pdf"
    assert detail["contractor_memberships"][0]["role"] == "WINNER"
    assert detail["notice_groups"][0]["group_id"] == "g1"
    assert detail["entity_links"][0]["entity_id"] == "e1"


def test_deep_queue_marks_refresh_need(seeded) -> None:
    from santhau_studio.desktop.backend import StudioBackend

    payload = StudioBackend(seeded["root"]).deep_queue()
    assert payload["summary"]["PARTIAL"] == 1 and payload["summary"]["stale"] == 1
    row = payload["rows"][0]
    assert row["stale"] == 1 and row["stale_reason"] == "SOURCE_VERSION_CHANGED"
    assert row["needs_refresh"] is True


def test_integrity_surfaces_damage_not_hides_it(seeded) -> None:
    from santhau_studio.desktop.backend import StudioBackend

    payload = StudioBackend(seeded["root"]).integrity()
    assert payload["raw_objects"].get("CORRUPT") == 1
    assert payload["quarantine"][0]["failure"] == "EMPTY_BODY"
    assert payload["unresolved"]["open"] == 1
    assert payload["gaps"][0]["kind"] == "HASH_DRIFT"
    assert payload["markers"][0]["key"] == "serving_stale"


def test_settings_lists_markers_and_versions(seeded) -> None:
    from santhau_studio.desktop.backend import StudioBackend

    payload = StudioBackend(seeded["root"]).settings()
    assert payload["snapshot"]["present"] is True
    assert any(row["key"] == "serving_stale" for row in payload["settings"])
    assert payload["app_version"]
    assert payload["backups"] == []


def test_enqueue_collection_publishes_request_document(seeded) -> None:
    from santhau_studio.desktop.backend import StudioBackend

    backend = StudioBackend(seeded["root"])
    result = backend.enqueue_collection({
        "job_kind": "BACKFILL",
        "family": "TBMT",
        "from": "2026-11-02T00:00:00Z",
        "to": "2026-11-03T00:00:00Z",
    })
    assert result["ok"] is True, result
    queued = list((seeded["root"] / "jobs" / "queued").glob("*.json"))
    assert len(queued) == 1 and result["job_id"] in queued[0].name
    jobs = backend.jobs()
    assert jobs["queued"][0]["job_id"] == result["job_id"]
    assert jobs["queued"][0]["families"] == ["TBMT"]


def test_enqueue_refuses_a_family_without_a_verified_rule(seeded) -> None:
    from santhau_studio.desktop.backend import StudioBackend

    backend = StudioBackend(seeded["root"])
    result = backend.enqueue_collection({
        "job_kind": "BACKFILL", "family": "KHLCNT",
        "from": "2026-11-02T00:00:00Z", "to": "2026-11-03T00:00:00Z",
    })
    assert result["ok"] is False
    assert "BOUNDARY_RULE_UNVERIFIED" in result["error"]
    assert not list((seeded["root"] / "jobs" / "queued").glob("*.json"))


def test_worker_command_without_a_worker_fails_closed(seeded) -> None:
    from santhau_studio.desktop.backend import StudioBackend

    backend = StudioBackend(seeded["root"])
    assert backend.worker_command("PAUSE", None)["ok"] is False
    assert backend.job_status("job-absent")["ok"] is False


def test_every_screen_payload_is_json_serialisable(seeded) -> None:
    import json

    from santhau_studio.desktop.backend import StudioBackend

    backend = StudioBackend(seeded["root"])
    payloads = [
        backend.onboarding(), backend.control_center(), backend.sources(), backend.jobs(),
        backend.coverage(None), backend.search({"text": "gói"}), backend.record_detail(seeded["record_id"]),
        backend.deep_queue(), backend.integrity(), backend.settings(),
    ]
    for payload in payloads:
        json.dumps(payload, ensure_ascii=False)


def test_serving_reader_never_writes(seeded) -> None:
    """The reader connection is query_only: a stray write must be refused by the connection."""
    from santhau_studio.storage.warehouse import connect_read_only

    with (
        contextlib.closing(connect_read_only(seeded["root"] / "serving.sqlite")) as conn,
        pytest.raises(sqlite3.OperationalError),
    ):
        conn.execute("DELETE FROM source_records")
