"""Durable job queue and the worker loop (§2.3).

The frozen DDL has no jobs table on purpose: a job request is an immutable *document* —
`{job_id, requested_by, sources[], scope, mode, request_limits, policy_version, created_at}` —
not mutable warehouse state. It lives in the data root as a JSON file under `jobs/queued/`,
is claimed by an atomic rename into `jobs/active/`, and every fact the warehouse records about
it goes into `runs`, `run_tasks` and `job_commands` (all pre-existing DDL).

Lifecycle rules enforced here:

* **Two axes stay separate** (§3.6): lifecycle is control state, status is outcome. A crash can
  only ever produce TERMINATED/INTERRUPTED — never COMPLETED (the DDL CHECK enforces the same).
* **PAUSE lands at a page boundary**: the runner's stop check runs between pages, so the current
  page finishes and commits, then the run is marked PAUSED with no status — exactly the state the
  DDL CHECK permits. The job request stays in `jobs/active/`.
* **RESUME never continues in place.** It re-queues the job request, and the claim loop runs it
  as a *new* run. That is safe because the §4.4 resolver makes re-running idempotent, and honest
  because the old run's PAUSED row keeps its own record.
* **Crash recovery** marks every non-terminal run of a dead worker INTERRUPTED and re-queues its
  request (RECOVERABLE). A paused run is left alone. A job whose runs are all terminal is
  re-queued too, so an INTERRUPTED job retries on the next worker start; jobs that end BLOCKED
  or FAILED are moved to `jobs/done/` and do not loop.
"""

from __future__ import annotations

import json
import os
import queue
import re
import sqlite3
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from santhau_studio.collectors.source_registry import SOURCES
from santhau_studio.domain.coverage import Interval
from santhau_studio.platform_win.os_lock import DataRootBusy
from santhau_studio.storage.rawstore import LAYOUT_SEGMENTS
from santhau_studio.storage.warehouse import WarehouseWriter, connect_read_only
from santhau_studio.workers.deep_run import LANE_KINDS, DeepFetcher, SpecProvider, run_deep_batch
from santhau_studio.workers.runner import IntervalOutcome, PageSource, Runner

JOB_KINDS = (
    "INDEX", "BACKFILL", "INCREMENTAL", "RECONCILE",
    "DETAIL", "HSMT", "REOFFER", "VERIFY", "IMPORT", "EXPORT", "BACKUP",
)
COMMANDS = ("START", "PAUSE", "RESUME", "STOP_GRACEFUL", "FORCE_TERMINATE", "GET_STATUS")
JOB_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,64}")

PageSourceBuilder = Callable[["WarehouseWriter", "JobRequest"], "PageSource | None"]
Clock = Callable[[], datetime]

_QUEUE_DIR = "queued"
_ACTIVE_DIR = "active"
_DONE_DIR = "done"
_REJECTED_DIR = "rejected"


class JobError(ValueError):
    """The job request document is not usable as written."""


class JobAlreadyQueued(JobError):
    """A request with this job_id is already queued, active or done."""


@dataclass(frozen=True)
class JobRequest:
    """The immutable request document (§2.3). One file, one job, no in-place edits."""

    job_id: str
    job_kind: str
    requested_by: str
    sources: tuple[dict[str, Any], ...]
    scope: dict[str, Any]
    mode: str = "interactive"
    request_limits: dict[str, Any] = field(default_factory=dict)
    policy_version: int = 1
    created_at: str = ""

    def __post_init__(self) -> None:
        if self.job_kind not in JOB_KINDS:
            raise JobError(f"unknown job_kind {self.job_kind!r}")
        if not JOB_ID_PATTERN.fullmatch(self.job_id):
            raise JobError(f"job_id {self.job_id!r} must match [A-Za-z0-9._-] and fit a filename")
        if not self.requested_by:
            raise JobError("requested_by is required")
        if not self.created_at:
            raise JobError("created_at is required (the request is immutable, so stamp it once)")
        _parse_iso(self.created_at)

    @classmethod
    def from_json(cls, text: str) -> JobRequest:
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise JobError(f"request is not JSON: {exc}") from exc
        if not isinstance(document, dict):
            raise JobError("request must be a JSON object")
        missing = {"job_id", "job_kind", "requested_by", "sources", "scope", "created_at"} - set(document)
        if missing:
            raise JobError(f"request is missing {sorted(missing)}")
        return cls(
            job_id=str(document["job_id"]),
            job_kind=str(document["job_kind"]),
            requested_by=str(document["requested_by"]),
            sources=tuple(document["sources"]) if isinstance(document["sources"], list) else (),
            scope=dict(document["scope"]) if isinstance(document["scope"], dict) else {},
            mode=str(document.get("mode", "interactive")),
            request_limits=dict(document.get("request_limits", {})),
            policy_version=int(document.get("policy_version", 1)),
            created_at=str(document["created_at"]),
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "job_id": self.job_id, "job_kind": self.job_kind, "requested_by": self.requested_by,
                "sources": list(self.sources), "scope": self.scope, "mode": self.mode,
                "request_limits": self.request_limits, "policy_version": self.policy_version,
                "created_at": self.created_at,
            },
            sort_keys=True,
        )

    def interval(self) -> Interval:
        try:
            return Interval(_parse_iso(str(self.scope["from"])), _parse_iso(str(self.scope["to"])))
        except KeyError as exc:
            raise JobError(f"scope needs 'from' and 'to': missing {exc}") from exc


