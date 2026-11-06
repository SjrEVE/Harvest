from test_runner_modes import DAY, FakePortal, make_runner, page_of
from test_runner_modes import writer as writer


def test_same_count_same_ids_changed_content_is_hash_drift(writer):
    portal = FakePortal({"DAY": [page_of(0, 2)]})
    runner = make_runner(writer, portal)
    runner.backfill("run-1", DAY)
    changed = page_of(0, 2)
    changed.payload["page"]["content"][0]["bidName"] = "Changed procurement title"
    portal.by_granularity["DAY"] = [changed]
    writer.register_run("audit", "audit", "RECONCILE")
    findings = runner.reconcile("audit", DAY)
    assert [f["kind"] for f in findings] == ["HASH_DRIFT"]
    assert findings[0]["changed_count"] == 1
    assert writer.conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0] == 2
    assert not runner.resolve(DAY).can_skip
    writer.register_run("repair", "repair", "BACKFILL")
    runner.backfill("repair", DAY)
    assert runner.resolve(DAY).can_skip
    assert writer.conn.execute("SELECT COUNT(*) FROM reconcile_gaps").fetchone()[0] == 1


def test_same_count_different_ids_is_not_clean_audit(writer):
    portal = FakePortal({"DAY": [page_of(0, 2)]})
    runner = make_runner(writer, portal)
    runner.backfill("run-1", DAY)
    changed = page_of(0, 2)
    changed.payload["page"]["content"][0]["notifyNo"] = "REPLACED-ID"
    portal.by_granularity["DAY"] = [changed]
    writer.register_run("audit", "audit", "RECONCILE")
    findings = runner.reconcile("audit", DAY)
    assert {f["kind"] for f in findings} == {
        "COUNT_DRIFT",
        "MISSING_FROM_SOURCE_IN_VERIFIED_WINDOW",
    }
    assert writer.conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0] == 3
