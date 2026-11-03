"""Canary driver: prints the owner-approval commands and refuses to run without approval.

Two stages per family, matching the ladder in `collectors.canary`:

1. `probe` — one page, NO date filter, records adapter_health evidence. Safe to run daily.
2. `collect` — one VN day through `Runner.backfill`; this is what can produce COMPLETE proof,
   and it is allowed only after a verified boundary rule exists.

Nothing here calls the real source unless BOTH `--execute` and `SANTHAU_CANARY_APPROVED=<owner>`
are present. `plan` prints exactly what would run, for the approval record.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from santhau_studio.collectors.canary import COLLECTION_PROVEN, canary_state, probe_family
from santhau_studio.collectors.source_registry import FAMILY_ORDER, SOURCE_EGP_HOME, SOURCES
from santhau_studio.domain.coverage import vn_calendar_day
from santhau_studio.storage import coverage
from santhau_studio.storage.warehouse import WarehouseWriter
from santhau_studio.workers.runner import Runner

ADAPTER_SHA_ENV = "SANTHAU_CANARY_ADAPTER_SHA"
APPROVAL_ENV = "SANTHAU_CANARY_APPROVED"
EXIT_REFUSED = 2


def _family_day(day: str | None) -> date:
    if day:
        return date.fromisoformat(day)
    return (datetime.now().astimezone().date() - timedelta(days=1))


def _rule(conn, family: str, adapter_sha: str):
    row = coverage.boundary_rule(
        conn, source_key=SOURCE_EGP_HOME, family=family,
        endpoint=SOURCES[SOURCE_EGP_HOME].endpoint, adapter_sha=adapter_sha,
    )
    return row


def print_plan(day: str | None) -> int:
    target = _family_day(day)
    day_interval = vn_calendar_day(target)
    print(f"# Canary plan for {target.isoformat()} (VN day {day_interval.from_utc}..{day_interval.to_utc})")
    print("# Run probe first; collection requires a verified boundary rule written by the owner.")
    for family in FAMILY_ORDER:
        print()
        print(f"## {family}")
        print(f"uv run python tools/canary.py probe --family {family} --data-root <root> --execute")
        print(f"uv run python tools/canary.py collect --family {family} --day {target.isoformat()}"
              f" --data-root <root> --execute")
        print("  expected: probe ok (rows/total readable, no date filter sent);")
        print("            collect -> intervals COMPLETE with proof, canary_state=COLLECTION_PROVEN")
    return 0


def _approved() -> str | None:
    if "--execute" not in sys.argv:
        return None
    return os.environ.get(APPROVAL_ENV) or None


def run_probe(args: argparse.Namespace) -> int:
    owner = _approved()
    if owner is None:
        print(f"REFUSED: pass --execute with {APPROVAL_ENV}=<owner> to touch the real source",
              file=sys.stderr)
        return EXIT_REFUSED
    adapter_sha = os.environ.get(ADAPTER_SHA_ENV, "")
    if not adapter_sha:
        print(f"REFUSED: set {ADAPTER_SHA_ENV} to the adapter hash the rule will cite", file=sys.stderr)
        return EXIT_REFUSED
    writer = WarehouseWriter.open(args.data_root, create_schema=True, role="canary")
    try:
        result = probe_family(writer, family=args.family, adapter_sha=adapter_sha)
    finally:
        writer.close()
    print(f"probe {args.family}: {result} (approved by {owner})")
    return 0 if result.get("ok") else 3


def run_collect(args: argparse.Namespace) -> int:
    owner = _approved()
    if owner is None:
        print(f"REFUSED: pass --execute with {APPROVAL_ENV}=<owner>", file=sys.stderr)
        return EXIT_REFUSED
    adapter_sha = os.environ.get(ADAPTER_SHA_ENV, "")
    if not adapter_sha:
        print(f"REFUSED: set {ADAPTER_SHA_ENV}", file=sys.stderr)
        return EXIT_REFUSED
    writer = WarehouseWriter.open(args.data_root, create_schema=True, role="canary")
    try:
        state = canary_state(writer.conn, SOURCE_EGP_HOME, args.family, adapter_sha)
        if state["state"] == COLLECTION_PROVEN:
            print(f"{args.family} already COLLECTION_PROVEN; nothing to do")
            return 0
        if not _rule(writer.conn, args.family, adapter_sha):
            print(f"REFUSED: no canary-verified boundary rule for {args.family}/{adapter_sha};"
                  " the probe must pass and the owner must verify the rule first", file=sys.stderr)
            return EXIT_REFUSED
        day_interval = vn_calendar_day(_family_day(args.day))
        run_id = f"canary-{uuid.uuid4().hex[:10]}"
        writer.register_run(run_id, f"canary-{args.family}", "BACKFILL", requested_by=owner)
        runner = Runner(
            writer=writer, source_key=SOURCE_EGP_HOME, family=args.family,
            endpoint=SOURCES[SOURCE_EGP_HOME].endpoint, adapter_sha=adapter_sha,
            page_source=_live_page_source(args.family),
        )
        outcomes = runner.backfill(run_id, day_interval)
        writer.finish_run(run_id, "COMPLETED" if all(o.status == "COMPLETE" for o in outcomes) else "PARTIAL")
        state = canary_state(writer.conn, SOURCE_EGP_HOME, args.family, adapter_sha)
        print({"family": args.family, "run_id": run_id,
               "outcomes": [o.status for o in outcomes], "state": state["state"]})
        return 0 if state["state"] == COLLECTION_PROVEN else 4
    finally:
        writer.close()


def _live_page_source(family: str):
    from santhau_studio.collectors.lane_source import egp_page_source

    return egp_page_source(family=family)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="print per-family commands for the approval record")
    plan.add_argument("--day")
    probe = sub.add_parser("probe", help="one page, no date filter")
    probe.add_argument("--family", required=True, choices=FAMILY_ORDER)
    probe.add_argument("--data-root", type=Path, required=True)
    probe.set_defaults(func=run_probe)
    collect = sub.add_parser("collect", help="one VN day through the runner")
    collect.add_argument("--family", required=True, choices=FAMILY_ORDER)
    collect.add_argument("--day")
    collect.add_argument("--data-root", type=Path, required=True)
    collect.set_defaults(func=run_collect)
    args = parser.parse_args(argv)
    if args.command == "plan":
        return print_plan(args.day)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
