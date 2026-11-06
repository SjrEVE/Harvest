from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

from test_search_filters import database

from santhau_studio.domain.identity import canonical_bytes
from santhau_studio.storage.serving import build_serving_db
from santhau_studio.storage.warehouse import WarehouseWriter
from santhau_studio.workers.jobs import claim_next_job, finish_job


def public_backend(tmp_path):
    backend = database(tmp_path)
    with closing(WarehouseWriter.open(backend.data_root)) as writer:
        row = {
            "notifyNo": "A",
            "notifyId": "notice-a",
            "notifyVersion": "00",
            "bidForm": "CGTTRG",
            "processApply": "LDT",
        }
        ref = writer.store.put(canonical_bytes(row))
        writer.register_object(ref)
        writer.conn.execute(
            "UPDATE source_records SET current_record_object_sha=? WHERE id=1", (ref.sha256,)
        )
    assert build_serving_db(backend.data_root / "warehouse.sqlite", backend.serving_path).published
    # Network rule/worker probing are external boundaries; queue files and validation stay real.
    backend._verified_rule = lambda *_: {
        "source_key": "egp_home",
        "endpoint": "https://example.invalid/public",
        "adapter_sha": "a" * 64,
    }
    backend.worker_status = lambda: {"running": False, "pipe_ok": False}
    return backend


def test_repeated_clicks_share_pending_job_and_can_retry_after_completion(tmp_path):
    backend = public_backend(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: backend.enqueue_deep({"record_id": 1, "lane": "HSMT"}), range(2))
        )
    assert all(r["ok"] for r in results)
    assert len({r["job_id"] for r in results}) == 1
    assert len(list((backend.data_root / "jobs/queued").glob("*.json"))) == 1
    first = claim_next_job(backend.data_root)
    assert backend.enqueue_deep({"record_id": 1, "lane": "HSMT"})["job_id"] == first.job_id
    finish_job(backend.data_root, first.job_id)
    assert backend.enqueue_deep({"record_id": 1, "lane": "HSMT"})["job_id"] != first.job_id


def test_deep_actions_reject_invalid_inputs_and_unverified_source(tmp_path):
    backend = public_backend(tmp_path)
    for value in (True, None, "bad", 0, -2, 1.5):
        result = backend.enqueue_deep({"record_id": value})
        assert not result["ok"]
    assert not backend.enqueue_deep({"record_id": 1, "force_refresh": "false"})["ok"]
    with closing(WarehouseWriter.open(backend.data_root)) as writer:
        writer.conn.execute("UPDATE source_records SET source_key='legacy:other' WHERE id=1")
    assert build_serving_db(backend.data_root / "warehouse.sqlite", backend.serving_path).published
    assert not backend.enqueue_deep({"record_id": 1})["ok"]
    assert not list((backend.data_root / "jobs/queued").glob("*.json"))
