"""Export scoped coverage metadata to stdout, without modifying the remote warehouse."""

import argparse
import json
import sqlite3
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--from", dest="first", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="last", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    if args.first > args.last:
        raise ValueError("Invalid scope")
    local = timezone(timedelta(hours=7))
    first = datetime.combine(args.first, time(), local).astimezone(UTC).isoformat().replace("+00:00", "Z")
    last = datetime.combine(args.last + timedelta(days=1), time(), local).astimezone(UTC).isoformat().replace("+00:00", "Z")
    conn = sqlite3.connect((args.data_root / "warehouse.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("BEGIN")
    try:
        result = {"root": str(args.data_root), "from_utc": first, "to_utc": last, "read_only": True,
                  "snapshot_at_utc": datetime.now(UTC).isoformat(), "tables": {}, "queue": []}
        for table in ("coverage_intervals", "coverage_proofs", "reconcile_gaps"):
            result["tables"][table] = [dict(row) for row in conn.execute(
                f"SELECT * FROM {table} WHERE source_key='egp_home' AND interval_from_utc<? AND interval_to_utc>?", (last, first))]
        result["tables"]["filter_boundary_rules"] = [dict(row) for row in conn.execute("SELECT * FROM filter_boundary_rules WHERE source_key='egp_home'")]
        result["reconcile_runs"] = [dict(row) for row in conn.execute("SELECT job_kind,lifecycle,status,COUNT(*) total FROM runs WHERE job_kind='RECONCILE' GROUP BY 1,2,3")]
        for state in ("active", "queued"):
            for path in (args.data_root / "jobs" / state).glob("*.json"):
                value = json.loads(path.read_text(encoding="utf-8"))
                result["queue"].append({"state": state, "request": value})
        print(json.dumps(result, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
