"""Milestone E: worker runtime and named-pipe IPC, exercised end to end.

The pipe tests talk to a *real* server and a *real* client — the acceptance criteria (FAULT-15
deny, name-conflict detection, FAULT-12 survive-the-GUI) are transport behaviors, not mocks.
Job-lifecycle tests run the WorkerLoop in-process against the offline portal from the runner
fixtures; the subprocess test runs the actual service entry point in a second process.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import date
from pathlib import Path

import pytest

from santhau_studio.collectors.egp_home import Page
from santhau_studio.collectors.source_registry import SOURCE_EGP_HOME, SOURCES
from santhau_studio.domain.coverage import Interval, vn_calendar_day
from santhau_studio.platform_win.os_lock import holder_info
from santhau_studio.platform_win.process import current_process_sid, pid_alive
from santhau_studio.storage import coverage
from santhau_studio.storage.warehouse import WarehouseWriter, connect, connect_read_only
from santhau_studio.workers.ipc import (
    PipeClient,
    PipeClosedError,
    PipeNameConflictError,
    PipeServer,
    PipeTransportError,
    worker_pipe_name,
)
from santhau_studio.workers.jobs import (
    JobAlreadyQueued,
    JobError,
    JobRequest,
    WorkerController,
    WorkerLoop,
    claim_next_job,
    enqueue_job,
    finish_job,
    recover_interrupted_runs,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="named-pipe IPC is Windows-only")

DAY = vn_calendar_day(date(2026, 11, 1))
ENDPOINT = SOURCES[SOURCE_EGP_HOME].endpoint
RULE_SHA = "a" * 64


def records(page_no: int, count: int) -> list[dict]:
    return [{"notifyNo": f"P{page_no}-{i}", "notifyVersion": "00", "bidName": f"Gói {i}"} for i in range(count)]


def _insert_rule(writer, request: JobRequest, precision: str = "DAY") -> None:
    source = request.sources[0]
    writer.conn.execute(
        "INSERT OR IGNORE INTO filter_boundary_rules (rule_key, rule_version, source_key, family, endpoint,"
        " adapter_sha, boundary_kind, precision, source_timezone, offset_minutes, conversion_params,"
        " canary_verified, verified_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("worker-portal", 1, source["source_key"], source["family"], source["endpoint"], source["adapter_sha"],
         "CLOSED_LOCAL", precision, "Asia/Ho_Chi_Minh", 420, '{"to_minus":"1s"}', 1, "2026-10-31T00:00:00Z"),
    )
    writer.conn.commit()


def portal_builder(pages: int, per_page: int = 5, precision: str = "DAY"):
    def build(writer, request: JobRequest):
        _insert_rule(writer, request, precision)

        def page_source(_interval: Interval, _granularity: str, _window) -> object:
            for page_no in range(pages):
                content = records(page_no, per_page)
                yield Page(
                    payload={"page": {"content": content, "totalElements": pages * per_page}},
                    page_no=page_no,
                    rows=per_page,
                    has_more=page_no + 1 < pages,
                    total_hint=pages * per_page,
                    total_hint_trusted=True,
                    http_status=200,
                    attempt_log=(),
                )

        return page_source

    return build


def job_request(**overrides) -> JobRequest:
    values = {
        "job_id": f"job-{uuid.uuid4().hex[:8]}",
        "job_kind": "BACKFILL",
        "requested_by": "test",
        "sources": (
            {"source_key": SOURCE_EGP_HOME, "family": "TBMT", "endpoint": ENDPOINT, "adapter_sha": RULE_SHA},
        ),
        "scope": {"from": DAY.from_utc.isoformat().replace("+00:00", "Z"),
                  "to": DAY.to_utc.isoformat().replace("+00:00", "Z")},
        "created_at": "2026-11-03T00:00:00Z",
    }
    values.update(overrides)
    return JobRequest(**values)


def test_response_timeout_does_not_freeze_a_client() -> None:
    name = f"\\\\.\\pipe\\santhau-timeout-{uuid.uuid4().hex}"
    def slow_handler(_payload, _sid):
        time.sleep(0.3)
        return {"ok": True}

    server = PipeServer(name, allowed_sid=current_process_sid(), handler=slow_handler)
    stop = threading.Event()
    thread = threading.Thread(target=server.serve_until, args=(stop,), daemon=True)
    thread.start()
    client = PipeClient.connect(name, timeout_seconds=0.05)
    try:
        with pytest.raises(PipeClosedError):
            client.request({"op": "ping"})
    finally:
        client.close()
        stop.set()
        server.wake()
        thread.join(timeout=2)


class Harness:
    """Loop + pipe server wired like service.main, but in-process and fully observable."""

    def __init__(self, root: Path, builder) -> None:
        self.root = root
        self.loop = WorkerLoop(root, page_source_builder=builder)
        self.controller = WorkerController(self.loop)
        self.sid = current_process_sid()
        self.pipe_name = f"\\\\.\\pipe\\santhau-test-{uuid.uuid4().hex}"
        self.server = PipeServer(self.pipe_name, allowed_sid=self.sid, handler=self.controller)
        self.stop = threading.Event()
        self.loop_thread = threading.Thread(target=self.loop.run, args=(self.stop,), daemon=True)
        self.pipe_thread = threading.Thread(target=self.server.serve_until, args=(self.stop,), daemon=True)

    def start(self) -> Harness:
        self.loop_thread.start()
        self.pipe_thread.start()
        return self

    def connect(self, timeout: float = 10.0) -> PipeClient:
        deadline = time.monotonic() + timeout
        while True:
            try:
                return PipeClient.connect(self.pipe_name, timeout_seconds=0.5)
            except PipeTransportError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def close(self) -> None:
        self.stop.set()
        self.server.wake()
        self.loop_thread.join(timeout=15)
        self.pipe_thread.join(timeout=5)


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture()
def harness(root: Path):
    h = Harness(root, portal_builder(pages=3, per_page=10)).start()
    try:
        yield h
    finally:
        h.close()


def gated_builder(gate: threading.Event, pages: int = 5):
    """A portal that parks after page 0 until the test opens the gate.

    Without the gate, a synthetic run finishes inside one poll interval and a test can never
    observe RUNNING — the pause/stop tests would race the whole job instead of the page boundary.
    """

    def build(writer, request: JobRequest):
        _insert_rule(writer, request)

        def page_source(_interval: Interval, _granularity: str, _window) -> object:
            for page_no in range(pages):
                if page_no > 0:
                    gate.wait(timeout=30)
                content = records(page_no, 5)
                yield Page(
                    payload={"page": {"content": content, "totalElements": pages * 5}},
                    page_no=page_no,
                    rows=5,
                    has_more=page_no + 1 < pages,
                    total_hint=pages * 5,
                    total_hint_trusted=True,
                    http_status=200,
                    attempt_log=(),
                )

        return page_source

    return build


@pytest.fixture()
def gated_harness(root: Path):
    gate = threading.Event()
    h = Harness(root, gated_builder(gate)).start()
    h.gate = gate
    try:
        yield h
    finally:
        gate.set()
        h.close()


def _db(root: Path):
    return connect_read_only(root / "warehouse.sqlite")


def run_rows(root: Path, job_id: str) -> list:
    try:
        with contextlib.closing(_db(root)) as conn:
            return conn.execute(
                "SELECT run_id, lifecycle, status, started_at_utc, finished_at_utc FROM runs"
                " WHERE job_id=? ORDER BY rowid",
                (job_id,),
            ).fetchall()
    except sqlite3.OperationalError:
        # Schema not created yet, or a transient WAL lock while polling a busy writer.
        return []


def terminal_run(root: Path, job_id: str):
    rows = run_rows(root, job_id)
    return rows[0] if rows and rows[0]["lifecycle"] in ("FINISHED", "TERMINATED") else None


def wait_running(harness: Harness, root: Path, job_id: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(row["lifecycle"] == "RUNNING" for row in run_rows(root, job_id)):
            return
        if not harness.loop_thread.is_alive():
            pytest.fail(f"worker loop died: {harness.loop.last_error!r} (rows={_rows_text(root, job_id)})")
        time.sleep(0.05)
    pytest.fail(
        f"job {job_id} never reached RUNNING: rows={_rows_text(root, job_id)}"
        f" loop_alive={harness.loop_thread.is_alive()} error={harness.loop.last_error!r}"
    )


def wait_terminal(harness: Harness, root: Path, job_id: str, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = terminal_run(root, job_id)
        if row is not None:
            return row
        if not harness.loop_thread.is_alive():
            pytest.fail(f"worker loop died: {harness.loop.last_error!r} (rows={_rows_text(root, job_id)})")
        time.sleep(0.05)
    pytest.fail(
        f"job {job_id} never reached a terminal run: rows={_rows_text(root, job_id)}"
        f" loop_alive={harness.loop_thread.is_alive()} error={harness.loop.last_error!r}"
    )


def _rows_text(root: Path, job_id: str) -> str:
    try:
        return str([dict(row) for row in run_rows(root, job_id)])
    except Exception as exc:  # diagnostics only
        return f"unreadable: {exc}"


def poll(query, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = query()
        if value:
            return value
        time.sleep(0.05)
    return query()


# ------------------------------------------------------------------ pipe transport

def test_ping_roundtrip_over_named_pipe(harness) -> None:
    client = harness.connect()
    try:
        assert client.request({"op": "ping"}) == {"ok": True, "worker": True, "capabilities": ["weekly-six-newest-v2"]}
    finally:
        client.close()


def test_client_with_foreign_sid_is_refused(root) -> None:
    """FAULT-15: the SID check denies; the client never sees a response byte."""
    controller = WorkerController(WorkerLoop(root))
    real_sid = current_process_sid()
    server = PipeServer(
        f"\\\\.\\pipe\\santhau-test-{uuid.uuid4().hex}",
        allowed_sid="S-1-5-999-99999",  # app-layer allowlist disagrees with the SDDL on purpose
        sddl_sid=real_sid,  # OS layer lets our user in, so the app-layer check is what fires
        handler=controller,
    )
    stop = threading.Event()
    thread = threading.Thread(target=server.serve_until, args=(stop,), daemon=True)
    thread.start()
    try:
        client = PipeClient.connect(server.name)
        try:
            with pytest.raises(PipeClosedError):
                client.request({"op": "ping"})
        finally:
            client.close()
    finally:
        stop.set()
        server.wake()
        thread.join(timeout=5)


def test_second_server_on_same_pipe_name_conflicts(harness) -> None:
    with pytest.raises(PipeNameConflictError):
        PipeServer(harness.pipe_name, allowed_sid=harness.sid, handler=harness.controller)


# ------------------------------------------------------------------ queue files

def test_enqueue_claim_and_finish_roundtrip(root) -> None:
    request = job_request()
    enqueue_job(root, request)
    claimed = claim_next_job(root)
    assert claimed is not None and claimed.job_id == request.job_id
    assert list((root / "jobs" / "active").glob("*.json"))
    assert claim_next_job(root) is None  # queue is empty now
    assert finish_job(root, request.job_id)
    assert list((root / "jobs" / "done").glob("*.json"))
    assert not list((root / "jobs" / "active").glob("*.json"))


def test_request_document_validates(root) -> None:
    with pytest.raises(JobError):
        job_request(job_kind="WAT")
    with pytest.raises(JobError):
        job_request(job_id="../escape")
    request = job_request()
    assert JobRequest.from_json(request.to_json()) == request
    enqueue_job(root, request)
    with pytest.raises(JobAlreadyQueued):
        enqueue_job(root, request)


# ------------------------------------------------------------------ job lifecycle

def test_backfill_job_runs_to_completed(harness, root) -> None:
    request = job_request()
    enqueue_job(root, request)
    row = wait_terminal(harness, root, request.job_id)
    assert row["lifecycle"] == "FINISHED" and row["status"] == "COMPLETED"
    assert harness.loop_thread.is_alive()  # the idle loop keeps serving the next job
    with contextlib.closing(_db(root)) as conn:
        proof = conn.execute(
            "SELECT cp.id FROM coverage_proofs cp JOIN coverage_intervals ci ON ci.proof_id = cp.id"
        ).fetchone()
        task = conn.execute(
            "SELECT status, detail_json FROM run_tasks WHERE run_id=?", (row["run_id"],)
        ).fetchone()
    assert proof is not None
    assert task["status"] == "COMPLETED"


def test_status_over_pipe_reports_runs(harness, root) -> None:
    request = job_request()
    enqueue_job(root, request)
    wait_terminal(harness, root, request.job_id)
    client = harness.connect()
    try:
        status = client.request({"op": "status", "job_id": request.job_id})
        assert status["ok"] is True
        assert status["runs"][0]["status"] == "COMPLETED"
        assert status["runs"][0]["tasks"][0]["status"] == "COMPLETED"
        empty = client.request({"op": "status", "job_id": "job-absent"})
        assert empty["runs"] == []
        bad = client.request({"op": "nonsense"})
        assert bad == {"ok": False, "error": "UNKNOWN_OP"}
    finally:
        client.close()


def test_pause_then_resume_runs_a_new_run(gated_harness, root) -> None:
    harness = gated_harness
    request = job_request()
    enqueue_job(root, request)
    wait_running(harness, root, request.job_id)
    client = harness.connect()
    try:
        assert client.request({"op": "command", "command": "PAUSE", "job_id": request.job_id})["ok"]
    finally:
        client.close()
    harness.gate.set()  # release the portal so the runner reaches its next page boundary
    row = poll(lambda: next((r for r in run_rows(root, request.job_id) if r["lifecycle"] == "PAUSED"), None))
    assert row is not None and row["status"] is None  # paused runs have no outcome yet

    client = harness.connect()
    try:
        assert client.request({"op": "command", "command": "RESUME", "job_id": request.job_id})["ok"]
    finally:
        client.close()

    def resumed_completed():
        rows = run_rows(root, request.job_id)
        return rows if len(rows) >= 2 and rows[-1]["status"] == "COMPLETED" else None

    rows = poll(resumed_completed, timeout=60)
    assert rows[0]["lifecycle"] == "PAUSED" and rows[0]["status"] is None
    assert rows[-1]["lifecycle"] == "FINISHED" and rows[-1]["status"] == "COMPLETED"
    assert poll(lambda: not list((root / "jobs" / "active").glob("*.json")))
    with contextlib.closing(_db(root)) as conn:
        commands = conn.execute(
            "SELECT command, issued_by_sid FROM job_commands WHERE job_id=? ORDER BY seq", (request.job_id,)
        ).fetchall()
    assert [c["command"] for c in commands] == ["PAUSE", "RESUME"]
    assert all(c["issued_by_sid"] == harness.sid for c in commands)


def test_stop_graceful_interrupts_the_run(gated_harness, root) -> None:
    harness = gated_harness
    request = job_request()
    enqueue_job(root, request)
    wait_running(harness, root, request.job_id)
    client = harness.connect()
    try:
        assert client.request({"op": "command", "command": "STOP_GRACEFUL", "job_id": request.job_id})["ok"]
    finally:
        client.close()
    harness.gate.set()

    def terminal():
        return next((r for r in run_rows(root, request.job_id) if r["lifecycle"] == "TERMINATED"), None)

    row = poll(terminal)
    assert row["status"] == "INTERRUPTED"  # §2.3: never self-COMPLETED
    assert poll(lambda: not list((root / "jobs" / "active").glob(f"*-{request.job_id}.json")))


def test_force_terminate_stops_the_worker_loop(gated_harness, root) -> None:
    harness = gated_harness
    request = job_request()
    enqueue_job(root, request)
    wait_running(harness, root, request.job_id)
    client = harness.connect()
    try:
        assert client.request({"op": "command", "command": "FORCE_TERMINATE", "job_id": request.job_id})["ok"]
    finally:
        client.close()
    harness.gate.set()

    def terminal():
        return next((r for r in run_rows(root, request.job_id) if r["lifecycle"] == "TERMINATED"), None)

    row = poll(terminal)
    assert row["status"] == "INTERRUPTED"
    harness.loop_thread.join(timeout=15)
    assert not harness.loop_thread.is_alive()  # the loop itself shut down


def test_incremental_job_advances_the_watermark(root) -> None:
    # Clipped days run at finer-than-DAY granularity, so the rule must certify down to SECOND.
    h = Harness(root, portal_builder(3, 10, precision="SECOND")).start()
    try:
        request = job_request(job_kind="INCREMENTAL")
        enqueue_job(root, request)
        row = wait_terminal(h, root, request.job_id)
        assert row["status"] == "COMPLETED", row["status"]
        with contextlib.closing(_db(root)) as conn:
            watermark = coverage.watermark_for(conn, SOURCE_EGP_HOME, "TBMT")
        # The watermark starts NULL on a fresh root; advancing at all is the observable claim.
        assert watermark is not None
    finally:
        h.close()


def test_undispatchable_job_kind_is_blocked(harness, root) -> None:
    request = job_request(job_kind="VERIFY")
    enqueue_job(root, request)
    row = wait_terminal(harness, root, request.job_id)
    # A blocked job is a terminated run with a verdict, never a clean FINISH (§3.6).
    assert row["lifecycle"] == "TERMINATED" and row["status"] == "BLOCKED"
    assert harness.loop_thread.is_alive()
    with contextlib.closing(_db(root)) as conn:
        task = conn.execute(
            "SELECT status, detail_json FROM run_tasks WHERE run_id=?", (row["run_id"],)
        ).fetchone()
    assert task["status"] == "BLOCKED" and "NOT_DISPATCHABLE" in task["detail_json"]


# ------------------------------------------------------------------ crash recovery

def test_resume_then_crash_ignores_historical_pause(root) -> None:
    writer = WarehouseWriter.open(root, create_schema=True)
    writer.register_run('old-pause', 'resume-crash', 'BACKFILL')
    writer.conn.execute("UPDATE runs SET lifecycle='PAUSED' WHERE run_id='old-pause'")
    writer.register_run('new-running', 'resume-crash', 'BACKFILL')
    writer.conn.commit()
    active = root / 'jobs' / 'active'
    active.mkdir(parents=True,exist_ok=True)
    (active / 'request.json').write_text('{"job_id":"resume-crash"}')
    result = recover_interrupted_runs(writer.conn,root)
    assert result['requeued_jobs'] == 1
    assert (root / 'jobs' / 'queued' / 'request.json').exists()
    writer.close()


def test_recover_interrupted_runs_marks_orphans_and_requeues(root) -> None:
    writer = WarehouseWriter.open(root, create_schema=True, role="seed")
    writer.register_run("run-dead", "job-dead", "BACKFILL")
    writer.register_run("run-paused", "job-paused", "BACKFILL")
    writer.conn.execute("UPDATE runs SET lifecycle='PAUSED' WHERE run_id='run-paused'")
    writer.conn.commit()
    writer.close()

    for job_id in ("job-dead", "job-paused"):
        enqueue_job(root, job_request(job_id=job_id))
        assert claim_next_job(root) is not None  # both requests now sit in jobs/active

    # No worker is running here; recovery needs the write connection, not the reader.
    with contextlib.closing(connect(root / "warehouse.sqlite")) as conn:
        summary = recover_interrupted_runs(conn, root)
        dead = conn.execute("SELECT lifecycle, status FROM runs WHERE run_id='run-dead'").fetchone()
        paused = conn.execute("SELECT lifecycle, status FROM runs WHERE run_id='run-paused'").fetchone()

    assert summary["interrupted_runs"] == 1
    assert dead["lifecycle"] == "TERMINATED" and dead["status"] == "INTERRUPTED"
    assert paused["lifecycle"] == "PAUSED" and paused["status"] is None
    assert (root / "jobs" / "queued" / "20261103T000000Z-job-dead.json").exists()
    assert (root / "jobs" / "active" / "20261103T000000Z-job-paused.json").exists()


def test_unreadable_active_request_goes_to_rejected(root) -> None:
    writer = WarehouseWriter.open(root, create_schema=True, role="seed")
    writer.register_run("run-dead", "job-dead", "BACKFILL")
    writer.close()
    (root / "jobs" / "active").mkdir(parents=True, exist_ok=True)
    (root / "jobs" / "active" / "bogus.json").write_text("{not json", encoding="utf-8")
    # No worker is running here; recovery needs the write connection, not the reader.
    with contextlib.closing(connect(root / "warehouse.sqlite")) as conn:
        summary = recover_interrupted_runs(conn, root)
    assert (root / "jobs" / "rejected" / "bogus.json").exists()
    assert summary["interrupted_runs"] == 1


# ------------------------------------------------------------------ FAULT-12: subprocess

PORTAL_SOURCE = '''
from santhau_studio.collectors.egp_home import Page

PAGES = 30
PER_PAGE = 5


def build(writer, request):
    source = request.sources[0]
    writer.conn.execute(
        "INSERT OR IGNORE INTO filter_boundary_rules (rule_key, rule_version, source_key, family, endpoint,"
        " adapter_sha, boundary_kind, precision, source_timezone, offset_minutes, conversion_params,"
        " canary_verified, verified_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("worker-portal", 1, source["source_key"], source["family"], source["endpoint"], source["adapter_sha"],
         "CLOSED_LOCAL", "DAY", "Asia/Ho_Chi_Minh", 420, '{"to_minus":"1s"}', 1, "2026-10-31T00:00:00Z"),
    )
    writer.conn.commit()

    def page_source(interval, granularity, window):
        for page_no in range(PAGES):
            content = [{"notifyNo": "P%d-%d" % (page_no, i), "notifyVersion": "00", "bidName": "G%d" % i}
                       for i in range(PER_PAGE)]
            yield Page(
                payload={"page": {"content": content, "totalElements": PAGES * PER_PAGE}},
                page_no=page_no, rows=PER_PAGE, has_more=page_no + 1 < PAGES,
                total_hint=PAGES * PER_PAGE, total_hint_trusted=True, http_status=200, attempt_log=(),
            )

    return page_source
'''


def service_env() -> dict:
    src = Path(__file__).resolve().parents[2] / "src"
    existing = os.environ.get("PYTHONPATH", "")
    return {**os.environ, "PYTHONPATH": os.pathsep.join([str(src), *filter(None, [existing])])}


def test_subprocess_worker_survives_gui_close_and_refuses_a_second_worker(root, tmp_path) -> None:
    """FAULT-12: the worker is a process, not a pane. Closing the client changes nothing."""
    portal = tmp_path / "portal.py"
    portal.write_text(PORTAL_SOURCE, encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[2]
    command = [sys.executable, "-m", "santhau_studio.workers.service",
               "--data-root", str(root), "--page-source-file", str(portal)]
    worker = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=service_env(), cwd=str(repo_root))
    try:
        request = job_request()
        enqueue_job(root, request)

        client = None
        deadline = time.monotonic() + 30
        while client is None and time.monotonic() < deadline:
            if worker.poll() is not None:
                worker.wait(timeout=5)
                stderr = worker.stderr.read().decode(errors="replace") if worker.stderr else ""
                pytest.fail(f"worker exited early rc={worker.returncode}: {stderr[-800:]}")
            try:
                client = PipeClient.connect(worker_pipe_name(data_root=root), timeout_seconds=0.5)
            except PipeTransportError:
                time.sleep(0.1)
        assert client is not None, "worker pipe never became ready"

        try:
            # The pipe is bound before the loop finishes opening the schema, so an early
            # status can legitimately say NOT_READY; the client retries like the GUI would.
            status = {"runs": []}
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not status["runs"]:
                status = client.request({"op": "status", "job_id": request.job_id})
                if not status["runs"]:
                    time.sleep(0.05)
            assert status["runs"], f"no run recorded yet: {status}"
        finally:
            client.close()  # the "GUI closes" moment

        def completed():
            rows = run_rows(root, request.job_id)
            return rows if rows and rows[0]["status"] == "COMPLETED" else None

        rows = poll(completed, timeout=60)
        assert rows[0]["lifecycle"] == "FINISHED"

        second = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=service_env(), cwd=str(repo_root))
        try:
            assert second.wait(timeout=30) != 0  # data-root lock or pipe conflict: no duplicate writer
        finally:
            if second.poll() is None:
                _kill_tree(second)

        client = PipeClient.connect(worker_pipe_name(data_root=root), timeout_seconds=20)
        try:
            status = client.request({"op": "status", "job_id": request.job_id})
            assert status["runs"][0]["status"] == "COMPLETED"  # reopen attaches, nothing was lost
        finally:
            client.close()

        holder = holder_info(root / "writer.lock")
        # The venv's python.exe is a launcher, so the lock holder is its interpreter child;
        # what attach needs is a live pid it can name, not the Popen handle.
        assert holder is not None and holder["role"] == "worker" and pid_alive(holder["pid"])

        client = PipeClient.connect(worker_pipe_name(data_root=root), timeout_seconds=20)
        try:
            assert client.request({"op": "command", "command": "FORCE_TERMINATE", "job_id": request.job_id})["ok"]
        finally:
            client.close()
        code = worker.wait(timeout=15)
        if code != 0:
            stderr = worker.stderr.read().decode(errors="replace") if worker.stderr else ""
            pytest.fail(f"worker exited {code}: {stderr[-1500:]}")
    finally:
        if worker.poll() is None:
            _kill_tree(worker)


def _kill_tree(process: subprocess.Popen) -> None:
    """Kill the launcher and its interpreter child; plain kill() would orphan the child."""
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                   capture_output=True, check=False)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=10)


def test_two_data_roots_run_isolated_workers(tmp_path: Path) -> None:
    """RC2: the pipe name is per data root, so two roots can serve at the same time."""
    from santhau_studio.desktop.backend import StudioBackend
    from santhau_studio.workers.ipc import worker_pipe_name

    roots = [tmp_path / "root-a", tmp_path / "root-b"]
    assert worker_pipe_name(data_root=roots[0]) != worker_pipe_name(data_root=roots[1])
    repo_root = Path(__file__).resolve().parents[2]
    processes = [
        subprocess.Popen(
            [sys.executable, "-m", "santhau_studio.workers.service", "--data-root", str(root)],
            env=service_env(), cwd=str(repo_root),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for root in roots
    ]
    backends = [StudioBackend(root) for root in roots]
    try:
        for backend in backends:
            assert poll(lambda b=backend: b.worker_status()["running"] and b.worker_status()["pipe_ok"], timeout=40)
        assert backends[0].worker_command("FORCE_TERMINATE", None)["ok"]
        assert poll(lambda: not backends[0].worker_status()["running"], timeout=20)
        assert backends[1].worker_status()["running"], "the second root must be unaffected"
        assert backends[1].worker_command("FORCE_TERMINATE", None)["ok"]
        for process in processes:
            assert process.wait(timeout=30) == 0
    finally:
        for process in processes:
            if process.poll() is None:
                _kill_tree(process)
