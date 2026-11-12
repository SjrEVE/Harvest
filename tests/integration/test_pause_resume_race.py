"""F02 regression: a durable PAUSED run is immediately visible and RESUME-able.

Before RC3-5 the run row was committed before the in-memory paused set was
updated, so an IPC RESUME arriving in that window was rejected with
JOB_TARGET_NOT_PAUSED even though the UI already showed a paused run.
"""

from test_worker_ipc import (  # noqa: F401
    enqueue_job,
    gated_harness,
    job_request,
    poll,
    root,
    run_rows,
    wait_running,
)


def test_durable_pause_is_immediately_controllable(gated_harness, root):  # noqa: F811
    harness = gated_harness
    request = job_request()
    enqueue_job(root, request)
    wait_running(harness, root, request.job_id)
    client = harness.connect()
    try:
        assert client.request(
            {"op": "command", "command": "PAUSE", "job_id": request.job_id}
        )["ok"]
    finally:
        client.close()
    harness.gate.set()
    paused = poll(
        lambda: next(
            (r for r in run_rows(root, request.job_id) if r["lifecycle"] == "PAUSED"),
            None,
        )
    )
    assert paused is not None
    client = harness.connect()
    try:
        runtime = client.request({"op": "ping"})["runtime"]
        assert request.job_id in runtime["paused_jobs"]
        assert client.request(
            {"op": "command", "command": "RESUME", "job_id": request.job_id}
        )["ok"]
    finally:
        client.close()
