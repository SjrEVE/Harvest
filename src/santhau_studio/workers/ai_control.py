"""AI Control Lane — a read-only state channel that never waits for the worker.

The old control pipe serves requests inline on the worker's loop thread, so a
2.2 GB serving rebuild made `ping` die with PipeClosedError. Here the worker only
*pushes* a tiny state update into a lock-free-in-practice buffer; a separate pipe
reads that buffer. No handler may call into WorkerLoop or wait on disk I/O.
"""
from __future__ import annotations

import contextlib
import json
import queue
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from santhau_studio.domain.ai_state import AIState
from santhau_studio.platform_win.process import current_process_sid
from santhau_studio.workers.ipc import PipeServer, worker_pipe_name

MAX_EVENTS = 500
STALE_AFTER_SECONDS = 5.0
_FIELDS = frozenset(AIState.__dataclass_fields__)

_state_lock = threading.Lock()
_state = AIState.initial()
_events: deque[dict] = deque(maxlen=MAX_EVENTS)
_event_queue: queue.Queue[str | None] = queue.Queue(maxsize=2000)
_event_path: Path | None = None
_flusher: threading.Thread | None = None
_flusher_stop = threading.Event()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _age(ts: str | None) -> float:
    if ts is None:
        return float("inf")
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds()
    except ValueError:
        return float("inf")


def ai_pipe_name(user: str | None = None, *, data_root: str | Path | None = None) -> str:
    """Same per-user / per-root scoping as the control pipe, distinct suffix."""
    return worker_pipe_name(user=user, data_root=data_root).removesuffix("-worker") + "-ai"


def _start_flusher() -> None:
    global _flusher
    if _flusher is not None and _flusher.is_alive():
        return
    _flusher_stop.clear()

    def run() -> None:
        while not _flusher_stop.is_set():
            try:
                line = _event_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None or _event_path is None:
                continue
            try:
                with _event_path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError:
                pass  # observation is best-effort; never block the worker

    _flusher = threading.Thread(target=run, name="santhau-ai-events", daemon=True)
    _flusher.start()


def configure(data_root: str | Path | None = None) -> None:
    """Reset the lane (also used by tests); optionally persist events to JSONL."""
    global _state, _events, _event_path
    with _state_lock:
        _state = AIState.initial()
        _events = deque(maxlen=MAX_EVENTS)
        _event_path = Path(data_root) / "ai-control" / "events.jsonl" if data_root else None
    if _event_path is not None:
        _event_path.parent.mkdir(parents=True, exist_ok=True)
    _start_flusher()


def emit(phase: str, **fields) -> dict:
    """Push a state transition. Must stay O(1) and never wait on I/O or the loop."""
    global _state
    known = {key: value for key, value in fields.items() if key in _FIELDS}
    with _state_lock:
        merged = _state.to_dict()
        merged.update(known)
        merged["phase"] = phase
        merged["seq"] = merged["seq"] + 1
        merged["updated_at_utc"] = _now()
        state = AIState(**merged)
        _state = state
        event = {"seq": state.seq, "phase": phase, "at": state.updated_at_utc}
        _events.append(event)
    with contextlib.suppress(queue.Full):  # drop an observation, never stall the writer
        _event_queue.put_nowait(json.dumps(event, sort_keys=True))
    return state.to_dict()


def handle(payload: dict) -> dict:
    """Answer from memory only. Hard bound: no disk read, no WorkerLoop call."""
    op = payload.get("op")
    with _state_lock:
        snapshot = _state.to_dict()
        events = list(_events)
    if op == "state":
        return {**snapshot, "stale": _age(snapshot["updated_at_utc"]) > STALE_AFTER_SECONDS}
    if op == "events":
        since = payload.get("since_seq")
        selected = [e for e in events if since is None or e["seq"] > since]
        return {"ok": True, "events": selected[-200:]}
    return {"ok": False, "error": "UNKNOWN_OP"}


class AIControlServer:
    """Dedicated named pipe. Read-only by design: no command op is accepted."""

    def __init__(self, *, data_root: str | Path | None = None, allowed_sid: str | None = None):
        self.name = ai_pipe_name(data_root=data_root)
        sid = allowed_sid or current_process_sid()
        if sid is None:
            raise RuntimeError("cannot read the process token; refusing to serve the AI lane")
        self._server = PipeServer(self.name, allowed_sid=sid, handler=self)

    def __call__(self, payload: dict, _sid: str) -> dict:
        return handle(payload)

    def serve_until(self, stop: threading.Event) -> None:
        self._server.serve_until(stop)

    def wake(self) -> None:
        self._server.wake()
