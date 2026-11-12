"""One-button backfill for the Linux server build.

Boss's rule: walk backwards from today, and *skip anything already proved*.
The evidence source is ``collection_scope.report()`` — the very same report the
"Muc do thu thap" UI uses. A second, finer-grained skip already exists in the
runner (``coverage.resolve`` + MISSING_ONLY) and is deliberately not duplicated.
"""
from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from santhau_studio.linux.config import make_jobs
from santhau_studio.storage import collection_scope
from santhau_studio.storage.warehouse import connect_read_only
from santhau_studio.workers.jobs import enqueue_job

SKIP_QUEUE = frozenset(("active", "queued"))
DEFAULT_LIMITS = {
    "max_pages": 1000, "max_requests": 3000, "max_seconds": 3600,
    "pacing_seconds": 3, "connect_timeout_seconds": 15, "max_attempts": 4,
}


def _today() -> date:
    return datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()


def extend_scope_to_today(scope: dict, today: date) -> dict:
    """Report windows up to *today* without rewriting the stored scope."""
    if today.isoformat() <= scope["date_to"]:
        return scope
    return {**scope, "date_to": today.isoformat()}


def plan_missing(report: dict, *, today: date | None = None) -> list[dict]:
    """Group unproved day/family windows into contiguous newest-first batches."""
    today = today or _today()
    per_day: dict[date, set[str]] = defaultdict(set)
    for window in report["windows"]:
        if window["state"] == "proved":
            continue  # already has data -> skip, do not crawl again
        if SKIP_QUEUE.intersection(window.get("queue") or ()):
            continue  # already queued/running -> do not enqueue twice
        day = date.fromisoformat(window["date"])
        if day > today:
            continue
        per_day[day].add(window["family"])

    batches: list[dict] = []
    current: dict | None = None
    for day in sorted(per_day, reverse=True):  # today -> past
        families = tuple(sorted(per_day[day]))
        if current and current["families"] == families and (current["_last"] - day).days == 1:
            current["days"].append(day.isoformat())
            current["from"] = day.isoformat()
            current["_last"] = day
            current["windows"] += len(families)
        else:
            current = {
                "from": day.isoformat(), "to": day.isoformat(), "families": families,
                "days": [day.isoformat()], "_last": day, "windows": len(families),
            }
            batches.append(current)
    for batch in batches:
        del batch["_last"]
    return batches


def enqueue_missing(root: Path, *, execute: bool = False, today: date | None = None) -> dict:
    """Dry-run by default. With execute=True, enqueue one BACKFILL job per family/batch."""
    today = today or _today()
    root = Path(root)
    db = root / "warehouse.sqlite"
    if not db.exists():
        return {"ok": True, "dry_run": not execute, "jobs": [], "batches": 0,
                "skipped_proved": 0, "date_from": None, "date_to": None,
                "note": "Kho chua khoi tao; chay `init` truoc."}
    with closing(connect_read_only(db)) as conn:
        conn.row_factory = sqlite3.Row
        scope = collection_scope.load(conn, required=False)
        if scope is None:
            return {"ok": True, "dry_run": not execute, "jobs": [], "batches": 0,
                    "skipped_proved": 0, "date_from": None, "date_to": None,
                    "note": "Chua cau hinh pham vi; chay `configure-scope` truoc."}
        report_scope = extend_scope_to_today(scope, today)
        report = collection_scope.report(conn, root, scope=report_scope)
        extended = report_scope is not scope

    batches = plan_missing(report, today=today)
    proved = sum(1 for w in report["windows"] if w["state"] == "proved")
    result = {
        "ok": True, "dry_run": not execute, "jobs": [], "batches": len(batches),
        "skipped_proved": proved,
        "date_from": scope["date_from"], "date_to": today.isoformat(),
        "scope_extended_to_today": extended,
        "note": "Keo nguoc tu hom nay ve date_from; bo qua ngay/family da co bang chung.",
    }
    if not execute:
        result["plan"] = batches
        return result

    for batch in batches:
        for family in batch["families"]:
            jobs = make_jobs(root, mode="BACKFILL", family=family,
                             first=batch["from"], last=batch["to"], limits=DEFAULT_LIMITS)
            for job in jobs:
                enqueue_job(root, job)
                result["jobs"].append(job.job_id)
    return result


def auto_pull(root: Path, *, execute: bool = False, run: bool = True,
              today: date | None = None) -> dict:
    """The single button. Windows is intentionally refused (Boss crawls there)."""
    if not sys.platform.startswith("linux"):
        raise ValueError(
            "Lenh `auto` chi danh cho ban server Linux; "
            "ban Windows de Boss tu cam may nha cao phu."
        )
    result = enqueue_missing(root, execute=execute, today=today)
    if execute and run and result["jobs"]:
        from santhau_studio.linux.cli import run_worker

        result["worker_exit"] = run_worker(root)
    return result
