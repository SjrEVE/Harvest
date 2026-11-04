"""Export only verified source configuration, never source records or coverage proof."""

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from santhau_studio.linux.config import PROFILE_COLUMNS, adapter_code_sha, read_profile

    with closing(
        sqlite3.connect(
            (args.data_root / "warehouse.sqlite").resolve().as_uri() + "?mode=ro", uri=True
        )
    ) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT "
            + ",".join(PROFILE_COLUMNS)
            + " FROM filter_boundary_rules WHERE source_key='egp_home' AND canary_verified=1 ORDER BY family,rule_version DESC,verified_at_utc DESC"
        ).fetchall()
    selected = {}
    evidence = []
    for row in rows:
        if row["family"] in selected:
            continue
        value = dict(row)
        params = json.loads(value["conversion_params"])
        evidence.append(hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest())
        value["conversion_params"] = json.dumps(
            {k: params.get(k) for k in ("to_minus", "wire_format", "date_field", "query_contract")},
            sort_keys=True,
        )
        selected[row["family"]] = value
    result = {
        "schema": 1,
        "level": "RULE_VERIFIED",
        "adapter_code_sha256": adapter_code_sha(),
        "provenance": "Previously owner-approved Windows RC3 boundary verification. No Linux live proof claimed.",
        "source_rule_sha256": evidence,
        "rules": list(selected.values()),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise ValueError("Output exists; refusing to overwrite")
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    read_profile(args.output)
    print("PROFILE_OK", len(selected), "rules; no records or coverage included")


if __name__ == "__main__":
    main()
