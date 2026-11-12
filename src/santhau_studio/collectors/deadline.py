"""Bound caller wall time while the in-flight operation retains its host lease."""

import queue
import threading


class RequestDeadlineExceeded(TimeoutError):
    pass


def until_deadline(operation, seconds, cleanup=None):
    if seconds is None:
        return operation(threading.Event())
    if seconds <= 0:
        raise RequestDeadlineExceeded("REQUEST_DEADLINE_EXCEEDED")
    result = queue.Queue(maxsize=1)
    cancelled = threading.Event()

    def run():
        try:
            value = operation(cancelled)
        except BaseException as exc:
            result.put((False, exc))
        else:
            result.put((True, value))

    threading.Thread(target=run, daemon=True, name="bounded-http-request").start()
    try:
        ok, value = result.get(timeout=seconds)
    except queue.Empty:
        cancelled.set()
        if cleanup:
            # Closing an owned connection interrupts I/O; never block the deadline on cleanup.
            threading.Thread(target=cleanup, daemon=True, name="http-deadline-cleanup").start()
        raise RequestDeadlineExceeded("REQUEST_DEADLINE_EXCEEDED") from None
    if not ok:
        raise value
    return value
