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
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from santhau_studio.collectors.lane_source import egp_page_source
from santhau_studio.platform_win.process import current_process_sid
from santhau_studio.workers.ipc import PipeNameConflictError, PipeServer, worker_pipe_name
from santhau_studio.workers.jobs import WorkerController, WorkerLoop

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
    del writer
    source = request.sources[0] if request.sources else None
    if not isinstance(source, dict) or "family" not in source:
        return None
    return egp_page_source(family=str(source["family"]))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="santhau-collector")
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--page-source-file", type=Path, default=None)
    parser.add_argument("--poll-seconds", type=float, default=0.05)
    args = parser.parse_args(argv)

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

    loop = WorkerLoop(args.data_root, page_source_builder=builder, poll_seconds=args.poll_seconds)
    controller = WorkerController(loop)
    try:
        server = PipeServer(worker_pipe_name(), allowed_sid=sid, handler=controller)
    except PipeNameConflictError:
        _log_failure(args.data_root, f"pipe {worker_pipe_name()} is already owned by another process")
        print(f"pipe {worker_pipe_name()} is already owned by another process", file=sys.stderr)
        return EXIT_PIPE_CONFLICT

    stop = threading.Event()
    loop_thread = threading.Thread(target=loop.run, args=(stop,), name="santhau-worker", daemon=True)
    pipe_thread = threading.Thread(target=server.serve_until, args=(stop,), name="santhau-pipe", daemon=True)
    loop_thread.start()
    pipe_thread.start()
    loop_thread.join()
    stop.set()
    server.wake()
    pipe_thread.join(timeout=5)
    controller.close()
    if loop.exit_code != 0:
        _log_failure(args.data_root, f"worker loop exit {loop.exit_code}\n{loop.last_error or ''}")
    return loop.exit_code


if __name__ == "__main__":
    sys.exit(main())
