#!/usr/bin/env python3
"""A3 — count real-procurement-data markers across tracked files at a revision.

Read-only: pulls blobs with `git show`, never touches a working tree. Emits masked
counts only (no record values), so the report is safe to commit.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

MARKERS: dict[str, re.Pattern[str]] = {
    "vn_org_name": re.compile(
        r"Công ty|Doanh nghiệp|Hợp tác xã|Ban Quản lý|Sở [A-ZÀ-ỸĐ]|UBND|Trung tâm|"
        r"Cục [A-ZÀ-ỸĐ]|Nhà máy|Tổng công ty|Bệnh viện|Trường Đại học|Kho bạc"
    ),
    "notify_no": re.compile(r"(?<![A-Z0-9])(?:IB|DC|B|DN|PA|TB)\d{2}0\d{7}(?![0-9])"),
    "tax_id_10": re.compile(r"(?<!\d)\d{10}(?!\d)"),
    "plan_no": re.compile(r"(?<![A-Z0-9])PL\d{2}0\d{7}(?![0-9])"),
    "contract_code": re.compile(r"(?<![A-Z0-9])HD\d{2}0\d{7}(?![0-9])"),
    "source_host": re.compile(r"muasamcong\.mof\.gov\.vn"),
    "tunnel_host": re.compile(r"instabox\.tech"),
    "money_ge_9_digits": re.compile(r"(?<![\d.])\d{9,}(?![\d.])"),
}

SCAN_SUFFIXES = (".json", ".md", ".txt", ".yaml", ".yml", ".py", ".html", ".csv", ".htm")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--top", type=int, default=30)
    args = parser.parse_args(argv)

    listing = (
        subprocess.run(
            [
                "git",
                "-C",
                str(args.repo),
                "ls-tree",
                "-r",
                "--name-only",
                args.sha,
                "--",
                args.prefix,
            ],
            capture_output=True,
            check=True,
        )
        .stdout.decode("utf-8", "replace")
        .splitlines()
    )

    rows = []
    for rel in listing:
        if not rel.lower().endswith(SCAN_SUFFIXES):
            continue
        blob = subprocess.run(
            ["git", "-C", str(args.repo), "show", f"{args.sha}:{rel}"],
            capture_output=True,
            check=False,
        )
        if blob.returncode != 0:
            continue
        text = blob.stdout.decode("utf-8", "replace")
        counts = {name: len(pattern.findall(text)) for name, pattern in MARKERS.items()}
        identity_total = (
            counts["vn_org_name"]
            + counts["notify_no"]
            + counts["tax_id_10"]
            + counts["plan_no"]
            + counts["contract_code"]
        )
        if identity_total or counts["tunnel_host"]:
            rows.append(
                {
                    "path": rel.removeprefix(args.prefix + "/"),
                    "bytes": len(blob.stdout),
                    "markers": counts,
                    "identity_markers": identity_total,
                }
            )

    rows.sort(key=lambda row: -row["identity_markers"])
    payload = {
        "sha": args.sha,
        "prefix": args.prefix,
        "files_scanned": len(listing),
        "files_with_identity_markers": len(rows),
        "files": rows,
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"scanned={len(listing)} files_with_identity_markers={len(rows)}")
    for row in rows[: args.top]:
        print(f"{row['identity_markers']:5d}  {row['path']}  {row['markers']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
