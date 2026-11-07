"""The worker process entry point (§2.3): claim jobs, serve the control pipe, exit by code.

`python -m santhau_studio.workers.service --data-root <root>` runs the loop in one thread and
the named-pipe server in another; the process lives until FORCE_TERMINATE or a fatal startup
error. Exit codes: 0 normal, 2 pipe name conflict (squatting or a second worker), 3 data root
busy (a writer already holds it), 1 other startup failure.

`--page-source-file` is the offline seam: a .py module defining `build(writer, request)` that
returns a page source (or None). The live lane is wired the same way once its canary passes —
the loop itself never knows which one it is talking to.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from santhau_studio.collectors.live_builder import live_builder
from santhau_studio.domain.ai_bridge import emit as ai_emit
from santhau_studio.platform_win.process import current_process_sid, register_runtime
from santhau_studio.platform_win.shutdown_signal import open_shutdown_event, shutdown_requested
from santhau_studio.workers.ai_control import AIControlServer
from santhau_studio.workers.ipc import (
    PipeNameConflictError,
    PipeServer,
    PipeTransportError,
    worker_pipe_name,
)
from santhau_studio.workers.jobs import WorkerController, WorkerLoop
from santhau_studio.workers.maintenance import ACTIONS, perform

EXIT_OK = 0
EXIT_STARTUP_FAILED = 1
EXIT_PIPE_CONFLICT = 2
EXIT_DATA_ROOT_BUSY = 3
EXIT_BAD_PAGE_SOURCE = 5


def _load_builder(path: Path):
    spec = importlib.util.spec_from_file_location("santhau_page_source", path)
    if spec is None or spec.loader is None:
        print(f"cannot load page source module {path}", file=sys.stderr)
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    builder = getattr(module, "build", None)
    if not callable(builder):
        print(f"{path} does not define build(writer, request)", file=sys.stderr)
        return None
    return builder


def _log_failure(data_root: Path, text: str) -> None:
    """A GUI-subsystem build has no console: startup failures must land somewhere findable."""
    try:
        logs = Path(data_root) / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        with (logs / "worker.log").open("a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {text}\n")
    except OSError:
        pass


def _default_builder(writer, request):
    """The live lane, used when no offline page-source module is supplied.

    It performs no request until the runner asks for a page, and the runner only asks once a
    canary-verified boundary rule exists — so an unverified source cannot be hit by accident.
    """
    return live_builder(writer, request)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="santhau-collector")
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--maintenance", choices=ACTIONS)
    parser.add_argument("--source")
    parser.add_argument("--page-source-file", type=Path, default=None)
    parser.add_argument("--poll-seconds", type=float, default=0.05)
    parser.add_argument("--scheduled", action="store_true", help="khởi chạy bởi Task Scheduler")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--idle-exit-seconds", type=float, default=None,
                        help="thoát khi hàng đợi trống đủ lâu (scheduled runs)")
    args = parser.parse_args(argv)

    from santhau_studio.workers import ai_control
    ai_control.configure(args.data_root)
    ai_emit("STARTING", worker_pid=os.getpid())

    shutdown_handle = open_shutdown_event()
    if shutdown_requested(shutdown_handle):
        return EXIT_OK
    register_runtime()
    if args.maintenance:
        result = perform(args.maintenance, args.data_root, args.source)
        if sys.stdout is not None:
            print(json.dumps(result, ensure_ascii=True), flush=True)
        return 0 if result["ok"] else 1

    sid = current_process_sid()
    if sid is None:
        _log_failure(args.data_root, "cannot read the process token; refusing to serve (§2.4 fail-closed)")
        print("cannot read the process token; refusing to serve (§2.4 fail-closed)", file=sys.stderr)
        return EXIT_STARTUP_FAILED
    builder = _default_builder
    if args.page_source_file is not None:
        builder = _load_builder(args.page_source_file)
        if builder is None:
            _log_failure(args.data_root, f"page source module unusable: {args.page_source_file}")
            return EXIT_BAD_PAGE_SOURCE

    from santhau_studio.collectors.cgttrg import public_spec
    from santhau_studio.collectors.public_deep import PublicDeepFetcher
    deep_fetcher, deep_specs, deep_factory = None, public_spec, PublicDeepFetcher
    if os.environ.get("SANTHAU_OFFLINE_ACCEPTANCE_ROOT") == str(args.data_root.resolve()):
        from santhau_studio.collectors.offline_acceptance import builder as offline_builder
        from santhau_studio.collectors.offline_acceptance import deep_fetch, deep_spec, enabled
        if not enabled(args.data_root):
            return EXIT_BAD_PAGE_SOURCE
        builder, deep_fetcher, deep_specs = offline_builder, deep_fetch, deep_spec
        deep_factory = None

    loop = WorkerLoop(
        args.data_root,
        page_source_builder=builder, deep_fetcher=deep_fetcher, deep_specs=deep_specs,
        deep_factory=deep_factory,
        poll_seconds=args.poll_seconds,
        idle_exit_seconds=args.idle_exit_seconds,
        scheduled=args.scheduled,
    )
    controller = WorkerController(loop)
    try:
        server = PipeServer(worker_pipe_name(data_root=args.data_root), allowed_sid=sid, handler=controller)
    except PipeNameConflictError:
        _log_failure(args.data_root, f"pipe {worker_pipe_name(data_root=args.data_root)} is already owned by another process")
        print(f"pipe {worker_pipe_name(data_root=args.data_root)} is already owned by another process", file=sys.stderr)
        return EXIT_OK if args.scheduled else EXIT_PIPE_CONFLICT

    stop = threading.Event()
    loop_thread = threading.Thread(target=loop.run, args=(stop,), name="santhau-worker", daemon=True)
    pipe_thread = threading.Thread(target=server.serve_until, args=(stop,), name="santhau-pipe", daemon=True)
    ai_stop = threading.Event()
    ai_server = None
    try:
        ai_server = AIControlServer(data_root=args.data_root)
        threading.Thread(target=ai_server.serve_until, args=(ai_stop,),
                         name="santhau-ai", daemon=True).start()
    except (PipeNameConflictError, PipeTransportError, RuntimeError, OSError) as exc:
        _log_failure(args.data_root, f"AI control lane unavailable: {exc}")
        ai_server = None  # observation is optional; never fail the worker
    ai_emit("IDLE", worker_pid=os.getpid())
    loop_thread.start()
    pipe_thread.start()
    while loop_thread.is_alive():
        if shutdown_requested(shutdown_handle):
            loop.flag.request_shutdown()
            stop.set()
        loop_thread.join(timeout=0.1)
    stop.set()
    ai_emit("STOPPED", worker_pid=os.getpid())
    ai_stop.set()
    if ai_server is not None:
        ai_server.wake()
    server.wake()
    pipe_thread.join(timeout=5)
    controller.close()
    if loop.exit_code != 0:
        _log_failure(args.data_root, f"worker loop exit {loop.exit_code}\n{loop.last_error or ''}")
    if args.scheduled and loop.exit_code == EXIT_DATA_ROOT_BUSY:
        # A scheduled launch that finds a live worker is not a failure: the morning run may still
        # be working, or the user started one interactively. Skip cleanly and record why.
        _log_failure(args.data_root, "scheduled run skipped: data root already busy")
        return 0
    return loop.exit_code


if __name__ == "__main__":
    sys.exit(main())
