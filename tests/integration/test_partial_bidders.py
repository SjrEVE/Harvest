"""A bad offeror row must not wipe the offerors that parsed, or fetch the same page twice."""

from datetime import UTC, datetime, timedelta

from test_deep_run import seed_records

from santhau_studio.collectors.cgttrg import PARSER_VERSION, public_spec
from santhau_studio.desktop.deep_status import queue, states
from santhau_studio.storage.deep import STALE_SOURCE_VERSION_CHANGED
from santhau_studio.workers.auto_deep import candidates, configure
from santhau_studio.workers.deep_run import run_deep_batch


def _record(writer):
    return writer.conn.execute(
        "SELECT id, natural_key FROM source_records ORDER BY id LIMIT 1"
    ).fetchone()


def _offerors(writer):
    return [
        row[0]
        for row in writer.conn.execute(
            "SELECT party_name FROM contractor_memberships ORDER BY party_name"
        )
    ]


def test_mixed_offeror_rows_keep_valid_bidders_and_the_sibling_page(tmp_path):
    writer = seed_records(tmp_path)
    record = _record(writer)
    calls = []

    def fetch(_family, key):
        calls.append(key)
        return {
            "notifyNo": key,
            "notifyVersion": "00",
            "bidForm": "CGTTRG",
            "contractorsResult": [
                {"contractorCode": "good", "contractorName": "Nhà thầu A"},
                {"contractorCode": "blank"},
                "not-an-object",
            ],
        }

    def collect(lane, force=False):
        return run_deep_batch(
            writer,
            run_id="partial-bidders",
            family="TBMT",
            lane=lane,
            scope={"record_ids": [record["id"]], "force_refresh": force},
            fetcher=fetch,
            spec_provider=public_spec,
        )

    try:
        first = collect("DETAIL")
        assert first.status == "COMPLETED"
        assert _offerors(writer) == ["Nhà thầu A", "blank"]
        capture = writer.conn.execute(
            "SELECT completeness, lane_status, stale FROM deep_captures"
        ).fetchone()
        assert capture["completeness"] == "PARTIAL"
        assert capture["lane_status"] == "PARTIAL"
        assert capture["stale"] == 0
        stored = writer.conn.execute("SELECT state, error FROM deep_tasks").fetchone()
        assert stored["state"] == "FETCHED"
        assert "OFFEROR_NAME_MISSING" in stored["error"]
        assert "OFFEROR_2_INVALID" in stored["error"]
        shown = next(row for row in states(writer.conn, record["id"]) if row["lane"] == "DETAIL")
        assert shown["state"] == "PARTIAL"
        assert shown["needs_refresh"] is False

        def refuse(*_args):
            raise AssertionError("REOFFER must reuse the DETAIL response")

        second = run_deep_batch(
            writer,
            run_id="partial-sibling",
            family="TBMT",
            lane="REOFFER",
            scope={"record_ids": [record["id"]]},
            fetcher=refuse,
            spec_provider=public_spec,
        )
        assert second.detail["reused"] == 1 and len(calls) == 1
        assert _offerors(writer) == ["Nhà thầu A", "blank"]
        assert writer.conn.execute("SELECT COUNT(*) FROM deep_capture_history").fetchone()[0] == 2
    finally:
        writer.close()


def test_shape_error_keeps_prior_bidders_and_does_not_fetch_the_sibling(tmp_path):
    writer = seed_records(tmp_path)
    record = _record(writer)
    calls = []

    def fetch(_family, key):
        calls.append(key)
        if len(calls) == 1:
            return {
                "notifyNo": key,
                "notifyVersion": "00",
                "bidForm": "CGTTRG",
                "contractorsResult": [{"contractorCode": "good", "contractorName": "Nhà thầu A"}],
            }
        return {"notifyNo": key, "notifyVersion": "00", "bidForm": "CGTTRG", "contractorsResult": "broken"}

    def collect(lane, force=False):
        return run_deep_batch(
            writer,
            run_id="shape",
            family="TBMT",
            lane=lane,
            scope={"record_ids": [record["id"]], "force_refresh": force},
            fetcher=fetch,
            spec_provider=public_spec,
        )

    try:
        assert collect("DETAIL").status == "COMPLETED"
        assert collect("DETAIL", True).status == "PARTIAL"
        assert _offerors(writer) == ["Nhà thầu A"]
        assert writer.conn.execute("SELECT state FROM deep_tasks WHERE lane='DETAIL'").fetchone()[0] == "PARSE_ERROR"
        second = collect("REOFFER")
        assert second.detail["reused"] == 1 and len(calls) == 2
        assert _offerors(writer) == ["Nhà thầu A"]
    finally:
        writer.close()


