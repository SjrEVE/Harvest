from datetime import UTC, datetime, timedelta

from test_auto_deep import make_eligible
from test_deep_run import seed_records
from test_public_deep import hsmt
from test_public_deep_refresh import capture

from santhau_studio.collectors.cgttrg import public_spec
from santhau_studio.desktop.backend import StudioBackend
from santhau_studio.storage.serving import build_serving_db
from santhau_studio.workers.deep_run import run_deep_batch


def test_record_preview_is_bounded_with_total_count_and_single_snapshot(tmp_path):
    writer = seed_records(tmp_path)
    try:
        capture(writer, hsmt([{"name": f"Hàng {i}", "qty": i} for i in range(350)]), "HSMT")
    finally:
        writer.close()
    assert build_serving_db(tmp_path / "warehouse.sqlite", tmp_path / "serving.sqlite").published
    backend = StudioBackend(tmp_path)
    connects = []
    open_snapshot = backend._serving

    def counted():
        connects.append(1)
        return open_snapshot()

    backend._serving = counted
    detail = backend.record_detail(1)
    assert len(detail["goods_rows"]) <= 100
    assert detail["history_counts"]["goods_rows"] == 350
    assert len(connects) == 1
    assert (
        len(backend.record_history({"record_id": 1, "kind": "goods_rows", "limit": 100})["rows"])
        == 100
    )


def test_expired_checkpoint_needs_explicit_refresh_at_fractional_clock(tmp_path):
    """Missing-only: an expired TTL never authorizes an implicit re-fetch.

    The fractional-second boundary still matters: an explicit refresh must
    recognise a due timestamp truncated to the same second as expired.
    """
    writer = seed_records(tmp_path)
    make_eligible(writer)
    value = hsmt([{"name": "A", "qty": 1}])
    value["bidoInvBiddingDTO"][0]["notifyNo"] = "IB-0"
    calls = []
    now = datetime.now(UTC).replace(microsecond=500000)

    def fetch(_family, _key):
        calls.append(1)
        return value

    def collect(**scope):
        return run_deep_batch(
            writer,
            run_id="test",
            family="TBMT",
            lane="HSMT",
            scope={"record_ids": [1], **scope},
            fetcher=fetch,
            spec_provider=public_spec,
            clock=lambda: now,
        )

    try:
        assert collect().status == "COMPLETED"
        expired = now - timedelta(microseconds=500000)
        writer.conn.execute(
            "UPDATE deep_captures SET refresh_due_at_utc=?",
            (expired.isoformat(timespec="seconds").replace("+00:00", "Z"),),
        )
        # Default is missing-only: no network request for an expired TTL.
        assert collect().detail["skipped"] == 1 and len(calls) == 1
        # Explicit refresh must see the same-second expiry and fetch once more.
        refreshed = collect(refresh_expired=True)
        assert refreshed.detail["captured"] == 1 and len(calls) == 2
    finally:
        writer.close()


def test_pause_during_fetch_is_not_persisted_as_source_block(tmp_path):
    from santhau_studio.collectors.deep import DetailShapeError

    writer = seed_records(tmp_path)
    stopped = False

    def fetch(_family, _key):
        nonlocal stopped
        stopped = True
        raise DetailShapeError("STOP_REQUESTED")

    try:
        result = run_deep_batch(
            writer,
            run_id="test",
            family="TBMT",
            lane="HSMT",
            scope={"record_ids": [1]},
            fetcher=fetch,
            spec_provider=public_spec,
            stop_requested=lambda: stopped,
        )
        assert result.status == "PARTIAL" and result.detail["reason"] == "STOP_REQUESTED"
        assert writer.conn.execute("SELECT COUNT(*) FROM deep_tasks").fetchone()[0] == 0
    finally:
        writer.close()
