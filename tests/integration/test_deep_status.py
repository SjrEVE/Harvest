from datetime import UTC, datetime

from test_auto_deep import make_eligible
from test_deep_run import seed_records
from test_public_deep_refresh import capture, payload

from santhau_studio.desktop.deep_status import queue, states
from santhau_studio.storage.public_deep import task_state
from santhau_studio.workers.auto_deep import candidates, configure


def test_unsupported_record_does_not_look_like_pending_collection(tmp_path):
    writer = seed_records(tmp_path)
    try:
        assert {r["state"] for r in states(writer.conn, 1)} == {"UNSUPPORTED"}
    finally:
        writer.close()


def test_orphan_checkpoint_is_stale_and_selected_for_repair(tmp_path):
    writer = seed_records(tmp_path)
    try:
        make_eligible(writer)
        configure(writer.conn, {"enabled": True})
        sha = writer.conn.execute(
            "SELECT current_record_object_sha FROM source_records WHERE id=1"
        ).fetchone()[0]
        task_state(writer.conn, 1, "HSMT", "FETCHED", listing_sha=sha)
        assert next(r for r in states(writer.conn, 1) if r["lane"] == "HSMT")["state"] == "STALE"
        assert any(r["id"] == 1 and r["lane"] == "HSMT" for r in candidates(writer.conn, 30))
    finally:
        writer.close()


def test_queue_has_bounded_pages_filters_and_no_duplicate_legacy_captures(tmp_path):
    writer = seed_records(tmp_path)
    try:
        make_eligible(writer)
        result = capture(writer, payload())
        task_state(
            writer.conn,
            1,
            "DETAIL",
            "FETCHED",
            capture_id=result.capture_id,
            listing_sha=writer.conn.execute(
                "SELECT current_record_object_sha FROM source_records WHERE id=1"
            ).fetchone()[0],
        )
        queries = []
        writer.conn.set_trace_callback(queries.append)
        first = queue(writer.conn, {"limit": 2})
        second = queue(writer.conn, {"limit": 2, "offset": 2})
        assert len(first["rows"]) == len(second["rows"]) == 2
        assert first["total"] == 9 and first["has_more"]
        assert first["summary"]["FETCHED"] == 1 and first["summary"]["NOT_COLLECTED"] == 8
        assert not ({r["row_key"] for r in first["rows"]} & {r["row_key"] for r in second["rows"]})
        assert len(queries) <= 12  # two complete reads, independent of result count
        filtered = queue(writer.conn, {"state": "NOT_COLLECTED", "lane": "HSMT", "text": "IB-1"})
        assert filtered["total"] == 1 and filtered["rows"][0]["natural_key"] == "IB-1"
        assert queue(writer.conn, {"state": "injected sql"})["error_code"] == "INVALID_FILTER"
    finally:
        writer.close()


def test_cooldown_compares_instants_not_timestamp_spellings(tmp_path):
    writer = seed_records(tmp_path)
    try:
        make_eligible(writer)
        configure(writer.conn, {"enabled": True})
        # '+07:00' lexical ordering can hide a failure older than the cooldown.
        stamp = datetime.now(UTC).replace(hour=0).isoformat()
        task_state(writer.conn, 1, "HSMT", "FAILED", error="temporary")
        writer.conn.execute("UPDATE deep_tasks SET updated_at_utc=?", (stamp,))
        writer.conn.execute(
            "UPDATE deep_tasks SET updated_at_utc=strftime('%Y-%m-%dT%H:%M:%S','now','-61 minutes','+7 hours') || '+07:00'"
        )
        assert any(r["id"] == 1 and r["lane"] == "HSMT" for r in candidates(writer.conn, 30))
    finally:
        writer.close()


def test_large_legacy_queue_stays_bounded_and_reports_filtered_totals(tmp_path):
    writer = seed_records(tmp_path)
    try:
        capture(writer, payload())
        for i in range(505):
            writer.conn.execute(
                """INSERT INTO deep_captures
              (source_record_id,lane,endpoint_kind,latest_capture_seq,completeness,captured_at_utc,
               refresh_due_at_utc,stale,source_version_json,response_object_sha,record_object_sha,content_hash,lane_status)
              SELECT source_record_id,lane,?,latest_capture_seq,completeness,captured_at_utc,
               refresh_due_at_utc,stale,source_version_json,response_object_sha,record_object_sha,content_hash,lane_status
              FROM deep_captures WHERE id=1""",
                (f"legacy-{i}",),
            )
        queries = []
        writer.conn.set_trace_callback(queries.append)
        result = queue(writer.conn, {"limit": 5000})
        assert len(result["rows"]) == 100
        assert result["total"] == result["legacy_count"] == 506
        assert result["eligible_total"] == 0
        assert len(queries) <= 6
        next_page = queue(writer.conn, {"limit": 100, "offset": 100})
        assert not (
            {r["row_key"] for r in result["rows"]} & {r["row_key"] for r in next_page["rows"]}
        )
    finally:
        writer.close()
