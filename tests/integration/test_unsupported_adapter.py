import json

from test_deep_run import seed_records

from santhau_studio.collectors.cgttrg import public_spec
from santhau_studio.collectors.extraction import extract_page
from santhau_studio.collectors.public_deep import PublicDeepFetcher
from santhau_studio.collectors.source_registry import binding_for
from santhau_studio.linux.deep import deep_status
from santhau_studio.workers.auto_deep import candidates, configure
from santhau_studio.workers.deep_run import run_deep_batch
from santhau_studio.workers.jobs import JobRequest


def test_khac_is_one_unsupported_package_three_lanes_and_never_http(tmp_path, monkeypatch):
    from santhau_studio.collectors import public_deep

    writer = seed_records(tmp_path)
    try:
        raw = {
            "page": {
                "content": [
                    {
                        "notifyNo": "IB-0",
                        "notifyVersion": "00",
                        "notifyId": "synthetic",
                        "bidForm": "CGTTRG",
                        "processApply": "KHAC",
                    }
                ],
                "totalElements": 1,
            }
        }
        ref = writer.store_response(raw)
        attempt = writer.ensure_attempt(
            run_id="run-list",
            family="TBMT",
            endpoint="synthetic",
            interval_from="2026-01-01T00:00:00Z",
            interval_to="2026-01-02T00:00:00Z",
            page_no=9,
            attempt_no=1,
            outcome="OK",
            response=ref,
        )
        writer.commit_page(
            run_id="run-list",
            attempt_id=attempt,
            response_ref=ref,
            records=extract_page(raw, binding_for("TBMT").spec).records,
        )
        rid = writer.conn.execute(
            "SELECT id FROM source_records WHERE natural_key='IB-0'"
        ).fetchone()[0]

        def no_http(**_kwargs):
            raise AssertionError("Unsupported processApply must not create an HTTP lane")

        monkeypatch.setattr(public_deep, "FetchLane", no_http)
        for lane in ("DETAIL", "HSMT", "REOFFER"):
            request = JobRequest(
                "unsupported-" + lane,
                lane,
                "test",
                ({"source_key": "egp_home", "family": "TBMT"},),
                {"record_ids": [rid]},
                created_at="2026-11-06T00:00:00Z",
            )
            writer.register_run(request.job_id, request.job_id, lane)
            fetcher = PublicDeepFetcher(writer, request)
            result = run_deep_batch(
                writer,
                run_id=request.job_id,
                family="TBMT",
                lane=lane,
                scope=request.scope,
                fetcher=fetcher,
                spec_provider=public_spec,
            )
            assert (
                result.detail["unsupported"] == 1
                and result.detail["blocked"] == 0
                and fetcher.budget.used == 0
            )
        tasks = writer.conn.execute(
            "SELECT * FROM deep_tasks WHERE source_record_id=?", (rid,)
        ).fetchall()
        assert all(
            "UNSUPPORTED_PROCESS_APPLY:KHAC" in t["error"]
            and not json.loads(t["provenance_json"])["attempts"]
            for t in tasks
        )
        configure(writer.conn, {"enabled": True, "batch_requests": 30})
        writer.conn.execute(
            "UPDATE deep_tasks SET updated_at_utc='2000-01-01T00:00:00Z' WHERE source_record_id=?",
            (rid,),
        )
        writer.conn.execute(
            "UPDATE deep_task_metadata SET task_updated_at_utc='2000-01-01T00:00:00Z' WHERE source_record_id=?",
            (rid,),
        )
        assert not candidates(writer.conn, 30)
        status = deep_status(tmp_path)
        assert status["unsupported"] == {"packages": 1, "lane_tasks": 3}
        assert status["states"] == {"UNSUPPORTED": 3}
        assert status["packages_by_state"] == {"UNSUPPORTED": 1}
        monkeypatch.setattr(public_deep, "ADAPTER_VERSION", public_deep.ADAPTER_VERSION + 1)
        assert len(candidates(writer.conn, 30)) == 3
    finally:
        writer.close()