# ------------------------------------------------------------------ queue files

def enqueue_job(data_root: Path, request: JobRequest) -> Path:
    """Publish the request into the queue atomically; refuse a duplicate job_id."""
    root = Path(data_root) / "jobs"
    target = root / _QUEUE_DIR / _file_name(request)
    for directory in (_QUEUE_DIR, _ACTIVE_DIR, _DONE_DIR):
        if (root / directory / _file_name(request)).exists():
            raise JobAlreadyQueued(f"job {request.job_id} is already in jobs/{directory}")
    (root / _QUEUE_DIR).mkdir(parents=True, exist_ok=True)
    staging = root / _QUEUE_DIR / f".{request.job_id}.part"
    staging.write_text(request.to_json(), encoding="utf-8")
    staging.replace(target)
    return target


def claim_next_job(data_root: Path) -> JobRequest | None:
    """Atomically move the oldest queued request into active and return it.

    The rename is the claim: a second claimer of the same file loses the race and moves on.
    Unreadable requests go to jobs/rejected/ instead of wedging the loop.
    """
    root = Path(data_root) / "jobs"
    (root / _ACTIVE_DIR).mkdir(parents=True, exist_ok=True)
    for path in sorted((root / _QUEUE_DIR).glob("*.json")):
        try:
            request = JobRequest.from_json(path.read_text(encoding="utf-8"))
        except JobError:
            _move(root, path, _REJECTED_DIR)
            continue
        try:
            path.replace(root / _ACTIVE_DIR / path.name)
        except FileNotFoundError:
            continue  # another claimer won
        return request
    return None


def requeue_active_job(data_root: Path, job_id: str) -> bool:
    """Move an active request back into the queue (the RESUME path)."""
    return _move_named(Path(data_root) / "jobs", job_id, _ACTIVE_DIR, _QUEUE_DIR)


def finish_job(data_root: Path, job_id: str) -> bool:
    """Move an active request to done: every terminal run outcome lands here, PAUSED does not."""
    return _move_named(Path(data_root) / "jobs", job_id, _ACTIVE_DIR, _DONE_DIR)


def _file_name(request: JobRequest) -> str:
    stamp = _parse_iso(request.created_at).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{request.job_id}.json"


def _move(jobs_root: Path, path: Path, directory: str) -> None:
    (jobs_root / directory).mkdir(parents=True, exist_ok=True)
    path.replace(jobs_root / directory / path.name)


def _move_named(jobs_root: Path, job_id: str, from_dir: str, to_dir: str) -> bool:
    for path in (jobs_root / from_dir).glob(f"*-{job_id}.json"):
        _move(jobs_root, path, to_dir)
        return True
    return False


# ------------------------------------------------------------------ recovery

def recover_interrupted_runs(conn: sqlite3.Connection, data_root: Path) -> dict[str, int]:
    """Dead worker, live data: mark orphaned runs INTERRUPTED and re-queue their requests.

    Holding the data-root lock proves no other worker is alive, so every non-terminal run row
    is an orphan. PAUSED rows are a deliberate stop, not a death — they stay.
    """
    interrupted: int = 0
    stuck = conn.execute(
        "SELECT run_id FROM runs WHERE lifecycle NOT IN ('FINISHED','TERMINATED','PAUSED')"
    ).fetchall()
    for row in stuck:
        conn.execute(
            "UPDATE runs SET lifecycle='TERMINATED', status='INTERRUPTED', finished_at_utc=? WHERE run_id=?",
            (_now(), row["run_id"]),
        )
    conn.commit()
    interrupted = len(stuck)
    requeued = 0
    for path in sorted((Path(data_root) / "jobs" / _ACTIVE_DIR).glob("*.json")):
        try:
            job_id = str(json.loads(path.read_text(encoding="utf-8"))["job_id"])
        except (json.JSONDecodeError, KeyError, OSError):
            _move(Path(data_root) / "jobs", path, _REJECTED_DIR)
            continue
        rows = conn.execute("SELECT lifecycle FROM runs WHERE job_id=?", (job_id,)).fetchall()
        if not rows or all(row["lifecycle"] in ("FINISHED", "TERMINATED") for row in rows):
            _move(Path(data_root) / "jobs", path, _QUEUE_DIR)
            requeued += 1
    return {"interrupted_runs": int(interrupted), "requeued_jobs": requeued}


