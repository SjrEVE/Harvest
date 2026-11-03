"""G0-A / A8 packaging spike — fake headless worker.

Emits line-delimited JSON status to stdout so the GUI can show that a separate
long-lived process survives independently of the QML viewer.

It performs no network I/O and touches no data root: every number below is
invented in this file. The receipt vocabulary mirrors §2.3 so the spike exercises
the shape the G2 worker will have to honour.
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from datetime import UTC, datetime

JOB_ID = "spike-job-0000"
SYNTHETIC_PAGES = 6
SYNTHETIC_RECORDS_PER_PAGE = 10


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def emit(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _force_utf8_stdout() -> None:
    """Status lines carry Vietnamese; a cp1252 console would raise on write.

    With --windows-console-mode=disable and no parent pipe, stdout can be None, so
    the reconfigure is best-effort rather than assumed.
    """
    stream = sys.stdout
    if stream is None:
        return
    with contextlib.suppress(AttributeError, OSError, ValueError):
        stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    _force_utf8_stdout()
    emit(
        {
            "job_id": JOB_ID,
            "state": "started",
            "at": _stamp(),
            "note": "worker giả lập, không I/O mạng",
        }
    )
    for page in range(1, SYNTHETIC_PAGES + 1):
        time.sleep(0.6)
        emit(
            {
                "job_id": JOB_ID,
                "state": "page_committed",
                "page_no": page,
                "records": SYNTHETIC_RECORDS_PER_PAGE,
                "attempt": 1,
                "response_received": True,
                "page_committed": True,
                "proof_verified": False,
                "snapshot_published": False,
                "at": _stamp(),
                "note": "fixture tổng hợp — chưa có proof",
            }
        )
    emit(
        {
            "job_id": JOB_ID,
            "state": "finished",
            "run_status": "PARTIAL",
            "records": SYNTHETIC_PAGES * SYNTHETIC_RECORDS_PER_PAGE,
            "pages": SYNTHETIC_PAGES,
            "at": _stamp(),
            "note": "PARTIAL theo §4.1: không có terminal evidence",
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