def test_refresh_due_is_not_shown_as_work_when_policy_holds_it(tmp_path):
    writer = seed_records(tmp_path)
    record = _record(writer)

    def fetch(_family, key):
        return {"notifyNo": key, "notifyVersion": "00", "bidForm": "CGTTRG",
                "contractorsResult": [{"contractorCode": "good", "contractorName": "Nhà thầu A"}]}

    try:
        run_deep_batch(
            writer, run_id="due", family="TBMT", lane="DETAIL",
            scope={"record_ids": [record["id"]]}, fetcher=fetch, spec_provider=public_spec,
        )
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        writer.conn.execute("UPDATE deep_captures SET refresh_due_at_utc=?", (past,))
        shown = next(row for row in states(writer.conn, record["id"]) if row["lane"] == "DETAIL")
        assert shown["state"] == "FETCHED"
        assert shown["refresh_due_elapsed"] == 1
        assert shown["needs_refresh"] is False
        listed = queue(writer.conn, {"state": "STALE"})
        assert listed["total"] == 0
        assert listed["refresh_policy"]["refresh_expired"] is False
        configure(writer.conn, {"enabled": True, "refresh_expired": True})
        shown = next(row for row in states(writer.conn, record["id"]) if row["lane"] == "DETAIL")
        assert shown["state"] == "STALE"
        assert shown["needs_refresh"] is True
    finally:
        writer.close()


def test_parser_upgrade_does_not_clear_version_staleness_or_add_history(tmp_path):
    writer = seed_records(tmp_path)
    record = _record(writer)

    def fetch(_family, key):
        return {"notifyNo": key, "notifyVersion": "00", "bidForm": "CGTTRG"}

    try:
        run_deep_batch(
            writer, run_id="first", family="TBMT", lane="DETAIL",
            scope={"record_ids": [record["id"]]}, fetcher=fetch, spec_provider=public_spec,
        )
        before = writer.conn.execute("SELECT COUNT(*) FROM deep_capture_history").fetchone()[0]
        writer.conn.execute(
            "UPDATE deep_captures SET stale=1, stale_reason=?, lane_status='STALE'",
            (STALE_SOURCE_VERSION_CHANGED,),
        )
        writer.conn.execute("UPDATE deep_capture_parse_metadata SET parser_version=1")
        writer.conn.execute("UPDATE deep_task_metadata SET parser_version=1")

        def no_network(*_args):
            raise AssertionError("parser upgrade must use the saved response")

        result = run_deep_batch(
            writer, run_id="upgrade", family="TBMT", lane="DETAIL",
            scope={"record_ids": [record["id"]]}, fetcher=no_network, spec_provider=public_spec,
        )
        assert result.detail["reused"] == 1
        assert writer.conn.execute("SELECT COUNT(*) FROM deep_capture_history").fetchone()[0] == before
        row = writer.conn.execute("SELECT stale, stale_reason FROM deep_captures").fetchone()
        assert row["stale"] == 1 and row["stale_reason"] == STALE_SOURCE_VERSION_CHANGED
        assert writer.conn.execute(
            "SELECT MAX(parser_version) FROM deep_capture_parse_metadata"
        ).fetchone()[0] == PARSER_VERSION
        shown = next(row for row in states(writer.conn, record["id"]) if row["lane"] == "DETAIL")
        assert shown["state"] == "STALE"
    finally:
        writer.close()


def test_incremental_refresh_recent_is_an_explicit_boolean():
    from santhau_studio.desktop.collection_dates import collection_scope

    plain = collection_scope({"auto_range": True}, "INCREMENTAL")
    assert "refresh_recent" not in plain
    opted = collection_scope({"auto_range": True, "refresh_recent": True}, "INCREMENTAL")
    assert opted["refresh_recent"] is True
    ignored = collection_scope({"auto_range": True, "refresh_recent": "yes"}, "INCREMENTAL")
    assert "refresh_recent" not in ignored


def test_automatic_deep_does_not_requeue_a_capture_only_because_the_parser_is_newer(tmp_path):
    from test_auto_deep import make_eligible

    writer = seed_records(tmp_path)
    record = _record(writer)

    def fetch(_family, key):
        return {"notifyNo": key, "notifyVersion": "00", "bidForm": "CGTTRG"}

    try:
        make_eligible(writer)
        run_deep_batch(
            writer, run_id="first", family="TBMT", lane="DETAIL",
            scope={"record_ids": [record["id"]]}, fetcher=fetch, spec_provider=public_spec,
        )
        writer.conn.execute("UPDATE deep_capture_parse_metadata SET parser_version=1")
        writer.conn.execute("UPDATE deep_task_metadata SET parser_version=1")
        configure(writer.conn, {"enabled": True, "batch_requests": 30})
        selected = {(row["id"], row["lane"]) for row in candidates(writer.conn, 30)}
        assert (record["id"], "DETAIL") not in selected
        assert (record["id"], "HSMT") in selected
    finally:
        writer.close()