# ------------------------------------------------------------------ worker loop

@dataclass(frozen=True)
class Command:
    job_id: str | None
    command: str
    sid: str
    issued_at: str


class CommandFlag:
    """The interruption flags the IPC thread sets and the worker thread observes.

    Pause and stop both end the *fetch* at the next page boundary; only stop gives the run a
    terminal status, so the runner sees one signal (`interrupt_requested`) and the loop tells
    the two apart afterwards.
    """

    def __init__(self) -> None:
        self._interrupt = threading.Event()
        self._stop = threading.Event()
        self._shutdown = threading.Event()

    def request_pause(self) -> None:
        self._interrupt.set()

    def request_stop(self) -> None:
        self._interrupt.set()
        self._stop.set()

    def request_shutdown(self) -> None:
        self.request_stop()
        self._shutdown.set()

    def reset_run_flags(self) -> None:
        self._interrupt.clear()
        self._stop.clear()

    @property
    def interrupt_requested(self) -> bool:
        return self._interrupt.is_set()

    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown.is_set()


class WorkerLoop:
    """Claims queued jobs one at a time on the data-root writer lock (§2.2, §5.2)."""

    def __init__(
        self,
        data_root: Path,
        *,
        page_source_builder: PageSourceBuilder | None = None,
        poll_seconds: float = 0.05,
        clock: Clock | None = None,
        idle_exit_seconds: float | None = None,
        deep_fetcher: DeepFetcher | None = None,
        deep_specs: SpecProvider | None = None,
    ) -> None:
        self.data_root = Path(data_root)
        self.flag = CommandFlag()
        self.exit_code: int = 0
        self.last_error: str | None = None
        self._builder = page_source_builder
        self._poll = poll_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._idle_exit = idle_exit_seconds
        self._idle_since = time.monotonic()
        self._deep_fetcher = deep_fetcher
        self._deep_specs = deep_specs
        self._commands: queue.Queue[Command] = queue.Queue()
        self._writer: WarehouseWriter | None = None
        self._current: JobRequest | None = None
        self._paused_job: str | None = None

    # -- called from the IPC thread
    def post_command(self, *, job_id: str | None, command: str, sid: str) -> None:
        """Signal the running loop immediately; the audit row is written on the loop thread.

        The flag must flip from the IPC thread, otherwise a PAUSE could not land at the next
        page boundary of a run that is already in flight — it would sit in the queue until the
        run finished, which is exactly what §2.3's "kết thúc page hiện hành" forbids.
        """
        if command == "PAUSE":
            self.flag.request_pause()
        elif command == "STOP_GRACEFUL":
            self.flag.request_stop()
        elif command == "FORCE_TERMINATE":
            self.flag.request_shutdown()
        self._commands.put(Command(job_id=job_id, command=command, sid=sid, issued_at=_now()))

    def run(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        try:
            # Production ingest writes the measured layout (segments); the environment can
            # still pin a root to the legacy object layout for comparison or recovery drills.
            layout = os.environ.get("SANTHAU_RAW_LAYOUT", LAYOUT_SEGMENTS)
            self._writer = WarehouseWriter.open(
                self.data_root, create_schema=True, role="worker", raw_layout=layout
            )
        except Exception as exc:
            self.exit_code = 3 if isinstance(exc, DataRootBusy) else 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            return
        try:
            recover_interrupted_runs(self._writer.conn, self.data_root)
            while True:
                self._drain_commands()
                if stop.is_set() or self.flag.shutdown_requested:
                    break
                if self._idle_exit is not None and time.monotonic() - self._idle_since >= self._idle_exit:
                    # Scheduled runs must end when the queue has been empty long enough, or the
                    # 20:00 trigger would be ignored as a second instance forever.
                    break
                request = claim_next_job(self.data_root)
                if request is None:
                    stop.wait(self._poll)
                    continue
                self._idle_since = time.monotonic()
                self._execute(request)
        except Exception:
            self.exit_code = 1
            self.last_error = traceback.format_exc()
            if sys.stderr is not None:
                traceback.print_exc()
        finally:
            if self._writer is not None:
                self._writer.close()
                self._writer = None

    # -- one job, claim to terminal row
    def _execute(self, request: JobRequest) -> None:
        writer = self._writer
        if writer is None:  # pragma: no cover - run() owns the writer
            return
        self.flag.reset_run_flags()
        self._current = request
        run_id = f"run-{uuid.uuid4().hex}"
        conn = writer.conn
        writer.register_run(run_id, request.job_id, request.job_kind, requested_by=request.requested_by)
        try:
            task_status, detail = self._dispatch(request, run_id)
        except Exception as exc:
            task_status, detail = "FAILED", {"error": f"{type(exc).__name__}: {exc}"[:200]}
        if self.flag.stop_requested:
            task_status, detail = "INTERRUPTED", {**detail, "stopped": True}
            writer.finish_run(run_id, "INTERRUPTED")
            finish_job(self.data_root, request.job_id)
        elif self.flag.interrupt_requested:
            task_status, detail = "INTERRUPTED", {**detail, "paused": True}
            conn.execute("UPDATE runs SET lifecycle='PAUSED' WHERE run_id=?", (run_id,))
            conn.commit()
            self._paused_job = request.job_id
        else:
            writer.finish_run(run_id, task_status)
            finish_job(self.data_root, request.job_id)
        conn.execute(
            "INSERT INTO run_tasks (run_id, task_kind, status, detail_json) VALUES (?,?,?,?)",
            (run_id, request.job_kind, task_status, json.dumps(detail, sort_keys=True, default=str)),
        )
        conn.commit()
        self._current = None

    def _dispatch(self, request: JobRequest, run_id: str) -> tuple[str, dict[str, Any]]:
        """Run the job kind; return the run_tasks status and its detail summary."""
        kind = request.job_kind
        if kind not in ("BACKFILL", "INCREMENTAL", "RECONCILE", *LANE_KINDS):
            return "BLOCKED", {"reason": f"KIND_{kind}_NOT_DISPATCHABLE"}
        source = request.sources[0] if request.sources else None
        if not isinstance(source, dict) or "source_key" not in source or "family" not in source:
            return "BLOCKED", {"reason": "NO_SOURCE_IN_REQUEST"}
        if kind in LANE_KINDS:
            assert self._writer is not None
            result = run_deep_batch(
                self._writer,
                run_id=run_id,
                family=str(source["family"]),
                lane=kind,
                scope=request.scope,
                fetcher=self._deep_fetcher,
                spec_provider=self._deep_specs,
                clock=self._clock,
            )
            return result.status, result.detail
        adapter_sha = str(source.get("adapter_sha") or "")
        if not adapter_sha:
            return "BLOCKED", {"reason": "NO_ADAPTER_SHA"}
        assert self._writer is not None
        page_source = self._builder(self._writer, request) if self._builder else None
        if page_source is None:
            return "BLOCKED", {"reason": "PAGE_SOURCE_UNAVAILABLE"}
        endpoint = str(source.get("endpoint") or SOURCES[str(source["source_key"])].endpoint)
        runner = Runner(
            writer=self._writer,
            source_key=str(source["source_key"]),
            family=str(source["family"]),
            endpoint=endpoint,
            adapter_sha=adapter_sha,
            page_source=page_source,
            clock=self._clock,
            stop_requested=lambda: self.flag.interrupt_requested,
        )
        if kind == "INCREMENTAL":
            outcomes = runner.incremental(run_id, now=self._clock())
        elif kind == "RECONCILE":
            gaps = runner.reconcile(run_id, request.interval())
            return "COMPLETED", {"gaps": len(gaps)}
        else:
            outcomes = runner.backfill(run_id, request.interval())
        leaves = [leaf for outcome in outcomes for leaf in _leaves(outcome)]
        counts: dict[str, int] = {}
        for leaf in leaves:
            counts[leaf.status] = counts.get(leaf.status, 0) + 1
        detail = {"intervals": len(leaves), "status_counts": counts, "rows": sum(o.rows for o in leaves)}
        return _judge(leaves), detail

    # -- commands
    def _drain_commands(self) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            self._apply(command)

    def _apply(self, command: Command) -> None:
        writer = self._writer
        if writer is None:  # pragma: no cover
            return
        job_id = command.job_id or (self._current.job_id if self._current else None) or "unassigned"
        seq = int(
            writer.conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM job_commands WHERE job_id=?", (job_id,)
            ).fetchone()[0]
        )
        writer.conn.execute(
            "INSERT INTO job_commands (job_id, seq, command, issued_at_utc, issued_by_sid, acknowledged_at_utc)"
            " VALUES (?,?,?,?,?,?)",
            (job_id, seq, command.command, command.issued_at, command.sid, _now()),
        )
        writer.conn.commit()
        if command.command == "PAUSE":
            self.flag.request_pause()
        elif command.command == "STOP_GRACEFUL":
            self.flag.request_stop()
        elif command.command == "FORCE_TERMINATE":
            self.flag.request_shutdown()
        elif command.command == "RESUME" and command.job_id and self._paused_job == command.job_id:
            requeue_active_job(self.data_root, command.job_id)
            self._paused_job = None


