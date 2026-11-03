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
from collections.abc import Sequence
from pathlib import Path

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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="santhau-collector")
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--page-source-file", type=Path, default=None)
    parser.add_argument("--poll-seconds", type=float, default=0.05)
    args = parser.parse_args(argv)

    sid = current_process_sid()
    if sid is None:
        print("cannot read the process token; refusing to serve (§2.4 fail-closed)", file=sys.stderr)
        return EXIT_STARTUP_FAILED
    builder = None
    if args.page_source_file is not None:
        builder = _load_builder(args.page_source_file)
        if builder is None:
            return EXIT_BAD_PAGE_SOURCE

    loop = WorkerLoop(args.data_root, page_source_builder=builder, poll_seconds=args.poll_seconds)
    controller = WorkerController(loop)
    try:
        server = PipeServer(worker_pipe_name(), allowed_sid=sid, handler=controller)
    except PipeNameConflictError:
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
    return loop.exit_code


if __name__ == "__main__":
    sys.exit(main())