class WorkerController:
    """The pipe protocol's server side: ops land here once the transport has authenticated.

    `status` reads run rows through a dedicated read-only connection owned by the IPC thread —
    WAL lets it observe the worker's committed writes without contending for the write lock.
    """

    def __init__(self, loop: WorkerLoop) -> None:
        self.loop = loop
        self._conn: sqlite3.Connection | None = None

    def __call__(self, payload: dict, sid: str) -> dict:
        op = payload.get("op")
        if op == "ping":
            return {"ok": True, "worker": True}
        if op == "status":
            job_id = payload.get("job_id")
            if not isinstance(job_id, str) or not job_id:
                return {"ok": False, "error": "JOB_ID_REQUIRED"}
            return {"ok": True, "job_id": job_id, **self._status(job_id)}
        if op == "command":
            command = payload.get("command")
            if command not in COMMANDS:
                return {"ok": False, "error": "UNKNOWN_COMMAND"}
            job_id = payload.get("job_id")
            self.loop.post_command(
                job_id=job_id if isinstance(job_id, str) and job_id else None,
                command=str(command),
                sid=sid,
            )
            return {"ok": True, "queued": True}
        return {"ok": False, "error": "UNKNOWN_OP"}

    def _status(self, job_id: str) -> dict[str, Any]:
        try:
            conn = self._read_conn()
            rows = conn.execute(
                "SELECT run_id, lifecycle, status, started_at_utc, finished_at_utc FROM runs"
                " WHERE job_id=? ORDER BY started_at_utc",
                (job_id,),
            ).fetchall()
        except sqlite3.Error:
            return {"runs": [], "error": "NOT_READY"}
        runs = []
        for row in rows:
            tasks = conn.execute(
                "SELECT task_kind, status, detail_json FROM run_tasks WHERE run_id=?", (row["run_id"],)
            ).fetchall()
            runs.append(
                {
                    "run_id": row["run_id"],
                    "lifecycle": row["lifecycle"],
                    "status": row["status"],
                    "started_at_utc": row["started_at_utc"],
                    "finished_at_utc": row["finished_at_utc"],
                    "tasks": [
                        {"task_kind": task["task_kind"], "status": task["status"],
                         "detail": json.loads(task["detail_json"]) if task["detail_json"] else None}
                        for task in tasks
                    ],
                }
            )
        return {"runs": runs}

    def _read_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            # Opened on the IPC thread, closed on the main thread after that thread is gone, so
            # the sqlite3 per-thread check would reject close(). Read-only + serialized, so
            # sharing the handle is safe, and mode=ro never flips journal mode under the writer.
            self._conn = connect_read_only(
                self.loop.data_root / "warehouse.sqlite", check_same_thread=False
            )
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# ------------------------------------------------------------------ helpers

def _leaves(outcome: IntervalOutcome) -> list[IntervalOutcome]:
    if outcome.children:
        return [leaf for child in outcome.children for leaf in _leaves(child)]
    return [outcome]


def _judge(leaves: list[IntervalOutcome]) -> str:
    if not leaves:
        return "COMPLETED"  # everything already proven: the resolver said so
    if any(leaf.status == "BLOCKED" for leaf in leaves):
        return "BLOCKED"
    if all(leaf.status == "COMPLETE" for leaf in leaves):
        return "COMPLETED"
    return "PARTIAL"


def _parse_iso(text: str) -> datetime:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise JobError(f"{text!r} is not an ISO-8601 timestamp") from exc


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
